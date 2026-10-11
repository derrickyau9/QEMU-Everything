/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Copyright 2026 Derrick Yao.
 * Experimental Ricoh service-core board. This is not a complete camera SoC.
 * Register layout follows Socionext's GPL serial usio.c, published in Ricoh's
 * GRIIIx_121 OSS package. No vendor kernel or firmware code is included.
 * RAM/GIC/USIO addresses come from the user-supplied sc2000.dtb.
 */
#include "qemu/osdep.h"
#include "qemu/units.h"
#include "qapi/error.h"
#include "hw/boards.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "hw/cpu/a15mpcore.h"
#include "hw/qdev-properties.h"
#include "hw/sysbus.h"
#include "hw/irq.h"
#include "chardev/char-fe.h"
#include "system/system.h"
#include "system/address-spaces.h"
#include "target/arm/cpu.h"

typedef struct USIOLab {
    MemoryRegion memory;
    CharFrontend chr;
    uint8_t regs[16], rx[128];
    unsigned head, used;
    qemu_irq rx_irq, tx_irq;
} USIOLab;

static void usio_irq(USIOLab *s)
{
    qemu_set_irq(s->rx_irq, s->used && (s->regs[1] & 0x10));
    qemu_set_irq(s->tx_irq, (s->regs[1] & 4) && (s->regs[13] & 2));
}

static uint64_t usio_read(void *opaque, hwaddr addr, unsigned size)
{
    USIOLab *s = opaque;
    unsigned value = 0, i;
    if (addr == 3) { return 3 | (s->used ? 4 : 0); }
    if (addr == 4) {
        if (s->used) {
            value = s->rx[s->head++ % 128];
            s->used--;
            usio_irq(s);
            qemu_chr_fe_accept_input(&s->chr);
        }
        return value;
    }
    if (addr == 12) { return s->regs[12] | (s->regs[13] << 8) | 0x400; }
    if (addr == 14) { return s->used << 8; }
    for (i = 0; i < size && addr + i < 16; i++) {
        value |= s->regs[addr + i] << (8 * i);
    }
    return value;
}

static void usio_write(void *opaque, hwaddr addr, uint64_t value, unsigned size)
{
    USIOLab *s = opaque;
    unsigned i;
    if (addr == 4) {
        uint8_t c = value;
        qemu_chr_fe_write_all(&s->chr, &c, 1);
        return;
    }
    for (i = 0; i < size && addr + i < 16; i++) {
        s->regs[addr + i] = value >> (8 * i);
    }
    usio_irq(s);
}

static int usio_can_read(void *opaque)
{
    USIOLab *s = opaque;
    return 128 - s->used;
}

static void usio_receive(void *opaque, const uint8_t *bytes, int size)
{
    USIOLab *s = opaque;
    int i;
    for (i = 0; i < size && s->used < 128; i++) {
        s->rx[(s->head + s->used++) % 128] = bytes[i];
    }
    usio_irq(s);
}

static const MemoryRegionOps usio_ops = {
    .read = usio_read, .write = usio_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 4,
    .impl.min_access_size = 1, .impl.max_access_size = 4,
};

static void ricoh_bootloader_interrupt_state(ARMCPU *cpu,
                                             const struct arm_boot_info *info)
{
    /* The vendor GIC driver preserves bit 0 and enables bit 1. Its camera
     * bootloader has already enabled the primary CPU interface. Model that
     * explicit bootloader state, rather than changing the original kernel.
     * No secure RTOS is present in this one-core development machine. */
    static const ARMInsnFixup setup[] = {
        { 0xe59f0008 }, /* ldr r0, [pc, #8] */
        { 0xe3a01001 }, /* mov r1, #1 */
        { 0xe5801000 }, /* str r1, [r0] */
        { 0xe12fff1e }, /* bx lr */
        { 0x1d002000 },
        { 0, FIXUP_TERMINATOR },
    };
    arm_write_bootloader("ricoh-lab.gic-setup", arm_boot_address_space(cpu, info),
                         info->board_setup_addr, setup, NULL);
}

static void ricoh_init(MachineState *machine)
{
    ARMCPU *cpu;
    DeviceState *gic;
    SysBusDevice *gic_bus;
    USIOLab *uart = g_new0(USIOLab, 1);
    MemoryRegion *shmem = g_new0(MemoryRegion, 1);
    struct arm_boot_info *boot = g_new0(struct arm_boot_info, 1);

    cpu = ARM_CPU(object_new(machine->cpu_type));
    object_property_set_bool(OBJECT(cpu), "has_el2", false, &error_fatal);
    object_property_set_bool(OBJECT(cpu), "has_el3", false, &error_fatal);
    object_property_set_int(OBJECT(cpu), "reset-cbar", 0x1d000000, &error_fatal);
    object_property_set_int(OBJECT(cpu), "cntfrq", 40000000, &error_fatal);
    qdev_realize(DEVICE(cpu), NULL, &error_fatal);
    memory_region_add_subregion(get_system_memory(), 0x40000000, machine->ram);
    /* Vendor libshmem_manager maps the RTOS communication payload separately
     * at 0x50000000. This zeroed RAM is only a transport backing store; there
     * are no synthesized camera replies or RTOS execution here. */
    memory_region_init_ram(shmem, NULL, "ricoh-lab.rtos-shmem", 8 * MiB,
                           &error_fatal);
    memory_region_add_subregion(get_system_memory(), 0x50000000, shmem);

    gic = qdev_new(TYPE_A15MPCORE_PRIV);
    qdev_prop_set_uint32(gic, "num-cpu", 1);
    qdev_prop_set_uint32(gic, "num-irq", 256);
    gic_bus = SYS_BUS_DEVICE(gic);
    sysbus_realize_and_unref(gic_bus, &error_fatal);
    sysbus_mmio_map(gic_bus, 0, 0x1d000000);
    sysbus_connect_irq(gic_bus, 0, qdev_get_gpio_in(DEVICE(cpu), ARM_CPU_IRQ));
    sysbus_connect_irq(gic_bus, 1, qdev_get_gpio_in(DEVICE(cpu), ARM_CPU_FIQ));
    sysbus_connect_irq(gic_bus, 2, qdev_get_gpio_in(DEVICE(cpu), ARM_CPU_VIRQ));
    sysbus_connect_irq(gic_bus, 3, qdev_get_gpio_in(DEVICE(cpu), ARM_CPU_VFIQ));
    uart->rx_irq = qdev_get_gpio_in(gic, 0x8d);
    uart->tx_irq = qdev_get_gpio_in(gic, 0x95);
    qemu_chr_fe_init(&uart->chr, serial_hd(0), &error_fatal);
    qemu_chr_fe_set_handlers(&uart->chr, usio_can_read, usio_receive,
                             NULL, NULL, uart, NULL, true);
    memory_region_init_io(&uart->memory, NULL, &usio_ops, uart,
                           "ricoh-lab.usio1", 16);
    memory_region_add_subregion(get_system_memory(), 0x1e700010, &uart->memory);

    boot->loader_start = 0x40000000;
    boot->ram_size = machine->ram_size;
    boot->board_id = -1;
    boot->board_setup_addr = 0x40001000;
    boot->write_board_setup = ricoh_bootloader_interrupt_state;
    arm_load_kernel(cpu, machine, boot);
}

static void ricoh_class_init(MachineClass *mc)
{
    static const char * const cpus[] = { ARM_CPU_TYPE_NAME("cortex-a7"), NULL };
    mc->desc = "Experimental Ricoh Milbeaut Linux service core (UART only)";
    mc->init = ricoh_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a7");
    mc->valid_cpu_types = cpus;
    mc->default_ram_size = 256 * MiB;
    mc->default_ram_id = "ricoh-lab.ram";
    mc->max_cpus = 1;
    mc->ignore_memory_transaction_failures = false;
}
DEFINE_MACHINE_ARM("ricoh-milbeaut-lab", ricoh_class_init)
