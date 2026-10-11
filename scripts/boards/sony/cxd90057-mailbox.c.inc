/* SPDX-License-Identifier: GPL-2.0-or-later
 * QEMU Everything, Copyright 2026 Derrick Yao.
 * Diagnostic single-core laboratory built on QEMU's virt machine.
 * Back the Sony GPL-source-confirmed boot work area with RAM, without changing the
 * guest Image. This does not model the real multicore handoff controller.
 */
#include "qemu/timer.h"

typedef struct SonyLabTimer {
    MemoryRegion region;
    QEMUTimer *alarm;
    qemu_irq irq;
    uint32_t control, compare, count;
    int64_t epoch;
} SonyLabTimer;

static uint32_t sony_timer_count(SonyLabTimer *t)
{
    uint32_t hz = 4000000U >> (t->control & 7);
    if (!(t->control & 0x1000)) {
        return t->count;
    }
    return t->count + muldiv64(qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - t->epoch,
                               hz, 1000000000);
}

static void sony_timer_arm(SonyLabTimer *t)
{
    uint32_t hz = 4000000U >> (t->control & 7);
    timer_del(t->alarm);
    if ((t->control & 0x1100) == 0x1100) {
        uint64_t delta = (uint32_t)(t->compare - sony_timer_count(t));
        if (!delta) {
            delta = 1;
        }
        timer_mod(t->alarm, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  muldiv64(delta, 1000000000, hz));
    }
}

static void sony_timer_fire(void *opaque)
{
    SonyLabTimer *t = opaque;
    qemu_set_irq(t->irq, 1);
}

static uint64_t sony_timer_read(void *opaque, hwaddr offset, unsigned size)
{
    SonyLabTimer *t = opaque;
    switch (offset) {
    case 0x00: return t->control;
    case 0x08: return t->compare;
    case 0x0c: return sony_timer_count(t);
    case 0x10: return t->count;
    default: return 0;
    }
}

static void sony_timer_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size)
{
    SonyLabTimer *t = opaque;
    switch (offset) {
    case 0x00:
        t->count = sony_timer_count(t);
        t->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        t->control = value;
        break;
    case 0x04:
        if (value & 1) {
            qemu_set_irq(t->irq, 0);
        }
        if (value & 0x10) {
            t->count = 0;
            t->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        }
        break;
    case 0x08: t->compare = value; break;
    case 0x10:
        t->count = value;
        t->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        break;
    default: return;
    }
    sony_timer_arm(t);
}

static const MemoryRegionOps sony_timer_ops = {
    .read = sony_timer_read, .write = sony_timer_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
};

typedef struct SonyLabRegisters {
    MemoryRegion region;
    uint32_t values[32][16];
} SonyLabRegisters;

typedef struct SonyCxd90057LabState {
    VirtMachineState parent;
    MemoryRegion handoff;
    SonyLabTimer timers[4];
    SonyLabRegisters gpio, scu;
    MemoryRegion crypto;
    uint32_t crypto_values[1024];
    MemoryRegion ddr;
    uint32_t ddr_values[4][1024];
} SonyCxd90057LabState;

static uint64_t sony_ddr_read(void *opaque, hwaddr offset, unsigned size)
{
    SonyCxd90057LabState *s = opaque;
    return s->ddr_values[offset / 0x1000][(offset & 0xfff) / 4];
}

static void sony_ddr_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    SonyCxd90057LabState *s = opaque;
    uint32_t *r = s->ddr_values[offset / 0x1000];
    unsigned word = (offset & 0xfff) / 4;
    if (word == 277) {
        r[275] &= ~value;
    } else {
        r[word] = value;
        if (word == 177 && (value & (1U << 25))) {
            r[275] |= 1U << 26;
        }
    }
}

static const MemoryRegionOps sony_ddr_ops = {
    .read = sony_ddr_read, .write = sony_ddr_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
};

static uint64_t sony_gpio_read(void *opaque, hwaddr offset, unsigned size)
{
    SonyLabRegisters *s = opaque;
    unsigned bank = offset / 0x1000;
    unsigned group = (offset & 0xfff) / 0x10;
    if (bank < 32 && group < 16) {
        return s->values[bank][group];
    }
    return 0;
}

static void sony_gpio_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    SonyLabRegisters *s = opaque;
    unsigned bank = offset / 0x1000;
    unsigned group = (offset & 0xfff) / 0x10;
    if (bank >= 32 || group >= 16) {
        return;
    }
    switch (offset & 0xf) {
    case 0: s->values[bank][group] = value; break;
    case 4: s->values[bank][group] |= value; break;
    case 8: s->values[bank][group] &= ~value; break;
    }
}

static const MemoryRegionOps sony_gpio_ops = {
    .read = sony_gpio_read, .write = sony_gpio_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
};

/* Initialization register latches only. AES-XTS operations and encryption
 * keys are not modeled; this device cannot service encrypted storage I/O. */
static uint64_t sony_crypto_read(void *opaque, hwaddr offset, unsigned size)
{
    SonyCxd90057LabState *s = opaque;
    return s->crypto_values[offset / 4];
}

static void sony_crypto_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    SonyCxd90057LabState *s = opaque;
    s->crypto_values[offset / 4] = value;
}

static const MemoryRegionOps sony_crypto_ops = {
    .read = sony_crypto_read, .write = sony_crypto_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
};

static void sony_cxd90057_lab_init(MachineState *machine)
{
    SonyCxd90057LabState *s = (SonyCxd90057LabState *)machine;
    machvirt_init(machine);
    memory_region_init_ram(&s->handoff, NULL,
                           "qe-sony-early-handoff", 0x1000, &error_fatal);
    memory_region_add_subregion(get_system_memory(), 0xfe005000, &s->handoff);
    /* GPIO mode/output registers only. External inputs/interrupts are absent. */
    memory_region_init_io(&s->gpio.region, NULL, &sony_gpio_ops, &s->gpio, "qe-sony-gpio", 0x20000);
    memory_region_add_subregion(get_system_memory(), 0xf1020000, &s->gpio.region);
    /* SCU set/clear latches: clock requests are recorded, but there is no
     * physical PLL/reset tree in this laboratory model. */
    memory_region_init_io(&s->scu.region, NULL, &sony_gpio_ops, &s->scu, "qe-sony-scu-latches", 0x20000);
    memory_region_add_subregion(get_system_memory(), 0xf1380000, &s->scu.region);
    /* DDR mode-command completion only, with immediate completion. RAM is
     * QEMU RAM; DDR PHY, refresh, calibration and power timing are absent. */
    memory_region_init_io(&s->ddr, NULL, &sony_ddr_ops, s, "qe-sony-ddr-mode", 0x4000);
    memory_region_add_subregion(get_system_memory(), 0xf105a000, &s->ddr);
    memory_region_init_io(&s->crypto, NULL, &sony_crypto_ops, s, "qe-sony-crypto-init-latches", 0x1000);
    memory_region_add_subregion(get_system_memory(), 0xf10e0000, &s->crypto);
    /* Independent timer model from public register specifications and the
     * hwtimer driver's observed free-run/compare/reset operations. Peripheral
     * capture and complete hardware timing have not been implemented. */
    for (int i = 0; i < 4; i++) {
        SonyLabTimer *t = &s->timers[i];
        char *name = g_strdup_printf("qe-sony-hwtimer-%d", i);
        t->irq = qdev_get_gpio_in(s->parent.gic, 200 + i);
        t->alarm = timer_new_ns(QEMU_CLOCK_VIRTUAL, sony_timer_fire, t);
        memory_region_init_io(&t->region, NULL, &sony_timer_ops, t, name, 0x1000);
        memory_region_add_subregion(get_system_memory(), 0xf1042000 + i * 0x1000,
                                    &t->region);
        g_free(name);
    }
}

static const char *sony_cxd90057_cpu(const MachineState *machine)
{
    return ARM_CPU_TYPE_NAME("cortex-a35");
}

static void sony_cxd90057_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    arm_virt_compat_set(mc);
    virt_machine_10_2_options(mc);
    mc->desc = "QEMU Everything Sony CXD90057 single-core handoff lab";
    mc->init = sony_cxd90057_lab_init;
    mc->max_cpus = 1;
    mc->get_default_cpu_type = sony_cxd90057_cpu;
}

static const TypeInfo sony_cxd90057_type = {
    .name = MACHINE_TYPE_NAME("sony-cxd90057-lab"),
    .parent = TYPE_VIRT_MACHINE,
    .instance_size = sizeof(SonyCxd90057LabState),
    .class_init = sony_cxd90057_class_init,
    .interfaces = arm_aarch64_machine_interfaces,
};

static void sony_cxd90057_register(void)
{
    type_register_static(&sony_cxd90057_type);
}
type_init(sony_cxd90057_register);
