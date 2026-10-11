/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Experimental H22 Linux bring-up. UART/GIC/RAM are functional; the SoC
 * register bank is explicitly a stub. No DSP, RTOS or original UI is implied.
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

typedef struct X3Lab {
    MemoryRegion registers, uart;
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

static void x3_rtos_gic_handoff(void *opaque)
{
    const uint8_t enabled[4] = {1, 0, 0, 0};
    /* X3 Linux initializes its CPU interface but delegates distributor
     * initialization to its absent RTOS. Reproduce only that handoff bit.
     * This is an explicitly assisted initial device state, not a boot ROM.
     */
    cpu_physical_memory_write(0xf0041000, enabled, sizeof(enabled));
    fprintf(stderr, "X3-LAB assisted RTOS handoff: GICD_CTLR=1\n");
}

static void x3_reset(void *opaque)
{
    X3Lab *s = opaque;
    /* Device cold reset clears the distributor after legacy reset handlers.
     * Apply the missing boot-firmware handoff when the virtual clock resumes.
     */
    timer_mod(s->handoff, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
}

static void x3_init(MachineState *machine)
{
    X3Lab *s = g_new0(X3Lab, 1);
    DeviceState *cpu = qdev_new(machine->cpu_type), *gic;
    MemoryRegion *memory = get_system_memory();
    int timer_ppi[4] = {14, 11, 10, 13};

    object_property_set_bool(OBJECT(cpu), "has_el3", false, &error_fatal);
    qdev_realize(cpu, NULL, &error_fatal);
    memory_region_add_subregion(memory, 0x200000, machine->ram);
    gic = qdev_new("arm_gic");
    qdev_prop_set_uint32(gic, "revision", 2);
    qdev_prop_set_uint32(gic, "num-cpu", 1);
    qdev_prop_set_uint32(gic, "num-irq", 256);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gic), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(gic), 0, 0xf0041000);
    sysbus_mmio_map(SYS_BUS_DEVICE(gic), 1, 0xf0042000);
    sysbus_connect_irq(SYS_BUS_DEVICE(gic), 0, qdev_get_gpio_in(cpu, ARM_CPU_IRQ));
    sysbus_connect_irq(SYS_BUS_DEVICE(gic), 1, qdev_get_gpio_in(cpu, ARM_CPU_FIQ));
    for (int i = 0; i < 4; i++) {
        qdev_connect_gpio_out(cpu, i, qdev_get_gpio_in(gic, 224 + 16 + timer_ppi[i]));
    }
    memory_region_init_io(&s->registers, OBJECT(machine), &bank_ops, s,
                          "x3-register-stub", 0x10000000);
    memory_region_add_subregion(memory, 0xe0000000, &s->registers);
    s->uart_irq = qdev_get_gpio_in(gic, 61);
    memory_region_init_io(&s->uart, OBJECT(machine), &uart_ops, s, "x3-uart", 0x1000);
    memory_region_add_subregion_overlap(memory, 0xe8005000, &s->uart, 1);
    qemu_chr_fe_init(&s->serial, serial_hd(0), &error_fatal);
    qemu_chr_fe_set_handlers(&s->serial, uart_can_receive, uart_receive, NULL,
                             NULL, s, NULL, true);
    s->boot.ram_size = machine->ram_size;
    s->boot.loader_start = 0x200000;
    s->boot.kernel_filename = machine->kernel_filename;
    s->boot.kernel_cmdline = machine->kernel_cmdline;
    s->boot.initrd_filename = machine->initrd_filename;
    s->boot.dtb_filename = machine->dtb;
    s->boot.psci_conduit = QEMU_PSCI_CONDUIT_HVC;
    arm_load_kernel(ARM_CPU(cpu), machine, &s->boot);
    s->handoff = timer_new_ns(QEMU_CLOCK_VIRTUAL, x3_rtos_gic_handoff, s);
    qemu_register_reset(x3_reset, s);
}

static void x3_class_init(MachineClass *mc)
{
    mc->desc = "Insta360 X3 H22 experimental Linux lab (stub peripherals)";
    mc->init = x3_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a53");
    mc->default_ram_id = "insta360-x3-linux.ram";
    mc->default_ram_size = 128 * MiB;
    mc->max_cpus = mc->default_cpus = 1;
}
DEFINE_MACHINE_AARCH64("insta360-h22-lab", x3_class_init)
