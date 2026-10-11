# Insta360 X3 H22 Linux lab

This separately built QEMU board runs the original X3 AArch64 Linux 4.9.76
kernel and original SquashFS/BusyBox userspace from the verified ONEX3
`v1.1.4.1_build1` update. It is an assisted Linux development console.
No firmware, decryption key, emulator binary or filesystem image is included.

## Build

On Linux, or an explicitly selected WSL distribution, install the usual QEMU
build dependencies plus `device-tree-compiler`, then run:

```text
python3 scripts/boards/insta360/build.py --work-dir ~/.cache/qemu-everything/insta360-h22
```

The builder verifies the original QEMU 10.2.4 archive SHA-256, builds a separate
`qemu-system-aarch64`, and writes `build/insta360-lab-build.json`. The GPL board
overlay and QEMU remain separate from the Python workbench. These board files
are licensed GPL-2.0-or-later; see COPYING.

Create `insta360-lab.json` in the workbench data home:

```json
{
  "distro": "Debian",
  "manifest": "/home/your-user/.cache/qemu-everything/insta360-h22/build/insta360-lab-build.json"
}
```

Import and unpack a user-supplied X3 update using the normal workbench pipeline.
`devkit.insta360_lab.run_lab(device, home, task, seconds=30)` returns a private
`evidence.json` beside the serial/emulator logs. The bridge checks exact artifact
sizes and hashes, verifies the build and overlay, and stops only its owned guest.
The original kernel, rootfs and checked development DTB are frozen into a private
temporary directory before QEMU reads them. Snapshot hashes are verified and
original input/DTB hashes are rechecked after the bounded session.
No guest network adapter or externally listening socket is enabled.

## Actual bring-up and limits

The board provides RAM, Cortex-A53, GICv2, architectural timer and an Ambarella
UART register model. Its broad SoC register bank is explicitly a stub. The
development device tree uses one CPU and an empty pinctrl state for a virtual
UART. A reset-completion callback enables the distributor, replacing the absent
RTOS interrupt-controller handoff. Vendor kernel instructions remain unchanged.

The original SquashFS is passed as a readonly RAM disk with `init=/bin/sh`.
Real execution was verified through `uname`, `id`, `/proc/mounts` and a fresh
random proof token returned as an independent serial line. A bounded session
records these milestones separately from original-UI verification.

An additional original `/sbin/init` test reached the real `rcS` scripts. It
then hit a NULL RPMsg channel in `rpmsg_example_printk` when `S99bootdone` wrote
the echo module parameter. The ThreadX side, DSP, sensor, camera display and
RTOS RPC services are not implemented; original camera UI is not verified.

References: [official X3 download page](https://www.insta360.com/download/insta360-x3),
[QEMU source](https://download.qemu.org/qemu-10.2.4.tar.xz),
[Ambarella H22A brief](https://www.ambarella.com/wp-content/uploads/Ambarella_H22A_Product_Brief_07APR2022.pdf).

Private diagnostic symbol recovery used
[marin-m/vmlinux-to-elf](https://github.com/marin-m/vmlinux-to-elf) at
`19683fb95b29cd31362d49e6f48ab8368f96cbdf` (GPL-3.0-or-later);
that code is not included or needed by this board.
