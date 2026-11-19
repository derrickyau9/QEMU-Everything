/* SPDX-License-Identifier: GPL-2.0-or-later
 * Included after pinned virt.c. Research transport, not a Sony board model.
 * CPU execution and every pixel store remain in QEMU. Unknown MMIO reads
 * synchronously wait BEFORE the load commits. No host-provided pixel API.
 */
#include "qobject/qdict.h"
#include "qobject/qjson.h"
#include "system/runstate.h"
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define MOLTEN_IO_BASE 0x0b000000ULL
#define MOLTEN_SURFACE_BASE 0x0b100000ULL
#define MOLTEN_PIXELS 0x10000
#define MOLTEN_WIDTH 80
#define MOLTEN_HEIGHT 48

typedef struct MoltenState {
    VirtMachineState parent;
    MemoryRegion io, surface;
    int bridge;
    bool stopped;
    uint64_t seq;
    uint32_t pixels[MOLTEN_WIDTH * MOLTEN_HEIGHT];
    uint64_t writers[64];
    unsigned n_writers, pixel_writes;
    bool model, constant, latched;
    uint64_t write_address, read_address;
    uint32_t latch, mask, xor_value, value;
} MoltenState;

static void molten_send_all(int fd, const char *buffer, size_t size)
{
    while (size) {
        ssize_t n = send(fd, buffer, size, MSG_NOSIGNAL);
        if (n <= 0) {
            if (errno == EINTR) { continue; }
            error_report("MOLTEN bridge disconnected");
            exit(1);
        }
        buffer += n;
        size -= n;
    }
}

static uint64_t molten_exchange(MoltenState *s, const char *kind,
                                uint64_t address, unsigned size,
                                const char *extra)
{
    if (s->stopped) { return 0; }
    CPUARMState *env = &ARM_CPU(current_cpu)->env;
    GString *message = g_string_new(NULL);
    g_string_append_printf(message,
        "{\"kind\":\"%s\",\"seq\":%" PRIu64 ",\"virtual_time\":%" PRId64
        ",\"pc\":\"0x%" PRIx64 "\",\"address\":\"0x%" PRIx64
        "\",\"width_bits\":%u,\"registers\":[", kind, s->seq++,
        qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), env->pc, address, size * 8);
    for (int i = 0; i < 32; i++) {
        g_string_append_printf(message, "%s\"0x%" PRIx64 "\"", i ? "," : "", env->xregs[i]);
    }
    g_string_append_printf(message, "]%s}\n", extra ? extra : "");
    molten_send_all(s->bridge, message->str, message->len);
    g_string_free(message, true);
    char reply[128];
    size_t used = 0;
    while (used + 1 < sizeof(reply)) {
        ssize_t n = read(s->bridge, reply + used, 1);
        if (n < 0 && errno == EINTR) { continue; }
        if (n <= 0) { error_report("MOLTEN incomplete reply"); exit(1); }
        if (reply[used++] == '\n') { break; }
    }
    reply[used] = 0;
    if (!strcmp(reply, "stop\n")) {
        s->stopped = true;
        qemu_system_shutdown_request(SHUTDOWN_CAUSE_HOST_QMP_QUIT);
        return 0;
    }
    char *end;
    uint64_t result = g_ascii_strtoull(reply, &end, 0);
    if (*end != '\n') { error_report("MOLTEN invalid typed reply"); exit(1); }
    return result;
}

static uint64_t molten_io_read(void *opaque, hwaddr offset, unsigned size)
{
    MoltenState *s = opaque;
    uint32_t result = 0;
    if (s->model) {
        if (offset + MOLTEN_IO_BASE != s->read_address || (!s->constant && !s->latched)) {
            error_report("MOLTEN model has no rule for this read/state"); exit(1);
        }
        result = s->constant ? s->value : ((s->latch ^ s->xor_value) & s->mask);
        char extra[100];
        snprintf(extra, sizeof(extra), ",\"value\":%u,\"native_model\":true", result);
        molten_exchange(s, "mmio_read", offset + MOLTEN_IO_BASE, size, extra);
        return result;
    }
    return molten_exchange(s, "mmio_read_unresolved", offset + MOLTEN_IO_BASE, size, NULL);
}

static void molten_io_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    MoltenState *s = opaque;
    if (s->model && offset + MOLTEN_IO_BASE != s->write_address) {
        error_report("MOLTEN model has no rule for this write"); exit(1);
    }
    s->latch = value;
    s->latched = true;
    char extra[100];
    snprintf(extra, sizeof(extra), ",\"value\":%u", (uint32_t)value);
    molten_exchange(s, "mmio_write", offset + MOLTEN_IO_BASE, size, extra);
}

static uint64_t molten_surface_read(void *opaque, hwaddr offset, unsigned size)
{
    MoltenState *s = opaque;
    if (offset != 0) { error_report("MOLTEN unsupported surface read"); exit(1); }
    return molten_exchange(s, "input_poll", MOLTEN_SURFACE_BASE, size, NULL);
}

static void molten_surface_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    MoltenState *s = opaque;
    if (offset >= MOLTEN_PIXELS && offset < MOLTEN_PIXELS + sizeof(s->pixels)) {
        s->pixels[(offset - MOLTEN_PIXELS) / 4] = value;
        s->pixel_writes++;
        uint64_t pc = ARM_CPU(current_cpu)->env.pc;
        unsigned i;
        for (i = 0; i < s->n_writers; i++) { if (s->writers[i] == pc) { break; } }
        if (i == s->n_writers) {
            if (i == ARRAY_SIZE(s->writers)) { error_report("MOLTEN writer limit"); exit(1); }
            s->writers[s->n_writers++] = pc;
        }
        return;
    }
    if (offset != 8) { error_report("MOLTEN unsupported surface write"); exit(1); }
    g_autofree char *pixels = g_base64_encode((const guchar *)s->pixels, sizeof(s->pixels));
    GString *extra = g_string_new(NULL);
    g_string_append_printf(extra, ",\"width\":%d,\"height\":%d,\"pixel_writes\":%u,"
        "\"bgra\":\"%s\",\"writers\":[", MOLTEN_WIDTH, MOLTEN_HEIGHT, s->pixel_writes, pixels);
    for (unsigned i = 0; i < s->n_writers; i++) {
        g_string_append_printf(extra, "%s\"0x%" PRIx64 "\"", i ? "," : "", s->writers[i]);
    }
    g_string_append(extra, "]");
    molten_exchange(s, "frame", MOLTEN_SURFACE_BASE + 8, size, extra->str);
    g_string_free(extra, true);
    s->n_writers = s->pixel_writes = 0;
}

static const MemoryRegionOps molten_io_ops = {
    .read = molten_io_read, .write = molten_io_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static const MemoryRegionOps molten_surface_ops = {
    .read = molten_surface_read, .write = molten_surface_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static uint64_t molten_model_int(QDict *dict, const char *key)
{
    const char *text = qdict_get_str(dict, key);
    char *end;
    uint64_t value = g_ascii_strtoull(text, &end, 0);
    if (*end) { error_report("MOLTEN invalid model integer"); exit(1); }
    return value;
}

static void molten_machine_init(MachineState *machine)
{
    MoltenState *s = (MoltenState *)machine;
    machvirt_init(machine);
    /* The generic loader may place stripped code anywhere in admitted RAM.
     * virt normally puts its generated DTB at RAM base, silently reserving
     * the first MiB. Keep that DTB in an explicit top-of-RAM reservation,
     * outside the ELF loader envelope and above the downward-growing stack.
     * arm_load_kernel() has already set dtb_start; virt_machine_done() will
     * later consume these bounds. No Sony machine is changed by this rule.
     */
    s->parent.bootinfo.dtb_start = 0x47f00000;
    s->parent.bootinfo.dtb_limit = 0x48000000;
    const char *socket_path = g_getenv("MOLTEN_SOCKET");
    if (!socket_path) { error_report("MOLTEN_SOCKET is required"); exit(1); }
    struct sockaddr_un address = { .sun_family = AF_UNIX };
    if (strlen(socket_path) >= sizeof(address.sun_path)) { error_report("MOLTEN socket path too long"); exit(1); }
    strcpy(address.sun_path, socket_path);
    s->bridge = socket(AF_UNIX, SOCK_STREAM, 0);
    if (s->bridge < 0 || connect(s->bridge, (struct sockaddr *)&address, sizeof(address))) {
        error_report("MOLTEN bridge connect: %s", strerror(errno)); exit(1);
    }
    const char *model_path = g_getenv("MOLTEN_MODEL");
    if (model_path) {
        g_autofree char *text = NULL;
        if (!g_file_get_contents(model_path, &text, NULL, NULL)) { error_report("MOLTEN cannot read model"); exit(1); }
        QObject *root = qobject_from_json(text, &error_fatal);
        QDict *model = qobject_to(QDict, root);
        const char *kind = qdict_get_str(model, "kind");
        s->constant = !strcmp(kind, "constant");
        if (!s->constant && strcmp(kind, "xor_latch")) { error_report("MOLTEN unsupported model AST"); exit(1); }
        s->write_address = molten_model_int(model, "write_address");
        s->read_address = molten_model_int(model, "read_address");
        s->mask = molten_model_int(model, "mask");
        s->xor_value = molten_model_int(model, "xor_value");
        s->value = molten_model_int(model, "value");
        s->model = true;
        qobject_unref(root);
    }
    memory_region_init_io(&s->io, NULL, &molten_io_ops, s, "molten-unknown", 0x100000);
    memory_region_add_subregion(get_system_memory(), MOLTEN_IO_BASE, &s->io);
    memory_region_init_io(&s->surface, NULL, &molten_surface_ops, s, "molten-surface", 0x100000);
    memory_region_add_subregion(get_system_memory(), MOLTEN_SURFACE_BASE, &s->surface);
}

static void molten_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    arm_virt_compat_set(mc);
    virt_machine_10_2_options(mc);
    mc->desc = "MOLTEN single-core AArch64 synchronous research boundary";
    mc->init = molten_machine_init;
    mc->max_cpus = 1;
}
static const TypeInfo molten_type = {
    .name = MACHINE_TYPE_NAME("molten-aarch64"), .parent = TYPE_VIRT_MACHINE,
    .instance_size = sizeof(MoltenState), .class_init = molten_class_init,
    .interfaces = arm_aarch64_machine_interfaces,
};
static void molten_register(void) { type_register_static(&molten_type); }
type_init(molten_register);
