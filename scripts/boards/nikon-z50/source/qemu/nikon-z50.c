/* Independently adapted from WonderLantern/z30-qemu, commit
 * 0ba0c3547ccbec09e7c861eaca35db8e82795f15 (itself ported from Z7).
 * Historical PCs/transfer comments below describe upstream observations,
 * not Z50 evidence unless explicitly marked Z50 C2.60. See ../../README.md. */
/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Experimental Z50 C2.60 board with explicitly selected assisted startup. */
#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "qapi/error.h"
#include "hw/boards.h"
#include "hw/arm/machines-qom.h"
#include "hw/loader.h"
#include "hw/cpu/a15mpcore.h"
#include "hw/qdev-properties.h"
#include "hw/irq.h"
#include "chardev/char-fe.h"
#include "qemu/timer.h"
#include "system/runstate.h"
#include "target/arm/cpu.h"

#define Z50_BASE 0x10020000
#define Z50_SIZE 31404090
#define Z50_HASH "57e834fac8f921c18c99abe0b1469edbaca71cb1957794bb9f138bd223d30fc1"

typedef struct Z50State {
    MemoryRegion rom, ram, aux, scratch, unknown, uart, io[3];
    CharFrontend output;
    uint8_t uart_regs[16];
    GHashTable *registers;
    bool assisted, pending;
    unsigned status_reads, unknown_count;
    uint32_t platform_saved[512];
    bool platform_ready;
    QEMUTimer *platform_timer;
} Z50State;

typedef struct Z50Window {
    Z50State *state;
    uint32_t base;
} Z50Window;

static void z50_unknown(Z50State *s, hwaddr address, unsigned size, bool write);

static uint32_t z50_register(Z50State *s, uint32_t address)
{
    return GPOINTER_TO_UINT(g_hash_table_lookup(s->registers,
                                              GUINT_TO_POINTER(address)));
}

static void z50_platform_complete(void *opaque)
{
    Z50State *s = opaque;
    s->platform_ready = true;
    fprintf(stderr, "Z50_PLATFORM complete ready=1\n");
}

/* Generated from the stock reset-prefix observations, not Z7 constants. */
static const uint32_t ready_addresses[] = {
    0x20022300, 0x20023300, 0x20023318, 0x20024300,
    0x20028030, 0x20028830, 0x20028c30, 0x20029030,
    0x20029830, 0x20029c30, 0x2002a030, 0x2002a830,
    0x2002ac30, 0x2002b030, 0x2002b830, 0x2002bc30,
    0x2002c030, 0x2002c830, 0x2002cc30, 0x2002e300,
    0x20042300, 0x20043300, 0x20043318, 0x20044300,
    0x20048030, 0x20048830, 0x20048c30, 0x20049030,
    0x20049830, 0x20049c30, 0x2004a030, 0x2004a830,
    0x2004ac30, 0x2004b030, 0x2004b830, 0x2004bc30,
    0x2004c030, 0x2004c830, 0x2004cc30, 0x2004e300,
};

static uint64_t z50_read(void *opaque, hwaddr offset, unsigned size)
{
    Z50Window *w = opaque;
    Z50State *s = w->state;
    uint32_t address = w->base + offset;
    if (s->assisted) {
        /* Stock 10059e84/10059ede wait for bit0. 10059edc submits at +34.
         * Initial ready and 1us completion are explicit functional hypotheses. */
        if (address == 0x20010038) {
            return (z50_register(s, address) & ~1U) | s->platform_ready;
        }
        if (address >= 0x20014800 && address < 0x20015000) {
            if (current_cpu && z50_register(s, 0x20010044) != 1) {
                z50_unknown(s, address, size, false);
                return 0;
            }
            return s->platform_saved[(address - 0x20014800) / 4];
        }
        if (address >= 0x24074000 && address < 0x24074800) {
            return s->platform_saved[(address - 0x24074000) / 4];
        }
        for (unsigned i = 0; i < ARRAY_SIZE(ready_addresses); i++) {
            if (address == ready_addresses[i]) {
                return 1;
            }
        }
        /* Phase13: done only. DMA/clear engine itself remains unimplemented. */
        if (address == 0x20070008) {
            return 1;
        }
        /* Experimental 48 MHz clock, virtual-time based (not per-read steps). */
        if (address == 0x04266008) {
            return (uint32_t)((qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / 1000) * 48);
        }
        /* Phase16: 0xa1 submission, completion bit0, W1C acknowledge. */
        if (address == 0x04287004) {
            return s->pending && ++s->status_reads >= 2 ? 1 : 0;
        }
    }
    /* Explicit zero-initialized register bank, no invented success bits. */
    return GPOINTER_TO_UINT(g_hash_table_lookup(s->registers,
                                              GUINT_TO_POINTER(address)));
}

static void z50_write(void *opaque, hwaddr offset, uint64_t value,
                      unsigned size)
{
    Z50Window *w = opaque;
    Z50State *s = w->state;
    uint32_t address = w->base + offset;
    g_hash_table_insert(s->registers, GUINT_TO_POINTER(address),
                        GUINT_TO_POINTER((uint32_t)value));
    if (s->assisted && address == 0x20010034 && value == 1) {
        s->platform_ready = false;
        timer_mod(s->platform_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 1000);
        fprintf(stderr, "Z50_PLATFORM submit ready=0 latency_ns=1000\n");
    }
    if (s->assisted && address >= 0x24074000 && address < 0x24074800) {
        if (z50_register(s, 0x20010060) != 1) {
            z50_unknown(s, address, size, true);
            return;
        }
        s->platform_saved[(address - 0x24074000) / 4] = value;
    }
    if (s->assisted && address == 0x04287010 && value == 0xa1) {
        s->pending = true;
        s->status_reads = 0;
        fprintf(stderr, "Z50_COMMAND submit selector=0x%x\n",
                GPOINTER_TO_UINT(g_hash_table_lookup(s->registers,
                                               GUINT_TO_POINTER(0x04287000))));
    }
    if (s->assisted && address == 0x04287004 && (value & 1)) {
        s->pending = false;
        fprintf(stderr, "Z50_COMMAND acknowledge\n");
    }
}

static const MemoryRegionOps z50_io_ops = {
    .read = z50_read, .write = z50_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4, .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void z50_unknown(Z50State *s, hwaddr address, unsigned size, bool write)
{
    if (s->unknown_count++ < 16) {
        fprintf(stderr, "Z50_UNMODELED %s address=0x%" HWADDR_PRIx
                " size=%u cpu=%d pc=0x%08x\n", write ? "write" : "read", address,
                size, current_cpu ? current_cpu->cpu_index : -1,
                current_cpu ? ARM_CPU(current_cpu)->env.regs[15] : 0);
    }
    /* Pause for diagnostics; unknown memory is never silently added as RAM. */
    if (current_cpu) {
        vm_stop(RUN_STATE_PAUSED);
        cpu_exit(current_cpu);
    }
}

static uint64_t z50_unknown_read(void *opaque, hwaddr address, unsigned size)
{
    z50_unknown(opaque, address, size, false);
    return 0;
}

static void z50_unknown_write(void *opaque, hwaddr address, uint64_t value,
                              unsigned size)
{
    z50_unknown(opaque, address, size, true);
}

static const MemoryRegionOps z50_unknown_ops = {
    .read = z50_unknown_read, .write = z50_unknown_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8 },
    .impl = { .min_access_size = 1, .max_access_size = 8 },
};

/* Stock 10020180 configures this 16-byte channel; 100201ec polls +3 bit1
 * and writes a halfword to +4. Host sink behavior follows WonderLantern.
 * Only write/readback and immediate transmit are modeled, not RX or IRQ. */
static uint64_t z50_uart_read(void *opaque, hwaddr offset, unsigned size)
{
    Z50State *s = opaque;
    uint64_t value = 0;
    for (unsigned i = 0; i < size; i++) {
        uint8_t byte = s->uart_regs[offset + i];
        if (s->assisted && offset + i == 3 && (s->uart_regs[1] & 1)) {
            byte |= 2;
        }
        value |= (uint64_t)byte << (8 * i);
    }
    return value;
}

static void z50_uart_write(void *opaque, hwaddr offset, uint64_t value,
                           unsigned size)
{
    Z50State *s = opaque;
    for (unsigned i = 0; i < size; i++) {
        s->uart_regs[offset + i] = value >> (8 * i);
    }
    if (offset == 4 && s->assisted && (s->uart_regs[1] & 1) &&
        qemu_chr_fe_backend_connected(&s->output)) {
        uint8_t byte = value;
        qemu_chr_fe_write_all(&s->output, &byte, 1);
    }
}

static const MemoryRegionOps z50_uart_ops = {
    .read = z50_uart_read, .write = z50_uart_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 2 },
    .impl = { .min_access_size = 1, .max_access_size = 2 },
};

static void z50_init_common(MachineState *machine, bool assisted)
{
    Z50State *s = g_new0(Z50State, 1);
    MemoryRegion *sysmem = get_system_memory();
    s->assisted = assisted;
    s->registers = g_hash_table_new(g_direct_hash, g_direct_equal);
    s->platform_ready = assisted;
    s->platform_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, z50_platform_complete, s);
    for (unsigned n = 0; n < machine->smp.cpus; n++) {
        Object *cpu = object_new(machine->cpu_type);
        object_property_set_int(cpu, "reset-cbar", 0x2c090000, &error_fatal);
        object_property_set_int(cpu, "cntfrq", 48000000, &error_fatal);
        qdev_realize(DEVICE(cpu), NULL, &error_fatal);
    }
    DeviceState *priv = qdev_new(TYPE_A15MPCORE_PRIV);
    SysBusDevice *bus = SYS_BUS_DEVICE(priv);
    qdev_prop_set_uint32(priv, "num-cpu", machine->smp.cpus);
    qdev_prop_set_uint32(priv, "num-irq", 256);
    sysbus_realize_and_unref(bus, &error_fatal);
    sysbus_mmio_map(bus, 0, 0x2c090000);
    for (unsigned n = 0; n < machine->smp.cpus; n++) {
        DeviceState *cpu = DEVICE(qemu_get_cpu(n));
        sysbus_connect_irq(bus, n, qdev_get_gpio_in(cpu, ARM_CPU_IRQ));
        sysbus_connect_irq(bus, n + machine->smp.cpus,
                           qdev_get_gpio_in(cpu, ARM_CPU_FIQ));
        sysbus_connect_irq(bus, n + 2 * machine->smp.cpus,
                           qdev_get_gpio_in(cpu, ARM_CPU_VIRQ));
        sysbus_connect_irq(bus, n + 3 * machine->smp.cpus,
                           qdev_get_gpio_in(cpu, ARM_CPU_VFIQ));
    }
    memory_region_init_io(&s->unknown, OBJECT(machine), &z50_unknown_ops,
                          s, "z50-unmodeled", UINT64_C(0x100000000));
    memory_region_add_subregion_overlap(sysmem, 0, &s->unknown, -100);
    memory_region_init_rom(&s->rom, NULL, "z50-main", 0x2200000, &error_fatal);
    memory_region_add_subregion(sysmem, Z50_BASE, &s->rom);
    /* Stock kernel heap producer: c2b20000..c7a00000 (local task research). */
    memory_region_init_ram(&s->ram, NULL, "z50-main-ram", 0x6200000, &error_fatal);
    memory_region_add_subregion(sysmem, 0xc1800000, &s->ram);
    memory_region_init_ram(&s->aux, NULL, "z50-aux-ram", 0xb00000, &error_fatal);
    memory_region_add_subregion(sysmem, 0xc7a00000, &s->aux);
    memory_region_init_ram(&s->scratch, NULL, "z50-boot-sram", 0x1000, &error_fatal);
    memory_region_add_subregion(sysmem, 0x04008000, &s->scratch);
    qemu_chr_fe_init(&s->output, qemu_chr_find("z50uart"), &error_fatal);
    memory_region_init_io(&s->uart, OBJECT(machine), &z50_uart_ops,
                          s, "z50-debug-serial", 16);
    memory_region_add_subregion_overlap(sysmem, 0x2c090000, &s->uart, 10);
    const uint32_t bases[] = { 0x04000000, 0x20000000, 0x24074000 };
    const uint32_t lengths[] = { 0x400000, 0x1000000, 0x800 };
    for (unsigned i = 0; i < ARRAY_SIZE(bases); i++) {
        Z50Window *w = g_new0(Z50Window, 1);
        w->state = s; w->base = bases[i];
        g_autofree char *name = g_strdup_printf("z50-register-bank-%u", i);
        memory_region_init_io(&s->io[i], OBJECT(machine), &z50_io_ops,
                              w, name, lengths[i]);
        memory_region_add_subregion_overlap(sysmem, bases[i], &s->io[i], -10);
    }
    if (machine->firmware) {
        g_autofree char *data = NULL;
        gsize length;
        g_autoptr(GError) error = NULL;
        if (!g_file_get_contents(machine->firmware, &data, &length, &error)) {
            error_report("Cannot read Z50 main: %s", error->message);
            exit(1);
        }
        g_autofree char *hash = g_compute_checksum_for_data(G_CHECKSUM_SHA256,
                                               (const guchar *)data, length);
        if (length != Z50_SIZE || strcmp(hash, Z50_HASH)) {
            error_report("Expected extracted Z50 C2.60 main: %u bytes, SHA256 %s",
                          Z50_SIZE, Z50_HASH);
            exit(1);
        }
        rom_add_blob_fixed("z50-stock-main", data, length, Z50_BASE);
    }
    fprintf(stderr, "Z50_MODEL assisted=%d cpu=cortex-a15 cbar=0x2c090000 "
            "clock_hypothesis_hz=48000000 boot_rom_missing=1 camera_boot_verified=0\n",
            assisted);
    /* PC selection is supplied by generic loader per CPU, also after reset. */
}

static void z50_init(MachineState *m) { z50_init_common(m, false); }
static void z50_assisted_init(MachineState *m) { z50_init_common(m, true); }
static void z50_class(MachineClass *mc)
{
    mc->desc = "Nikon Z50 C2.60 research (zero peripheral status)";
    mc->init = z50_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a15");
    mc->default_cpus = 2;
    mc->min_cpus = 1;
    mc->max_cpus = 2;
    mc->default_ram_size = 0;
    mc->no_serial = true;
    mc->no_parallel = true;
}
static void z50_assisted_class(MachineClass *mc)
{
    z50_class(mc);
    mc->desc = "Nikon Z50 C2.60 research (explicit synthetic device completion)";
    mc->init = z50_assisted_init;
}
DEFINE_MACHINE_ARM("nikon-z50", z50_class)
DEFINE_MACHINE_ARM("nikon-z50-assisted", z50_assisted_class)
