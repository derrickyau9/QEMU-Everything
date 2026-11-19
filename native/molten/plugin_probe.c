/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <qemu-plugin.h>
#include <stdio.h>
#include <string.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;
static struct qemu_plugin_register *x0;
static bool completed;

static bool equal_bytes(GByteArray *left, GByteArray *right)
{
    return left->len == right->len && left->len && !memcmp(left->data, right->data, left->len);
}

static void probe(unsigned int cpu, void *userdata)
{
    if (completed || !x0) { return; }
    completed = true;
    GByteArray *saved = g_byte_array_new();
    GByteArray *check = g_byte_array_new();
    bool rr = qemu_plugin_read_register(x0, saved) == 8;
    bool rw = rr && qemu_plugin_write_register(x0, saved) == 8;
    rw = rw && qemu_plugin_read_register(x0, check) == 8 && equal_bytes(saved, check);
    /* Reserved scratch RAM, outside fixture load segments; restore it too. */
    const uint64_t scratch = 0x47fff000;
    GByteArray *memory = g_byte_array_new();
    GByteArray *pattern = g_byte_array_new();
    const guint8 bytes[] = {0x37, 0x91, 0x5a, 0xe6};
    g_byte_array_append(pattern, bytes, sizeof(bytes));
    bool vr = qemu_plugin_read_memory_vaddr(scratch, memory, 4);
    bool vw = vr && qemu_plugin_write_memory_vaddr(scratch, pattern);
    bool verify = qemu_plugin_read_memory_vaddr(scratch, check, 4);
    vw = vw && verify && equal_bytes(check, pattern);
    bool virtual_restore = vr && qemu_plugin_write_memory_vaddr(scratch, memory);
    virtual_restore = virtual_restore && qemu_plugin_read_memory_vaddr(scratch, check, 4) && equal_bytes(check, memory);
    uint64_t physical = 0;
    bool translated = qemu_plugin_translate_vaddr(scratch, &physical) && physical == scratch;
    int hr = qemu_plugin_read_memory_hwaddr(scratch, memory, 4);
    int hw = qemu_plugin_write_memory_hwaddr(scratch, pattern);
    int hv = qemu_plugin_read_memory_hwaddr(scratch, check, 4);
    bool physical_verified = hr == QEMU_PLUGIN_HWADDR_OPERATION_OK && hw == QEMU_PLUGIN_HWADDR_OPERATION_OK
        && hv == QEMU_PLUGIN_HWADDR_OPERATION_OK && equal_bytes(check, pattern);
    bool physical_restore = qemu_plugin_write_memory_hwaddr(scratch, memory) == QEMU_PLUGIN_HWADDR_OPERATION_OK;
    physical_restore = physical_restore && qemu_plugin_read_memory_hwaddr(scratch, check, 4) == QEMU_PLUGIN_HWADDR_OPERATION_OK
        && equal_bytes(check, memory);
    fprintf(stderr, "MOLTEN_PROBE {\"api\":%d,\"register_read\":%s,\"register_write_same_value\":%s,"
        "\"virtual_read\":%s,\"virtual_write\":%s,\"translate\":%s,"
        "\"physical_read_result\":%d,\"physical_write_result\":%d,\"physical_verify_result\":%d,"
        "\"physical_verified\":%s,\"virtual_restore_verified\":%s,\"physical_restore_verified\":%s,"
        "\"direct_pc_api\":false}\n",
        QEMU_PLUGIN_VERSION, rr ? "true" : "false", rw ? "true" : "false",
        vr ? "true" : "false", vw ? "true" : "false", translated ? "true" : "false",
        hr, hw, hv, physical_verified ? "true" : "false", virtual_restore ? "true" : "false",
        physical_restore ? "true" : "false");
    g_byte_array_unref(saved); g_byte_array_unref(check);
    g_byte_array_unref(memory); g_byte_array_unref(pattern);
}

static void init(qemu_plugin_id_t id, unsigned int cpu)
{
    GArray *registers = qemu_plugin_get_registers();
    for (guint i = 0; i < registers->len; i++) {
        qemu_plugin_reg_descriptor *r = &g_array_index(registers, qemu_plugin_reg_descriptor, i);
        if (!strcmp(r->name, "x0")) { x0 = r->handle; }
    }
    g_array_free(registers, true);
}

static void translate(qemu_plugin_id_t id, struct qemu_plugin_tb *tb)
{
    for (size_t i = 0; i < qemu_plugin_tb_n_insns(tb); i++) {
        struct qemu_plugin_insn *instruction = qemu_plugin_tb_get_insn(tb, i);
        uint64_t pc = qemu_plugin_insn_vaddr(instruction);
        if (pc >= 0x40000000 && pc < 0x47f00000) {
            qemu_plugin_register_vcpu_insn_exec_cb(instruction, probe, QEMU_PLUGIN_CB_RW_REGS, NULL);
        }
    }
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id, const qemu_info_t *info,
                                          int argc, char **argv)
{
    if (strcmp(info->target_name, "aarch64") || !info->system_emulation || info->version.cur != 5) {
        return -1;
    }
    qemu_plugin_register_vcpu_init_cb(id, init);
    qemu_plugin_register_vcpu_tb_trans_cb(id, translate);
    return 0;
}
