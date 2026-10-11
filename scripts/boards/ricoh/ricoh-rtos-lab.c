/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Copyright 2026 Derrick Yao.
 * Independent reset-trace board for user-supplied Ricoh RTOS firmware.
 * This models RAM, boot scratch RAM, the GIC and documented time sources.
 * No firmware, camera IPC answers, display state or vendor code is included.
 */
#include "qemu/osdep.h"
#include "qemu/units.h"
#include "qemu/timer.h"
#include "qemu/host-utils.h"
#include "qemu/log.h"
#include "qapi/error.h"
#include "hw/boards.h"
#include "hw/arm/machines-qom.h"
#include "hw/cpu/a15mpcore.h"
#include "hw/qdev-properties.h"
#include "hw/sysbus.h"
#include "hw/irq.h"
#include "system/address-spaces.h"
#include "target/arm/cpu.h"

typedef struct RicohRTOSTimer {
    MemoryRegion memory;
    QEMUTimer *timer;
    qemu_irq irq;
    uint32_t csr, reload, reload2;
    int64_t started;
} RicohRTOSTimer;

/* Register offsets/bits are confirmed against Ricoh's GPL milbeaut-timer.c.
 * Original GR IV reset code selects channel 13, CSL=1, reload=6749 and
 * IRQ 131 (SPI 99). Its elapsed-time conversion implies 6.75 MHz.
 * Timer events reflect actual virtual elapsed time; no fixed ready values. */
#define RTOS_TIMER_HZ 6750000

static void ricoh_rtos_timer_schedule(RicohRTOSTimer *s)
{
    timer_del(s->timer);
    if ((s->csr & 2) && s->reload) {
        s->started = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        timer_mod(s->timer, s->started +
                  ((uint64_t)s->reload + 1) * 1000000000ULL / RTOS_TIMER_HZ);
    }
}

static void ricoh_rtos_timer_expire(void *opaque)
{
    RicohRTOSTimer *s = opaque;
    s->csr |= 4;
    qemu_set_irq(s->irq, (s->csr & 8) != 0);
    if (s->csr & 16) {
        ricoh_rtos_timer_schedule(s);
    } else {
        s->csr &= ~2;
    }
}

static uint64_t ricoh_rtos_timer_read(void *opaque, hwaddr addr, unsigned size)
{
    RicohRTOSTimer *s = opaque;
    uint64_t ticks;
    switch (addr) {
    case 0: return s->csr;
    case 4:
        if (!(s->csr & 2) || !s->reload) { return s->reload; }
        ticks = muldiv64(qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->started,
                        RTOS_TIMER_HZ, 1000000000ULL);
        return s->reload - MIN(ticks, s->reload);
    case 8: return s->reload;
    case 12: return s->reload2;
    default: return 0;
    }
}

static void ricoh_rtos_timer_write(void *opaque, hwaddr addr, uint64_t value,
                                  unsigned size)
{
    RicohRTOSTimer *s = opaque;
    switch (addr) {
    case 0:
        s->csr = value & ~1U;
        qemu_set_irq(s->irq, (s->csr & 12) == 12);
        ricoh_rtos_timer_schedule(s);
        break;
    case 4: case 8: s->reload = value; break;
    case 12: s->reload2 = value; break;
    }
}

static const MemoryRegionOps ricoh_rtos_timer_ops = {
    .read = ricoh_rtos_timer_read,
    .write = ricoh_rtos_timer_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

typedef struct RicohUSB3JTAdjustment {
    MemoryRegion memory;
    uint32_t value;
    bool written;
} RicohUSB3JTAdjustment;

/* Ricoh's GPL sc2000.dtsi maps USB3 glue at 0x1b110280. dwc3-sn.c
 * identifies offset 0x18 as USB3_JT_ADJ and modifies its RW bit 16,
 * USB3_OVRCUR_EN. GR IV writes the full register before reading it.
 * Model only write/read configuration retention, without inventing USB
 * transfers, attach state or a reset value absent from the public source. */
static MemTxResult ricoh_usb_jt_read(void *opaque, hwaddr addr, uint64_t *value,
                                    unsigned size, MemTxAttrs attrs)
{
    RicohUSB3JTAdjustment *s = opaque;
    if (!s->written) {
        qemu_log_mask(LOG_UNIMP, "ricoh-rtos-lab: USB3_JT_ADJ reset value unknown\n");
        return MEMTX_ERROR;
    }
    *value = s->value;
    return MEMTX_OK;
}

static MemTxResult ricoh_usb_jt_write(void *opaque, hwaddr addr, uint64_t value,
                                     unsigned size, MemTxAttrs attrs)
{
    RicohUSB3JTAdjustment *s = opaque;
    s->value = value;
    s->written = true;
    qemu_log_mask(LOG_UNIMP, "ricoh-rtos-lab: USB3_JT_ADJ write %08x (configuration storage only)\n",
                  s->value);
    return MEMTX_OK;
}

static const MemoryRegionOps ricoh_usb_jt_ops = {
    .read_with_attrs = ricoh_usb_jt_read,
    .write_with_attrs = ricoh_usb_jt_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

typedef struct RicohSystemCounter {
    MemoryRegion memory;
    uint32_t control;
    uint64_t ticks;
    int64_t started;
} RicohSystemCounter;

/* The original function at 0x53aac61c reads CTRL bit 1, count low/high
 * offsets 8/c, and frequency at 0x20. These exactly match the ARMv7
 * CNTControlBase register interface (ARM-software/CSAL csregisters.h).
 * Ricoh's GPL sc2000.dtsi identifies the generic timer as always-on at
 * 40 MHz. Start enabled as explicit development bootloader configuration;
 * this is a real elapsed-time counter, not a fixed status/ready answer.
 * Unknown registers and unsupported frequency-control bits remain faults. */
#define RICOH_SYSTEM_COUNTER_HZ 40000000

static uint64_t ricoh_system_counter_ticks(RicohSystemCounter *s)
{
    if (!(s->control & 1)) {
        return s->ticks;
    }
    return s->ticks + muldiv64(qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->started,
                              RICOH_SYSTEM_COUNTER_HZ, 1000000000ULL);
}

static MemTxResult ricoh_system_counter_read(void *opaque, hwaddr addr,
                                            uint64_t *value, unsigned size,
                                            MemTxAttrs attrs)
{
    RicohSystemCounter *s = opaque;
    switch (addr) {
    case 0: *value = s->control; break;
    case 8: *value = (uint32_t)ricoh_system_counter_ticks(s); break;
    case 12: *value = ricoh_system_counter_ticks(s) >> 32; break;
    case 32: *value = RICOH_SYSTEM_COUNTER_HZ; break;
    default: return MEMTX_ERROR;
    }
    return MEMTX_OK;
}

static MemTxResult ricoh_system_counter_write(void *opaque, hwaddr addr,
                                             uint64_t value, unsigned size,
                                             MemTxAttrs attrs)
{
    RicohSystemCounter *s = opaque;
    if (addr == 0 && !(value & ~3U)) {
        s->ticks = ricoh_system_counter_ticks(s);
        s->started = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        s->control = value;
        return MEMTX_OK;
    }
    if (!(s->control & 1) && (addr == 8 || addr == 12)) {
        if (addr == 8) {
            s->ticks = (s->ticks & 0xffffffff00000000ULL) | (uint32_t)value;
        } else {
            s->ticks = ((uint64_t)(uint32_t)value << 32) | (uint32_t)s->ticks;
        }
        return MEMTX_OK;
    }
    return MEMTX_ERROR;
}

static const MemoryRegionOps ricoh_system_counter_ops = {
    .read_with_attrs = ricoh_system_counter_read,
    .write_with_attrs = ricoh_system_counter_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static void ricoh_rtos_init(MachineState *machine)
{
    ARMCPU *cpus[3];
    DeviceState *gic;
    SysBusDevice *gic_bus;
    MemoryRegion *scratch = g_new0(MemoryRegion, 1);
    RicohRTOSTimer *timer = g_new0(RicohRTOSTimer, 1);
    RicohUSB3JTAdjustment *usb_jt = g_new0(RicohUSB3JTAdjustment, 1);
    RicohSystemCounter *counter = g_new0(RicohSystemCounter, 1);
    int i;

    for (i = 0; i < 3; i++) {
        cpus[i] = ARM_CPU(object_new(machine->cpu_type));
        object_property_set_bool(OBJECT(cpus[i]), "has_el2", false, &error_fatal);
        object_property_set_bool(OBJECT(cpus[i]), "has_el3", true, &error_fatal);
        object_property_set_int(OBJECT(cpus[i]), "mp-affinity", i, &error_fatal);
        object_property_set_int(OBJECT(cpus[i]), "reset-cbar", 0x1d000000,
                                &error_fatal);
        object_property_set_int(OBJECT(cpus[i]), "cntfrq", 40000000,
                                &error_fatal);
        qdev_realize(DEVICE(cpus[i]), NULL, &error_fatal);
    }
    memory_region_add_subregion(get_system_memory(), 0x40000000, machine->ram);
    /* Original reset vectors coordinate the three RTOS cores through this
     * SRAM range. It starts zeroed: the original guest publishes its flags.
     * No loader-authored rendezvous values or camera replies are injected. */
    memory_region_init_ram(scratch, NULL, "ricoh-rtos-lab.boot-sram", 64 * KiB,
                           &error_fatal);
    memory_region_add_subregion(get_system_memory(), 0x01000000, scratch);
    gic = qdev_new(TYPE_A15MPCORE_PRIV);
    qdev_prop_set_uint32(gic, "num-cpu", 3);
    qdev_prop_set_uint32(gic, "num-irq", 256);
    gic_bus = SYS_BUS_DEVICE(gic);
    sysbus_realize_and_unref(gic_bus, &error_fatal);
    sysbus_mmio_map(gic_bus, 0, 0x1d000000);
    for (i = 0; i < 3; i++) {
        sysbus_connect_irq(gic_bus, i, qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_IRQ));
        sysbus_connect_irq(gic_bus, i + 3, qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_FIQ));
        sysbus_connect_irq(gic_bus, i + 6, qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_VIRQ));
        sysbus_connect_irq(gic_bus, i + 9, qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_VFIQ));
    }
    timer->irq = qdev_get_gpio_in(gic, 99);
    timer->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, ricoh_rtos_timer_expire, timer);
    memory_region_init_io(&timer->memory, NULL, &ricoh_rtos_timer_ops, timer,
                          "ricoh-rtos-lab.timer13", 16);
    memory_region_add_subregion(get_system_memory(), 0x1e0000d0, &timer->memory);
    memory_region_init_io(&usb_jt->memory, NULL, &ricoh_usb_jt_ops, usb_jt,
                          "ricoh-rtos-lab.usb3-jt-adjustment", 4);
    memory_region_add_subregion(get_system_memory(), 0x1b110298, &usb_jt->memory);
    counter->control = 1;
    counter->started = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    memory_region_init_io(&counter->memory, NULL, &ricoh_system_counter_ops,
                          counter, "ricoh-rtos-lab.system-counter", 0x24);
    memory_region_add_subregion(get_system_memory(), 0x1d010000, &counter->memory);
}

static void ricoh_rtos_class_init(MachineClass *mc)
{
    static const char *const cpus[] = { ARM_CPU_TYPE_NAME("cortex-a7"), NULL };
    mc->desc = "Experimental Ricoh secure RTOS reset trace (no camera peripherals)";
    mc->init = ricoh_rtos_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a7");
    mc->valid_cpu_types = cpus;
    mc->default_ram_size = 512 * MiB;
    mc->default_ram_id = "ricoh-rtos-lab.ram";
    mc->default_cpus = 3;
    mc->min_cpus = 3;
    mc->max_cpus = 3;
    mc->ignore_memory_transaction_failures = false;
}
DEFINE_MACHINE_ARM("ricoh-rtos-lab", ricoh_rtos_class_init)
