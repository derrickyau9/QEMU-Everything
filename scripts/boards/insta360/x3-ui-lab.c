/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Assisted original X3 emWin GUI component lab. The firmware executes its
 * own widgets, fonts and event handlers. Cortex-A15 executes the ARMv7 GUI;
 * this is NOT a complete H22/Cortex-A53 or ThreadX power-on emulation.
 * Synthetic RAM platform hooks and diagnostic register latches are explicit.
 */
#include "qemu/osdep.h"
#include "qemu/module.h"
#include "qemu/units.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "hw/boards.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "hw/sysbus.h"
#include "hw/qdev-properties.h"
#include "hw/irq.h"
#include "chardev/char-fe.h"
#include "system/system.h"
#include "system/address-spaces.h"
#include "system/reset.h"
#include "exec/cpu-common.h"
#include "target/arm/cpu.h"
#include "ui/console.h"

typedef struct X3Lab {
    MemoryRegion registers, uart, platform_delay;
    QemuConsole *console;
    bool reopen_pressed;
    uint8_t presented[352 * 476 * 4];
    CharFrontend serial;
    qemu_irq uart_irq;
    uint32_t bank[0x100000 / 4];
    uint8_t rx[256];
    unsigned rx_in, rx_out, rx_count, warnings;
    uint32_t ier, lcr, divisor;
    QEMUTimer *handoff;
    struct arm_boot_info boot;
} X3Lab;

static void uart_irq(X3Lab *s)
{
    qemu_set_irq(s->uart_irq, ((s->ier & 1) && s->rx_count) ||
                 ((s->ier & 2) && !(s->lcr & 0x80)));
}

static int uart_can_receive(void *opaque)
{
    return 256 - ((X3Lab *)opaque)->rx_count;
}

static void uart_receive(void *opaque, const uint8_t *data, int size)
{
    X3Lab *s = opaque;
    for (int i = 0; i < size && s->rx_count < 256; i++) {
        s->rx[s->rx_in++ % 256] = data[i];
        s->rx_count++;
    }
    uart_irq(s);
}

static uint64_t uart_read(void *opaque, hwaddr addr, unsigned size)
{
    X3Lab *s = opaque;
    uint8_t value;
    switch (addr) {
    case 0:
        if (s->lcr & 0x80) {
            return s->divisor & 0xff;
        }
        if (!s->rx_count) {
            return 0;
        }
        value = s->rx[s->rx_out++ % 256];
        s->rx_count--;
        uart_irq(s);
        qemu_chr_fe_accept_input(&s->serial);
        return value;
    case 4: return s->lcr & 0x80 ? s->divisor >> 8 : s->ier;
    case 8: return s->rx_count && (s->ier & 1) ? 4 : s->ier & 2 ? 2 : 1;
    case 12: return s->lcr;
    case 20: return 0x60 | !!s->rx_count;
    case 24: return 0xb0;
    case 0x7c: return 6 | (s->rx_count ? 8 : 0);
    default: return 0;
    }
}

static void uart_write(void *opaque, hwaddr addr, uint64_t data, unsigned size)
{
    X3Lab *s = opaque;
    uint8_t byte = data;
    switch (addr) {
    case 0:
        if (s->lcr & 0x80) {
            s->divisor = (s->divisor & 0xff00) | byte;
        } else {
            qemu_chr_fe_write_all(&s->serial, &byte, 1);
        }
        break;
    case 4:
        if (s->lcr & 0x80) {
            s->divisor = (s->divisor & 0xff) | (byte << 8);
        } else {
            s->ier = byte;
        }
        break;
    case 12: s->lcr = byte; break;
    case 0x88:
        if (data & 1) {
            s->ier = s->lcr = s->rx_count = s->rx_in = s->rx_out = 0;
        }
        break;
    }
    uart_irq(s);
}

static uint64_t bank_read(void *opaque, hwaddr addr, unsigned size)
{
    X3Lab *s = opaque;
    if (s->warnings++ < 128) {
        fprintf(stderr, "X3-LAB stub-read address=0x%" HWADDR_PRIx " size=%u\n",
                0xe0000000 + addr, size);
    }
    return s->bank[(addr % sizeof(s->bank)) / 4];
}

static void bank_write(void *opaque, hwaddr addr, uint64_t data, unsigned size)
{
    X3Lab *s = opaque;
    if (s->warnings++ < 128) {
        fprintf(stderr, "X3-LAB stub-write address=0x%" HWADDR_PRIx " size=%u value=0x%" PRIx64 "\n",
                0xe0000000 + addr, size, data);
    }
    s->bank[(addr % sizeof(s->bank)) / 4] = data;
}

static const MemoryRegionOps uart_ops = {
    .read = uart_read, .write = uart_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 4,
};
static const MemoryRegionOps bank_ops = {
    .read = bank_read, .write = bank_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 4,
};


/* Single active CPU, cooperative emWin delay. Bound each host pause to 20 ms
 * so monitor/input and owned-process shutdown remain responsive. */
static uint64_t delay_read(void *opaque, hwaddr addr, unsigned size)
{
    return 0;
}
static void delay_write(void *opaque, hwaddr addr, uint64_t data, unsigned size)
{
    X3Lab *s = opaque;
    if (addr == 4) {
        cpu_physical_memory_read(0x10000000, s->presented, sizeof(s->presented));
    } else {
        g_usleep(MIN(data, 20) * 1000);
    }
}
static const MemoryRegionOps delay_ops = {
    .read = delay_read, .write = delay_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
};

static void ui_update(void *opaque)
{
    X3Lab *s=opaque;
    DisplaySurface *surface=qemu_console_surface(s->console);
    memcpy(surface_data(surface), s->presented, sizeof(s->presented));
    dpy_gfx_update(s->console,0,0,352,476);
}
static const GraphicHwOps ui_ops={.gfx_update=ui_update};
static void ui_mouse(void *opaque,int x,int y,int z,int buttons)
{
    X3Lab *s = opaque;
    if (buttons & 4) {
        uint32_t pending = cpu_to_le32(2);
        cpu_physical_memory_write(0x1ffe0010, &pending, 4);
        s->reopen_pressed = true;
        return;
    }
    if (s->reopen_pressed) {
        s->reopen_pressed = false;
        return;
    }
    uint32_t words[3]={cpu_to_le32((uint32_t)x*351/32767),cpu_to_le32((uint32_t)y*475/32767),cpu_to_le32(buttons&1)};
    uint32_t pending=cpu_to_le32(1);
    cpu_physical_memory_write(0x1ffe0020,words,sizeof(words));
    cpu_physical_memory_write(0x1ffe0010,&pending,4);
}
static void x3_rtos_init(MachineState *machine)
{
    X3Lab *s = g_new0(X3Lab, 1);
    DeviceState *cpus[3], *gic = qdev_new("arm_gic");
    MemoryRegion *memory = get_system_memory();
    int ppi[4] = {14, 11, 10, 13};
    memory_region_add_subregion(memory, 0, machine->ram);
    memory_region_init_io(&s->platform_delay, OBJECT(machine), &delay_ops, s,
                          "x3-gui-cooperative-delay", 0x1000);
    memory_region_add_subregion(memory, 0xdfff0000, &s->platform_delay);
    s->console=graphic_console_init(NULL,0,&ui_ops,s);
    qemu_console_resize(s->console,352,476);
    qemu_add_mouse_event_handler(ui_mouse,s,1,"X3 original emWin touch harness");
    qdev_prop_set_uint32(gic, "revision", 2);
    qdev_prop_set_uint32(gic, "num-cpu", 3);
    qdev_prop_set_uint32(gic, "num-irq", 256);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gic), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(gic), 0, 0xf0041000);
    sysbus_mmio_map(SYS_BUS_DEVICE(gic), 1, 0xf0042000);
    for (int i = 0; i < 3; ++i) {
        DeviceState *cpu = cpus[i] = qdev_new(machine->cpu_type);
        object_property_set_bool(OBJECT(cpu), "has_el3", false, &error_fatal);
        object_property_set_bool(OBJECT(cpu), "has_el2", false, &error_fatal);
        object_property_set_int(OBJECT(cpu), "mp-affinity", i, &error_fatal);
        object_property_set_int(OBJECT(cpu), "psci-conduit", QEMU_PSCI_CONDUIT_SMC, &error_fatal);
        object_property_set_bool(OBJECT(cpu), "start-powered-off", i != 0, &error_fatal);
        qdev_prop_set_uint64(cpu, "cntfrq", 24000000);
        qdev_realize(cpu, NULL, &error_fatal);
        sysbus_connect_irq(SYS_BUS_DEVICE(gic), i, qdev_get_gpio_in(cpu, ARM_CPU_IRQ));
        sysbus_connect_irq(SYS_BUS_DEVICE(gic), 3+i, qdev_get_gpio_in(cpu, ARM_CPU_FIQ));
        for (int j=0; j<4; ++j) qdev_connect_gpio_out(cpu,j,qdev_get_gpio_in(gic,224+i*32+16+ppi[j]));
    }
    memory_region_init_io(&s->registers,OBJECT(machine),&bank_ops,s,"x3-rtos-register-stub",0x10000000);
    memory_region_add_subregion(memory,0xe0000000,&s->registers);
    s->uart_irq=qdev_get_gpio_in(gic,61);
    memory_region_init_io(&s->uart,OBJECT(machine),&uart_ops,s,"x3-rtos-uart",0x1000);
    memory_region_add_subregion_overlap(memory,0xe8005000,&s->uart,1);
    qemu_chr_fe_init(&s->serial,serial_hd(0),&error_fatal);
    qemu_chr_fe_set_handlers(&s->serial,uart_can_receive,uart_receive,NULL,NULL,s,NULL,true);
}
static void x3_rtos_class_init(MachineClass *mc)
{
    mc->desc="Insta360 X3 assisted original emWin GUI component lab";
    mc->init=x3_rtos_init;
    mc->default_cpu_type=ARM_CPU_TYPE_NAME("cortex-a15");
    mc->default_ram_id="insta360-x3-rtos.ram";
    mc->default_ram_size=512*MiB;
    mc->max_cpus=mc->default_cpus=3;
}
DEFINE_MACHINE_AARCH64("insta360-x3-ui-lab",x3_rtos_class_init)
