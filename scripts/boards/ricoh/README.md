# Ricoh GR Linux service-core lab / 理光 GR Linux 服务核实验

This independent QEMU board starts the **original vendor Linux kernel and rootfs** from GR III 2.10, GR IIIx 1.60 and GR IV 1.11. It is a serial development environment. The camera RTOS, original screen/menu, imaging pipeline and camera-control IPC replies are not implemented.

此板模型启动 GR III 2.10、GR IIIx 1.60、GR IV 1.11 固件内的**原 Linux 内核与原根文件系统**，提供串口开发环境。相机 RTOS、原厂屏幕菜单、成像链路及相机控制 IPC 响应尚未实现。Linux 启动成功不代表原厂 UI 已启动。

## Build an isolated QEMU / 独立构建

Use Linux or WSL Debian with a C compiler, Python 3.12 or newer, Ninja, pkg-config, GLib development headers and device-tree-compiler. The pinned QEMU 10.2.4 archive must have SHA-256:

```text
821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746
```

From the repository in Linux, run:

```bash
python3 scripts/boards/ricoh/build.py \
  --archive /absolute/path/qemu-10.2.4.tar.xz \
  --work-dir /absolute/path/ricoh-qemu-build \
  --jobs 3
```

Use a dedicated work directory. The builder refuses an existing unrelated source checkout. It does not download, build or run vendor firmware. The output `build-manifest.json` records the upstream archive, board/DTS, binary and compiled DTB hashes.

Windows users copy that JSON to a local Windows path, preserving its internal Linux binary/DTB paths, and add `ricoh-lab.json` to the DevKit data home:

```json
{
  "distro": "Debian",
  "manifest": "C:/Users/you/ricoh-qemu-build-manifest.json"
}
```

Import the official update `.bin` into the workbench and run **Unpack firmware** first. GR IV extraction emits `rootfs.squashfs`; the older models emit `ramdisk.cpio.gz`. The runtime only accepts the exact reviewed source/kernel/rootfs fingerprints, and freezes kernel, rootfs and DTB into private Linux files before execution. Different versions require an additional actual boot verification.

## Runtime API / 启动接口

```python
from devkit.ricoh_lab import run_lab
record = run_lab(device, home, seconds=30, mode="services")
```

`device` and `home` are `pathlib.Path` objects for an imported library entry and the DevKit data home. Modes:

- `shell`: original `/bin/sh` as PID 1; verify serial command output, kernel version and root identity.
- `services`: also run the original `camctld -w`, inspect its syslog, process state, ready file and startup mode.
- `init`: original `/sbin/finit` as PID 1; no injected shell commands. Vendor udev child output (GR IV), or the kernel's named D-Bus child entropy read (GR III/IIIx), establishes that init executed.

Every run is limited to 8–120 seconds. Cancellation/EOF stops only the owned QEMU process group. Serial/stderr logs are capped at 4 MiB each; no NIC, camera USB, host filesystem sharing or writable disk is exposed. Original firmware is kept unchanged. Results and exact commands live in `device/ricoh-lab-runs/<run-id>/result.json` and `ricoh-lab-last.json`.

## Deliberate hardware and startup changes / 明确的启动干预

- One Cortex-A7 at logical CPU index 0, 256 MiB Linux RAM and 40 MHz architectural timer. This is a development topology, not the camera's full multiprocessor boot.
- Minimal DTB retains the vendor machine match, GIC and USIO1 serial addresses. Other peripheral nodes are absent; the original extracted `board.dtb` remains unchanged.
- The vendor Linux 4.4 GIC driver requires a nonempty interrupt blacklist. Development DTB reserves unused SPI 0.
- A small independent ARM boot setup writes `GICC_CTLR=1`. The vendor driver preserves the bootloader's Group 0 enable bit while enabling Group 1. Without the bootloader state, the first timer interrupt stays pending and calibration never completes.
- RTOS transport control RAM at `0x4fed0000..0x4fefffff` and metadata RAM at `0x4ff00000..0x4fffffff` are reserved from Linux allocation; an additional zeroed 8 MiB transport backing store is mapped at `0x50000000`. Original DTB `shared_commem` pointer locations are retained, while their values remain zero. It provides memory only, with no synthesized camera state, RTOS allocations or replies.
- Shell probes select the firmware's original shell and mount proc/sys/devtmpfs. Service probes use volatile tmpfs for `/tmp` and `/run`, and run original binaries. GR IV root remains the original read-only SquashFS image.

`original_ui_verified` always remains `false` for this board. A `camctld_ready` file alone is insufficient: both reviewed service-core generations create it in **restrict** mode after their IPCU open failures. The oracle records that distinction.

## Separate original RTOS reset trace / 独立原 RTOS 启动追踪

`ricoh-rtos-lab.c` is a separate board for the exact GR IV 1.11 payload. It does not change the Linux lab binary or its DTB. Build it in a **different** directory:

```bash
python3 scripts/boards/ricoh/build_rtos.py \
  --archive /absolute/path/qemu-10.2.4.tar.xz \
  --work-dir /absolute/path/ricoh-rtos-build \
  --jobs 3

python3 scripts/boards/ricoh/rtos_probe.py \
  --payload /absolute/path/imported-device/extracted/payload.bin \
  --manifest /absolute/path/ricoh-rtos-build/build-manifest.json \
  --output /absolute/path/new-rtos-trace-directory \
  --seconds 5 --bootloader-gic-flag
```

Run inside Linux/WSL. The probe refuses unknown payload or board fingerprints and an existing output directory. It records the exact command, build and input hashes, real instruction/exception trace, CPU registers, all guest-state interventions and owned-process cleanup. Firmware bytes remain local. The ROM prefix begins at payload offset `0x17d10` and is loaded at `0x53000000`; its extent follows the original reset code's initialized-data copy literals. This is a reset-trace input, not a claimed standalone RTOS partition boundary.

Actual GR IV verification progressed through the original MMU setup, initialized-data/BSS setup, three-core SRAM rendezvous, RTOS SVC/task startup and real timer IRQ handling. The board provides three secure Cortex-A7 cores, development RAM, zeroed boot SRAM, a GIC and channel 13 of the Milbeaut peripheral timer. The timer's 6.75 MHz rate is inferred from the original reload literal and elapsed-time conversion. No camera IPC reply or original-screen image is synthesized.

The optional `--bootloader-gic-flag` explicitly sets `0x0100fff8=0xbeefcafe`, the flag checked by the original reset vector before its own GIC setup. Its provenance from a real camera bootloader remains unverified; it is recorded as an intervention. Without secure world the original NSACR access raises an undefined-instruction exception; without the extra cores/SRAM it waits for rendezvous; without timer 13 it aborts at `0x1e0000d0`. Those blockers are addressed by the independent board rather than vendor-byte patches.

Two additional documented peripheral gaps are addressed:

- `0x1b110298` is USB3 glue `USB3_JT_ADJ`: official `sc2000.dtsi` maps the glue block at `0x1b110280`, and `dwc3-sn.c` defines this register at offset `0x18`. The original RTOS writes `0x20` before reading it. Only that four-byte configuration register retains guest-written values; a read before the first write faults because its reset value is unknown. USB transfers and attach/readiness state are absent.
- `0x1d010000` matches ARM's standard `CNTControlBase`: the original function reads `CNTCR` bit 1, counter low/high at `0x8/0xc`, and `CNTFID0` at `0x20`. The board counts actual `QEMU_CLOCK_VIRTUAL` time at 40 MHz, matching the original Ricoh Generic Timer definition and each CPU's explicit `cntfrq=40000000`. QEMU 10.2.4 uses the same clock for architectural counter reads, with a 25 ns tick at this frequency. Starting `CNTCR.EN=1` is recorded as development bootloader configuration; real-camera bootloader state remains unverified. Unknown registers and unsupported frequency-control requests fault.

The latest reviewed five-second trace with the flag still produced **583 SVC exceptions and 2 IRQ exceptions**, and progressed beyond those two former faults. The remaining first hardware fault is now CPU 2 at `0x53acc2dc`, reading `0x28825000`. The original routine at `0x53acc2a0` performs read-modify-write updates of 32 entries, replacing 12-bit fields at base `0x28820000` with bank stride `0x20000`, then updating enable bits. The reviewed official GPL kernel has no matching Socionext register definition for this block. Its reset state and behavior are unknown, so the board leaves the access as a real abort. The vendor exception handler subsequently aborts at address zero. The complete peripheral behavior, Linux/RTOS IPC link and original display remain unimplemented. **This is not a verified original UI boot.**

GR IV 原 RTOS 已实际越过 MMU、三核同步、任务启动及计时器 IRQ，并通过已确认的 USB3 配置寄存器与 ARM 40 MHz 系统计数器。当前明确卡在 `0x53acc2dc` 对未知外围块 `0x28825000` 的首次读取；其读改写需要真实复位值与寄存器行为，现有官方源码未提供。上述 bootloader 标志和计数器初始启用均明确记录，未伪造 IPC 回复或相机画面。原厂 UI 仍未验证。

## Provenance / 来源

The board and builder are independently authored. USIO register behavior and vendor GIC startup requirements were checked against [Ricoh's official GPL source packages](https://www.ricoh-imaging.co.jp/english/products/oss/) (GRIIIx 1.21 package SHA-256 `9612dc9077be83cbccaf9b9553332806a197b0e9fb92cf74c3670557803583b9`). No vendor kernel code or firmware bytes are included.

Standard system-counter offsets and EN/HDBG bits were checked against [ARM's CSAL register definitions](https://github.com/ARM-software/CSAL/blob/5e7d85601f5744726037ddca37c6cbfd97ccbc23/include/csregisters.h). Only the architectural facts were used; its source is not copied into the board.

Earlier public [GR IIIx firmware exploration](https://github.com/hhornbacher/gr3x-fw-hack/tree/dc84ad50a0e5afc86d7273eb75e86ff889b83b19) identified the split Linux services and shared-memory/IPCU dependencies. Its unlicensed mock-library source is not copied or installed here.

The C board and DTS are GPL-2.0-or-later. QEMU's original license notices remain in the local build tree. Download firmware directly from the official [GR III](https://www.ricoh-imaging.co.jp/english/support/digital/gr3_s.html), [GR IIIx](https://www.ricoh-imaging.co.jp/english/support/digital/gr3x_s.html) and [GR IV](https://www.ricoh-imaging.co.jp/english/support/digital/gr4_s.html) support pages.
