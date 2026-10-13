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
#include "qemu/error-report.h"
#include "qapi/error.h"
#include "hw/boards.h"
#include "hw/arm/machines-qom.h"
#include "hw/cpu/a15mpcore.h"
#include "hw/qdev-properties.h"
#include "hw/sysbus.h"
#include "hw/irq.h"
#include "chardev/char-fe.h"
#include "system/system.h"
#include "system/address-spaces.h"
#include "target/arm/cpu.h"

typedef struct RicohRTOSTimer {
    MemoryRegion memory;
    QEMUTimer *timer;
    qemu_irq irq;
    uint32_t csr, reload, reload2;
    int64_t started;
    bool selectable_clock;
} RicohRTOSTimer;

/* Register offsets/bits are confirmed against Ricoh's GPL milbeaut-timer.c.
 * Original GR IV reset code selects channel 13, CSL=1, reload=6749 and
 * IRQ 131 (SPI 99). Its elapsed-time conversion implies 6.75 MHz.
 * Timer events reflect actual virtual elapsed time; no fixed ready values. */
#define RTOS_TIMER_HZ 6750000

static uint64_t ricoh_rtos_timer_rate(RicohRTOSTimer *s)
{
    if (!s->selectable_clock) {
        return RTOS_TIMER_HZ;
    }
    /* Development source 27 MHz: original timer13 CSL=1/reload=6749
     * establishes its 6.75 MHz clock; extrapolated CSL=0 is /2.
     * External/other selector values are deliberately unsupported. */
    switch ((s->csr >> 10) & 3) {
    case 0: return 13500000;
    case 1: return 6750000;
    default: return 0;
    }
}

static void ricoh_rtos_timer_schedule(RicohRTOSTimer *s)
{
    timer_del(s->timer);
    if ((s->csr & 2) && s->reload && ricoh_rtos_timer_rate(s)) {
        s->started = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        timer_mod(s->timer, s->started +
                  ((uint64_t)s->reload + 1) * 1000000000ULL / ricoh_rtos_timer_rate(s));
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
                        ricoh_rtos_timer_rate(s), 1000000000ULL);
        return s->reload - MIN(ticks, s->reload);
    case 8: return s->reload;
    case 12: return s->reload2;
    default: return 0;
    }
}

static MemTxResult ricoh_rtos_timer_write(void *opaque, hwaddr addr, uint64_t value,
                                          unsigned size, MemTxAttrs attrs)
{
    RicohRTOSTimer *s = opaque;
    switch (addr) {
    case 0:
        if (s->selectable_clock && (value & 2) && ((value >> 10) & 3) > 1) {
            qemu_log_mask(LOG_UNIMP, "ricoh-rtos-lab: unknown timer clock selector\n");
            return MEMTX_ERROR;
        }
        s->csr = value & ~1U;
        qemu_set_irq(s->irq, (s->csr & 12) == 12);
        ricoh_rtos_timer_schedule(s);
        break;
    case 4: case 8: s->reload = value; break;
    case 12: s->reload2 = value; break;
    }
    return MEMTX_OK;
}

static const MemoryRegionOps ricoh_rtos_timer_ops = {
    .read = ricoh_rtos_timer_read,
    .write_with_attrs = ricoh_rtos_timer_write,
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

typedef struct RicohRTOSConsole {
    MemoryRegion memory;
    CharFrontend chr;
    uint8_t regs[16];
} RicohRTOSConsole;

/* The original polling TX routine at 0x53ac8340 checks SSR offset 3
 * TDRE bit 1, then writes the low character bits to halfword DR offset 4.
 * These fields match Ricoh GPL usio.c. The virtual TX buffer is empty
 * after its character is consumed by the local chardev. No RX bytes or
 * serial interrupts are fabricated; this initial console is polled TX only. */
static uint64_t ricoh_rtos_console_read(void *opaque, hwaddr addr, unsigned size)
{
    RicohRTOSConsole *s = opaque;
    uint64_t value = 0;
    unsigned i;
    if (addr == 3) { return 3; }
    if (addr == 4) { return 0; }
    for (i = 0; i < size && addr + i < 16; i++) {
        value |= (uint64_t)s->regs[addr + i] << (8 * i);
    }
    return value;
}

static void ricoh_rtos_console_write(void *opaque, hwaddr addr, uint64_t value,
                                     unsigned size)
{
    RicohRTOSConsole *s = opaque;
    unsigned i;
    if (addr == 4) {
        uint8_t character = value;
        qemu_chr_fe_write_all(&s->chr, &character, 1);
        return;
    }
    for (i = 0; i < size && addr + i < 16; i++) {
        s->regs[addr + i] = value >> (8 * i);
    }
}

static const MemoryRegionOps ricoh_rtos_console_ops = {
    .read = ricoh_rtos_console_read,
    .write = ricoh_rtos_console_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 4,
    .impl.min_access_size = 1, .impl.max_access_size = 4,
};

/* Opt-in diagnostic hypothesis. Firmware proves these locations are
 * read-modify-write configuration arrays, but their reset values and the
 * physical peripheral are not established. They are never mapped by default.
 * Zero initial contents are experimental assumptions, not hardware facts. */
static bool ricoh_experimental_latches;
static bool ricoh_experimental_gate_idle;

typedef struct RicohExperimentalConfig {
    MemoryRegion memory;
    hwaddr base;
    uint32_t values[0x40000 / 4];
    bool observed[0x40000 / 4];
    bool written[0x40000 / 4];
} RicohExperimentalConfig;

typedef struct RicohEmptyI2C {
    MemoryRegion memory;
    QEMUTimer *timer;
    qemu_irq irq;
    hwaddr base;
    uint32_t registers[8];
    uint8_t pending_mode;
    bool address_written;
} RicohEmptyI2C;

/* Independently authored empty-bus controller. GR IV GPL
 * i2c-snisc2000.c establishes START/STOP/CLRST and status mode meanings;
 * sc2000.dtsi establishes three blocks and SPI101+channel. There are no
 * attached power, touch, sensor or camera devices: every address NACKs.
 * Diagnostic zero reset / 50 MHz PCLK are explicit assumptions. */
static void ricoh_empty_i2c_irq(RicohEmptyI2C *s)
{
    qemu_set_irq(s->irq, (s->registers[1] & 0x80) &&
                         (s->registers[3] & 4));
}

static void ricoh_empty_i2c_event(void *opaque)
{
    RicohEmptyI2C *s = opaque;
    s->registers[1] = s->pending_mode | 0x80;
    ricoh_empty_i2c_irq(s);
}

static void ricoh_empty_i2c_schedule(RicohEmptyI2C *s, uint8_t mode)
{
    unsigned divider = MAX((s->registers[5] >> 1) & 0x7f, 4);
    /* GPL frequency literals establish SCL=PCLK/(4*divider).
     * A byte/address event takes nine clocks; one START takes one. */
    unsigned clocks = mode == 1 || mode == 2 ? 1 : 9;
    s->pending_mode = mode;
    timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                       (uint64_t)clocks * 4 * divider * 1000000000ULL / 50000000);
}

static MemTxResult ricoh_empty_i2c_read(void *opaque, hwaddr addr,
                                      uint64_t *value, unsigned size,
                                      MemTxAttrs attrs)
{
    RicohEmptyI2C *s = opaque;
    if (addr > 0x1c || addr & 3) { return MEMTX_ERROR; }
    *value = s->registers[addr / 4];
    return MEMTX_OK;
}

static MemTxResult ricoh_empty_i2c_write(void *opaque, hwaddr addr,
                                       uint64_t value, unsigned size,
                                       MemTxAttrs attrs)
{
    RicohEmptyI2C *s = opaque;
    uint32_t previous = s->registers[1];
    if (addr > 0x1c || addr & 3 || addr == 4) { return MEMTX_ERROR; }
    qemu_log_mask(LOG_UNIMP, "ricoh-rtos-lab: EMPTY I2C %08x=%08x\n",
                  (unsigned)(s->base + addr), (unsigned)value);
    s->registers[addr / 4] = value;
    if (addr == 0) { s->address_written = true; }
    if (addr == 0xc) {
        s->registers[3] &= ~0x83U; /* command bits are self clearing */
        if (value & 0x80) {
            s->registers[1] &= ~0x80U;
            if (s->address_written && ((previous & 0x7f) == 1 ||
                                       (previous & 0x7f) == 2)) {
                uint32_t address = s->registers[0];
                qemu_log_mask(LOG_UNIMP,
                    "ricoh-rtos-lab: EMPTY I2C %08x address %02x %s NACK (no attached slave)\n",
                    (unsigned)s->base, address >> 1,
                    address & 1 ? "read" : "write");
                ricoh_empty_i2c_schedule(s, address & 1 ? 9 : 5);
                s->address_written = false;
            }
        }
        if (value & 2) {
            timer_del(s->timer);
            s->registers[1] = 0;
            s->registers[2] &= ~1U;
        } else if ((value & 1) && (s->registers[5] & 1)) {
            bool repeated = (s->registers[2] & 1) != 0;
            s->registers[2] |= 1;
            ricoh_empty_i2c_schedule(s, repeated ? 2 : 1);
        }
    }
    if (addr == 0x14 && !(value & 1)) {
        timer_del(s->timer);
        s->registers[1] = 0;
        s->registers[2] &= ~1U;
    }
    ricoh_empty_i2c_irq(s);
    return MEMTX_OK;
}

static const MemoryRegionOps ricoh_empty_i2c_ops = {
    .read_with_attrs = ricoh_empty_i2c_read,
    .write_with_attrs = ricoh_empty_i2c_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 4,
    .impl.min_access_size = 1, .impl.max_access_size = 4,
};

static bool ricoh_experimental_config_offset(RicohExperimentalConfig *s, hwaddr addr)
{
    hwaddr offset = addr & 0x1ffff;
    if (s->base == 0x1c26f000) {
        /* GPL pinctrl-milbeaut.c: EXIU EIMASK/EISRCSEL/EIREQSTA/
         * EIRAWREQSTA/EIREQCLR/EILVL/EIEDG/EISIR at 0..0x1c.
         * External pin sources are unconnected, hence no requests. */
        return addr <= 0x1c && !(addr & 3);
    }
    if (s->base == 0x1c250000 || s->base == 0x19140000) {
        /* GPL milbeaut-xdmac.c: XDACS bit28 XE, channel stride 0x30,
         * XDTBC through XDDSD at 0x10..0x30. Original 0x53aace00
         * confirms XDDES +0x28 bit28 CE for this exact block. */
        return addr == 0 || (addr >= 0x10 && addr <= 0xc0 &&
                            (addr - 0x10) % 0x30 <= 0x20 && !(addr & 3));
    }
    if (s->base == 0x1b110000) {
        /* Original 0x53aae224 updates +0x60 RW configuration bits.
         * GPL reset-exstop.c defines soft reset/RAM power/USB2 control. */
        /* Original 0x53aada68..0x53aadee0 separately RMW +0x10/+0x14/
         * +0x1c/+0x30 configuration fields.
         * Their bit semantics remain diagnostic configuration hypotheses. */
        return addr == 0xc || addr == 0x10 || addr == 0x14 ||
               addr == 0x1c || addr == 0x30 ||
               addr == 0x40 || addr == 0x60 || addr == 0x70 ||
               (addr >= 0x200 && addr <= 0x244 && !(addr & 3));
    }
    if (s->base == 0x1e100000 || s->base == 0x1e110000 || s->base == 0x1b040000) {
        /* Official GPL milbeaut-hdmac.c/mb8ac0300-hdmac.c: global DMACR
         * then eight 0x10-byte DMACA/DMACB/SRC/DST channel blocks. */
        hwaddr limit = s->base == 0x1b040000 ? 0x50 : 0x90;
        return addr == 0 || (addr >= 0x10 && addr < limit && !(addr & 3));
    }
    if (s->base == 0x1d020000) {
        /* Original 0x53a3d0e0..0x53a3d3d0 explicitly updates these
         * source-selection bits. Initial selection is diagnostic zero. */
        return addr <= 0x20 && !(addr & 3);
    }
    if (s->base == 0x1d021000) {
        /* Official GPL clk-mlb01.c defines contiguous CLKSEL, PLLCNT,
         * CLKSTOP, CRSWR/CRRRS/CRRSM words through +0x94. Original
         * 0x53a3f3e8 writes its clock configuration in this range. */
        return addr <= 0x94 && !(addr & 3);
    }
    if (s->base == 0x1dffb000) {
        /* GPL pinctrl-mlb01-cm0.c: 48 pins in three 16-bit registers,
         * PDR=0, DDR=0x100, EPCR=0x200, PUDER=0x300, PUDCR=0x400. */
        return (addr & 0xff) <= 8 && !(addr & 3) && addr <= 0x408;
    }
    if (s->base == 0x1d022000) {
        /* GR IIIx GPL pinctrl-milbeaut.c identifies MLB01 26x8 pins,
         * 16 pins per word, the same direction/pull/PDR mask interface. */
        return (addr & 0xff) <= 0x30 && !(addr & 3) && addr <= 0x430;
    }
    if (s->base == 0x1dffe000) {
        /* Original UART setup 0x53ac7c88 reads selector bit 24 here,
         * and an 8-bit divisor at +0x100. Zero selects its 100 MHz path
         * and divider 1. Both reset values remain explicit hypotheses. */
        return addr == 0 || addr == 0x100;
    }
    if (s->base == 0x1e002000) {
        /* Original 0x53ac298c initializes six channels at stride 0x20:
         * offset 0x14=0x8008, 0xc=0, preserve high halfword at 0x4.
         * The peripheral identity and external events remain unknown. */
        return addr < 0xc0 && ((addr & 0x1f) == 4 ||
                              (addr & 0x1f) == 0xc || (addr & 0x1f) == 0x14);
    }
    if (s->base != 0x28820000) {
        /* Original mapping switch 0x53aca24c and configuration helpers
         * 0x53aca30c..0x53acc018 establish these exact RW offsets/arrays.
         * They do not establish reset values or completion behavior. */
        return offset == 4 || offset == 8 || offset == 0xc || offset == 0x10 ||
               offset == 0x34 || offset == 0x38 || offset == 0x3c ||
               offset == 0x40 || offset == 0x44 || offset == 0x48 ||
               offset == 0x4c || offset == 0x60 || offset == 0x140 ||
               (offset >= 0x160 && offset < 0x180) ||
               (offset >= 0x1000 && offset < 0x1800 && (offset & 0xff) < 0xfc) ||
               (offset >= 0x2400 && offset < 0x2600 && (offset & 0x3f) < 0x30) ||
               offset == 0x3004 || (offset >= 0x3040 && offset < 0x3050);
    }
    return offset < 0x80 || (offset >= 0x5000 && offset < 0x5080) ||
           (offset >= 0x5200 && offset < 0x5280) ||
           (offset >= 0x5400 && offset < 0x5480) || offset == 0x5a00;
}

static MemTxResult ricoh_experimental_config_read(void *opaque, hwaddr addr,
                                                  uint64_t *value, unsigned size,
                                                  MemTxAttrs attrs)
{
    RicohExperimentalConfig *s = opaque;
    if (!ricoh_experimental_config_offset(s, addr)) {
        return MEMTX_ERROR;
    }
    if (!s->observed[addr / 4]) {
        qemu_log_mask(LOG_UNIMP,
                      "ricoh-rtos-lab: EXPERIMENTAL reset=0 config read %08x\n",
                      (unsigned)(s->base + addr));
        s->observed[addr / 4] = true;
    }
    *value = s->values[addr / 4];
    if (s->base == 0x1c26f000 && (addr == 8 || addr == 0xc || addr == 0x10)) {
        *value = 0; /* No external interrupt source is attached. */
    }
    if ((s->base == 0x1dffb000 && addr <= 8) ||
        (s->base == 0x1d022000 && addr <= 0x30)) {
        uint32_t direction = s->values[(0x100 + addr) / 4];
        uint32_t pulls = s->values[(0x300 + addr) / 4] &
                         s->values[(0x400 + addr) / 4];
        /* Diagnostic unconnected pins follow programmed pull resistors.
         * Pins without a pull resolve to assumed zero; no camera button,
         * battery, lens or power-controller input is synthesized. */
        *value = ((*value & direction) | (pulls & ~direction)) & 0xffff;
    }
    if ((s->base == 0x28000000 || s->base == 0x28400000 ||
         s->base == 0x28500000 || s->base == 0x28810000 ||
         s->base == 0x28814000 || s->base == 0x28818000 ||
         s->base == 0x2881c000) && addr == 0x38 &&
        ricoh_experimental_gate_idle) {
        hwaddr control = (addr & ~0x1ffff) + 0xc;
        if (s->written[control / 4] && !(s->values[control / 4] & 1)) {
            *value |= 1;
        }
    }
    return MEMTX_OK;
}

static MemTxResult ricoh_experimental_config_write(void *opaque, hwaddr addr,
                                                   uint64_t value, unsigned size,
                                                   MemTxAttrs attrs)
{
    RicohExperimentalConfig *s = opaque;
    if (!ricoh_experimental_config_offset(s, addr)) {
        return MEMTX_ERROR;
    }
    if (s->base == 0x1c26f000 &&
        (addr == 8 || addr == 0xc || (addr == 0x1c && value))) {
        qemu_log_mask(LOG_UNIMP,
                      "ricoh-rtos-lab: EXIU unsupported request/status operation\n");
        return MEMTX_ERROR;
    }
    if ((s->base == 0x1c250000 || s->base == 0x19140000) &&
        addr >= 0x28 && (addr - 0x28) % 0x30 == 0 &&
        (value & 0x11000000) == 0x11000000) {
        qemu_log_mask(LOG_UNIMP,
                      "ricoh-rtos-lab: XDMAC transfer requested; configuration-only diagnostic refuses fake completion\n");
        return MEMTX_ERROR;
    }
    if ((s->base == 0x1e100000 || s->base == 0x1e110000 ||
         s->base == 0x1b040000) &&
        addr >= 0x10 && !(addr & 0xf) &&
        (value & 0xa0000000) == 0xa0000000) {
        qemu_log_mask(LOG_UNIMP,
                      "ricoh-rtos-lab: HDMAC transfer requested; configuration-only diagnostic refuses fake completion\n");
        return MEMTX_ERROR;
    }
    qemu_log_mask(LOG_UNIMP,
                  "ricoh-rtos-lab: EXPERIMENTAL config latch %08x=%08x\n",
                  (unsigned)(s->base + addr), (unsigned)value);
    if (s->base == 0x1d021000 && addr >= 0x54 && addr <= 0x88) {
        /* GPL _mgc_enable writes paired bits: 2 enables (stop=0),
         * 3 disables (stop=1). The odd bit is a write mask, not state. */
        uint32_t mask = ((uint32_t)value >> 1) & 0x55555555;
        s->values[addr / 4] = (s->values[addr / 4] & ~mask) | (value & mask);
    } else if ((s->base == 0x1dffb000 && addr <= 8) ||
               (s->base == 0x1d022000 && addr <= 0x30)) {
        /* GPL cm0_gpio_set writes an upper-halfword mask and low-halfword
         * data to PDR. Keep untouched output pins; no input events invented. */
        uint32_t mask = (value >> 16) & 0xffff;
        s->values[addr / 4] = (s->values[addr / 4] & ~mask) | (value & mask);
    } else {
        s->values[addr / 4] = value;
    }
    s->written[addr / 4] = true;
    return MEMTX_OK;
}

static const MemoryRegionOps ricoh_experimental_config_ops = {
    .read_with_attrs = ricoh_experimental_config_read,
    .write_with_attrs = ricoh_experimental_config_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static bool ricoh_experimental_latches_get(Object *object, Error **errp)
{
    return ricoh_experimental_latches;
}

static void ricoh_experimental_latches_set(Object *object, bool value,
                                           Error **errp)
{
    ricoh_experimental_latches = value;
}

static bool ricoh_experimental_gate_idle_get(Object *object, Error **errp)
{
    return ricoh_experimental_gate_idle;
}

static bool ricoh_experimental_memory_layout;

static bool ricoh_experimental_memory_get(Object *object, Error **errp)
{
    return ricoh_experimental_memory_layout;
}

static void ricoh_experimental_memory_set(Object *object, bool value,
                                         Error **errp)
{
    ricoh_experimental_memory_layout = value;
}

static void ricoh_experimental_gate_idle_set(Object *object, bool value,
                                             Error **errp)
{
    ricoh_experimental_gate_idle = value;
}

static void ricoh_rtos_init(MachineState *machine)
{
    ARMCPU *cpus[3];
    DeviceState *gic;
    SysBusDevice *gic_bus;
    MemoryRegion *scratch = g_new0(MemoryRegion, 1);
    RicohRTOSTimer *timer;
    RicohUSB3JTAdjustment *usb_jt = g_new0(RicohUSB3JTAdjustment, 1);
    RicohSystemCounter *counter = g_new0(RicohSystemCounter, 1);
    RicohRTOSConsole *console = g_new0(RicohRTOSConsole, 1);
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
    if (ricoh_experimental_memory_layout) {
        MemoryRegion *bank0 = g_new0(MemoryRegion, 1);
        MemoryRegion *bank1 = g_new0(MemoryRegion, 1);
        if (machine->ram_size != 2 * GiB) {
            error_report("Ricoh diagnostic memory layout requires -m 2048");
            exit(1);
        }
        /* Original pool setup at 0x538e3c18 allocates through 0x80000000;
         * the next descriptor starts at 0xa0000000 with size 0x40000000.
         * This is an explicit diagnostic RAM layout inferred from original
         * software, not a claim about physical DRAM bank wiring/capacity. */
        memory_region_init_alias(bank0, NULL, "ricoh-rtos-lab.EXPERIMENTAL-bank0",
                                 machine->ram, 0, GiB);
        memory_region_init_alias(bank1, NULL, "ricoh-rtos-lab.EXPERIMENTAL-bank1",
                                 machine->ram, GiB, GiB);
        memory_region_add_subregion(get_system_memory(), 0x40000000, bank0);
        memory_region_add_subregion(get_system_memory(), 0xa0000000, bank1);
    } else {
        memory_region_add_subregion(get_system_memory(), 0x40000000, machine->ram);
    }
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
    for (i = 0; i < 16; i++) {
        g_autofree char *name = g_strdup_printf("ricoh-rtos-lab.timer%d", i);
        if (i != 13 && !ricoh_experimental_latches) {
            continue;
        }
        timer = g_new0(RicohRTOSTimer, 1);
        timer->selectable_clock = ricoh_experimental_latches;
        /* GPL sc2000.dtsi maps channel5 to SPI91; original RTOS channel13
         * uses SPI99. Both independently establish SPI86 + channel. */
        timer->irq = qdev_get_gpio_in(gic, 86 + i);
        timer->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, ricoh_rtos_timer_expire, timer);
        memory_region_init_io(&timer->memory, NULL, &ricoh_rtos_timer_ops, timer,
                              name, 16);
        memory_region_add_subregion(get_system_memory(), 0x1e000000 + i * 16, &timer->memory);
    }
    memory_region_init_io(&usb_jt->memory, NULL, &ricoh_usb_jt_ops, usb_jt,
                          "ricoh-rtos-lab.usb3-jt-adjustment", 4);
    memory_region_add_subregion(get_system_memory(), 0x1b110298, &usb_jt->memory);
    counter->control = 1;
    counter->started = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    memory_region_init_io(&counter->memory, NULL, &ricoh_system_counter_ops,
                          counter, "ricoh-rtos-lab.system-counter", 0x24);
    memory_region_add_subregion(get_system_memory(), 0x1d010000, &counter->memory);
    qemu_chr_fe_init(&console->chr, serial_hd(0), &error_fatal);
    memory_region_init_io(&console->memory, NULL, &ricoh_rtos_console_ops, console,
                          "ricoh-rtos-lab.polled-usio-console", 16);
    memory_region_add_subregion(get_system_memory(), 0x1de00000, &console->memory);
    if (ricoh_experimental_latches) {
        RicohExperimentalConfig *config = g_new0(RicohExperimentalConfig, 1);
        static const hwaddr gate_bases[] = {
            0x28000000, 0x28400000, 0x28500000, 0x28810000,
            0x28814000, 0x28818000, 0x2881c000, 0x1e002000, 0x1dffe000,
            0x1dffb000,
            0x1d020000, 0x1d021000,
            0x1e100000, 0x1e110000,
            0x1b110000,
            0x1b040000,
            0x1c250000,
            0x19140000,
            0x1d022000,
            0x1c26f000,
        };
        config->base = 0x28820000;
        memory_region_init_io(&config->memory, NULL,
                              &ricoh_experimental_config_ops, config,
                              "ricoh-rtos-lab.EXPERIMENTAL-config-latches", 0x40000);
        memory_region_add_subregion(get_system_memory(), 0x28820000,
                                    &config->memory);
        for (i = 0; i < ARRAY_SIZE(gate_bases); i++) {
            RicohExperimentalConfig *gate = g_new0(RicohExperimentalConfig, 1);
            g_autofree char *name = g_strdup_printf(
                "ricoh-rtos-lab.EXPERIMENTAL-config-gate-%08x", (unsigned)gate_bases[i]);
            gate->base = gate_bases[i];
            memory_region_init_io(&gate->memory, NULL,
                                  &ricoh_experimental_config_ops, gate, name,
                                  gate->base == 0x1dffe000 ? 0x104 :
                                  gate->base == 0x1dffb000 ? 0x40c :
                                  gate->base == 0x1d020000 ? 0x24 :
                                  gate->base == 0x1d021000 ? 0x98 :
                                  (gate->base == 0x1e100000 ||
                                   gate->base == 0x1e110000) ? 0x90 :
                                  gate->base == 0x1b110000 ? 0x248 :
                                  gate->base == 0x1b040000 ? 0x50 :
                                  (gate->base == 0x1c250000 ||
                                   gate->base == 0x19140000) ? 0xc4 :
                                  gate->base == 0x1d022000 ? 0x434 :
                                  gate->base == 0x1c26f000 ? 0x20 :
                                  gate->base == 0x1e002000 ? 0xc0 : 0x4000);
            memory_region_add_subregion(get_system_memory(), gate->base, &gate->memory);
        }
        for (i = 0; i < 3; i++) {
            RicohEmptyI2C *i2c = g_new0(RicohEmptyI2C, 1);
            g_autofree char *name = g_strdup_printf("ricoh-rtos-lab.EMPTY-i2c-%u", i);
            i2c->base = 0x1e003000 + 0x400 * i;
            i2c->irq = qdev_get_gpio_in(gic, 101 + i);
            i2c->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, ricoh_empty_i2c_event, i2c);
            memory_region_init_io(&i2c->memory, NULL, &ricoh_empty_i2c_ops,
                                  i2c, name, 0x20);
            memory_region_add_subregion(get_system_memory(), i2c->base, &i2c->memory);
        }
        /* Original configuration helpers clear and fill these three table
         * apertures with ordinary byte stores. Model diagnostic backing
         * memory only; an assumed reset value is not a hardware verification. */
        {
            static const hwaddr table_bases[] = { 0x29400000, 0x29500000, 0x29800000 };
            for (i = 0; i < ARRAY_SIZE(table_bases); i++) {
                MemoryRegion *table = g_new0(MemoryRegion, 1);
                g_autofree char *name = g_strdup_printf(
                    "ricoh-rtos-lab.EXPERIMENTAL-config-table-%08x", (unsigned)table_bases[i]);
                memory_region_init_ram(table, NULL, name, 4 * KiB, &error_fatal);
                memory_region_add_subregion(get_system_memory(), table_bases[i], table);
            }
        }
    }
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
    object_class_property_add_bool(OBJECT_CLASS(mc), "experimental-latches",
                                    ricoh_experimental_latches_get,
                                    ricoh_experimental_latches_set);
    object_class_property_set_description(OBJECT_CLASS(mc), "experimental-latches",
        "Opt-in guessed zero-reset configuration latches; diagnostic only, not camera hardware verification");
    object_class_property_add_bool(OBJECT_CLASS(mc), "experimental-gate-idle",
                                    ricoh_experimental_gate_idle_get,
                                    ricoh_experimental_gate_idle_set);
    object_class_property_set_description(OBJECT_CLASS(mc), "experimental-gate-idle",
        "Diagnostic hypothesis: disabled configuration gate reports idle; hardware semantics unverified");
    object_class_property_add_bool(OBJECT_CLASS(mc), "experimental-memory-layout",
                                    ricoh_experimental_memory_get,
                                    ricoh_experimental_memory_set);
    object_class_property_set_description(OBJECT_CLASS(mc), "experimental-memory-layout",
        "Diagnostic two 1 GiB RAM banks inferred from original pool descriptors; physical layout unverified");
}
DEFINE_MACHINE_ARM("ricoh-rtos-lab", ricoh_rtos_class_init)
