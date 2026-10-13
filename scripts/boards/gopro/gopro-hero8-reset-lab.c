/* SPDX-License-Identifier: GPL-2.0-or-later
 * Minimal independent HERO8 reset diagnostic. Camera peripherals absent.
 * Model facts: original Cortex-A7 reset, actual Linux DT GIC address,
 * original RTOS MMU identity map and three-core rendezvous code.
 * 2x1GiB RAM layout is a diagnostic assumption, not full DDR wiring.
 */
#include "qemu/osdep.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "hw/boards.h"
#include "hw/core/cpu.h"
#include "hw/cpu/a15mpcore.h"
#include "hw/qdev-properties.h"
#include "hw/sysbus.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/units.h"
#include "system/address-spaces.h"
#include "target/arm/cpu.h"
static void gopro_reset_init(MachineState *machine)
{
    ARMCPU *cpus[3];
    MemoryRegion *bank0 = g_new0(MemoryRegion, 1);
    MemoryRegion *bank1 = g_new0(MemoryRegion, 1);
    MemoryRegion *scratch = g_new0(MemoryRegion, 1);
    DeviceState *gic;
    SysBusDevice *bus;
    int i;
    if (machine->ram_size != 2 * GiB) {
        error_report("HERO8 reset diagnostic requires -m 2048");
        exit(1);
    }
    for (i = 0; i < 3; i++) {
        cpus[i] = ARM_CPU(object_new(machine->cpu_type));
        object_property_set_int(OBJECT(cpus[i]), "mp-affinity", i, &error_fatal);
        object_property_set_int(OBJECT(cpus[i]), "reset-cbar", 0x1d000000, &error_fatal);
        object_property_set_int(OBJECT(cpus[i]), "cntfrq", 40000000, &error_fatal);
        object_property_set_bool(OBJECT(cpus[i]), "start-powered-off", false, &error_fatal);
        object_property_set_link(OBJECT(cpus[i]), "memory", OBJECT(get_system_memory()), &error_fatal);
        qdev_realize(DEVICE(cpus[i]), NULL, &error_fatal);
    }
    memory_region_init_alias(bank0, NULL, "hero8-reset.diagnostic-bank0", machine->ram, 0, GiB);
    memory_region_init_alias(bank1, NULL, "hero8-reset.diagnostic-bank1", machine->ram, GiB, GiB);
    memory_region_add_subregion(get_system_memory(), 0x40000000, bank0);
    memory_region_add_subregion(get_system_memory(), 0xa0000000, bank1);
    memory_region_init_ram(scratch, NULL, "hero8-reset.boot-scratch", 64 * KiB, &error_fatal);
    memory_region_add_subregion(get_system_memory(), 0x01000000, scratch);
    gic = qdev_new(TYPE_A15MPCORE_PRIV);
    qdev_prop_set_uint32(gic, "num-cpu", 3);
    /* A15 private-peripheral adapter supports at most 256 IRQs. This
     * captures reset only; higher SC2000A interrupts remain unimplemented. */
    qdev_prop_set_uint32(gic, "num-irq", 256);
    bus = SYS_BUS_DEVICE(gic);
    sysbus_realize_and_unref(bus, &error_fatal);
    sysbus_mmio_map(bus, 0, 0x1d000000);
    for (i = 0; i < 3; i++) {
        sysbus_connect_irq(bus, i, qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_IRQ));
        sysbus_connect_irq(bus, i + 3, qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_FIQ));
        sysbus_connect_irq(bus, i + 6, qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_VIRQ));
        sysbus_connect_irq(bus, i + 9, qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_VFIQ));
    }
}
static void gopro_reset_class(MachineClass *mc)
{
    mc->desc = "Independent original HERO8 reset diagnostic; no camera peripherals";
    mc->init = gopro_reset_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a7");
    mc->default_ram_size = 2 * GiB;
    mc->default_ram_id = "hero8-reset.ram";
    mc->default_cpus = mc->min_cpus = mc->max_cpus = 3;
    mc->ignore_memory_transaction_failures = false;
}
DEFINE_MACHINE_ARM("gopro-hero8-reset-lab", gopro_reset_class)
