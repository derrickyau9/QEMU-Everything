# Ricoh GR Linux and original RTOS menu labs / 理光 GR Linux 与原 RTOS 菜单实验

The Linux service-core board starts the **original vendor Linux kernel and rootfs** from GR III 2.10, GR IIIx 1.60 and GR IV 1.11. It is a serial development environment; this profile does not run the camera RTOS/menu or imaging pipeline, and does not produce camera-control IPC replies. A separate GR IV 1.11 RTOS board now runs the **original TopMenu with its real fonts/icons and verified Up/Down, Confirm and Back input**, through the auxiliary software-display route documented below.

Linux 服务核模型启动 GR III 2.10、GR IIIx 1.60、GR IV 1.11 固件内的**原 Linux 内核与原根文件系统**，提供串口开发环境；此配置不运行相机 RTOS 菜单，也不提供成像和相机 IPC 回复。独立 GR IV 1.11 RTOS 模型已通过下述辅助软件显示路线运行**原厂 TopMenu、原字体和图标，并验证上下键、确认与返回输入**。这仍不代表完整相机冷启动和拍摄可用。

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

`original_ui_verified` always remains `false` for the **Linux service-core profile**. A `camctld_ready` file alone is insufficient: both reviewed service-core generations create it in **restrict** mode after their IPCU open failures. The oracle records that distinction. The separate RTOS component has its own native-frame/input verification gate.

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

The reviewed five-second baseline trace with the flag produced **583 SVC exceptions and 2 IRQ exceptions**, and progressed beyond those two former faults. Its first unresolved hardware fault is CPU 2 at `0x53acc2dc`, reading `0x28825000`. The original routine at `0x53acc2a0` performs read-modify-write updates of 32 entries, replacing 12-bit fields at base `0x28820000` with bank stride `0x20000`, then updating enable bits. The reviewed official GPL kernel has no matching Socionext register definition for this block. Its reset state and behavior are unknown, so the baseline leaves the access as a real abort. The vendor exception handler subsequently aborts at address zero. The complete peripheral behavior, Linux/RTOS IPC link and physical display remain unimplemented. **This baseline is not a verified original UI boot.** The explicitly auxiliary menu route below is independently verified.

GR IV 原 RTOS 已实际越过 MMU、三核同步、任务启动及计时器 IRQ，并通过已确认的 USB3 配置寄存器与 ARM 40 MHz 系统计数器。基准冷启动在 `0x53acc2dc` 对未知外围块 `0x28825000` 的首次读取处故障；其真实复位值和行为尚未恢复。上述 bootloader 标志和计数器初始启用均明确记录。基准冷启动未验证 UI；下面的辅助原菜单路线已独立验证。

## Diagnostic continuation / 诊断性继续启动

The separate RTOS board also has three **default-off diagnostic options**. They are intended for investigating later original-code faults, and are not part of verified camera hardware support:

```bash
python3 scripts/boards/ricoh/rtos_probe.py \
  --payload /absolute/path/imported-device/extracted/payload.bin \
  --manifest /absolute/path/ricoh-rtos-build/build-manifest.json \
  --output /absolute/path/new-diagnostic-trace-directory \
  --seconds 45 --bootloader-gic-flag \
  --experimental-latches --experimental-gate-idle \
  --experimental-memory-layout
```

- `experimental-latches` maps specific configuration words/arrays decoded from the original accesses. Unknown offsets still fault. Each first-read zero reset is a hypothesis, not a recovered hardware reset value. It includes documented CM0 GPIO direction/pull registers and PDR write masks, clock stop paired write masks, DMA configuration and EXSTOP configuration. A DMA transfer request is rejected rather than given a fabricated completion. No IPCU mailbox reply or ACK is produced. The additional reload timers count actual virtual time, with inferred source frequencies recorded in the result.
- `experimental-gate-idle` additionally models a disabled configuration gate as idle. This behavior is inferred from the original poll loop and remains unverified against real hardware. It applies only to the seven decoded gate blocks, not unrelated clocks or DMA registers.
- `experimental-memory-layout` provides two independent 1 GiB RAM banks at `0x40000000` and `0xa0000000`. Original pool descriptors at `0x538e3c18..0x538e3c4c` request memory ending at `0x80000000` and a further 1 GiB starting at `0xa0000000`; original MMU sections map these addresses identically. This layout allows diagnosis of the original pools. It does not establish physical DRAM capacity or bank wiring.

With these explicitly recorded board interventions, the **unchanged original GR IV RTOS** printed `A7 RTOS Start!!` and `Gpio For KB636 EVB`, ran the original `BOOT` task (ID `0x01000005`) and printed its own exception report/backtrace at later peripheral faults. Its original C++ ROM contains `TopMenuController`, `DrawString`, `FontManager`, `Canvas`, `ScreenLcd` and `LcdDmaDriver`; a separate Linux GUI executable has not been found. These three board options alone do not establish an original UI frame or input response. The additional execution-snapshot/resource/software-display adapters below enable the separately verified menu component.

原 RTOS 在这些明确记录的实验假设下，已经输出真实启动日志、执行 `BOOT` 任务，并由原厂异常处理器打印调用栈。寄存器复位值、idle 行为和两块内存的物理布局仍有未验证假设；DMA 完成和 IPC 回复没有伪造。仅这三个板级选项不能启动 UI；下述额外辅助适配已运行原菜单并验证输入。

The probe is bounded to 1–60 seconds, hashes the original firmware snapshot and both instruction/serial logs, and owns/cleans up its QEMU process group. Repeated exception addresses are summarized with counts; the original bounded trace preserves their ordering. The optional actual-board regression in `tests/test_ricoh_lab.py` executes an independently authored ARM program to check register-window isolation, GPIO/clock write masks and UART output. It uses no camera firmware and cannot establish UI boot:

```bash
RICOH_RTOS_TEST_MANIFEST=/absolute/path/ricoh-rtos-build/build-manifest.json \
  python3 -m pytest tests/test_ricoh_lab.py -q
```

## Original-resource and software-display component diagnostics / 原资源与软件显示组件

The separate RTOS probe also accepts the following explicitly auxiliary options:

```bash
python3 scripts/boards/ricoh/rtos_probe.py \
  --payload /absolute/path/imported-device/extracted/payload.bin \
  --manifest /absolute/path/ricoh-rtos-build/build-manifest.json \
  --output /absolute/path/new-component-directory --seconds 60 \
  --bootloader-gic-flag --experimental-latches --experimental-gate-idle \
  --experimental-memory-layout --diagnostic-skip-sric-wait \
  --diagnostic-gui-entry --diagnostic-domain-init \
  --diagnostic-empty-domain-storage --diagnostic-resources \
  --diagnostic-ui-component
```

This route is **not an unchanged camera boot**. The owned execution snapshot records every modified address, original/replacement bytes and hash. It bypasses physical hardware startup waits, isolates non-GUI SystemControl hardware/NV event dispatch without generating acknowledgements, and lets missing persistent settings return failure. Original menu, widget, font engine and pixel-drawing instructions are unchanged.

`resources.py` parses the exact GR IV RES bundle with bounded record extents, duplicate-path checks, resource-type checks and per-file hashes. The independently authored `resource_backend.c` supplies a read-only `stat/open/read/close` backend to the original Resource object. Nonexistent files, writes and overreads fail. No original fonts, images, audio or firmware bytes are shipped. In actual firmware runs, the original Resource/font loader read `RT_Latin.bin` and `LE_Latin.bin`; QMP captured their destination RAM and confirmed exact SHA-256 equality with the source files, as it did for the four original WAV resources.

`resource_boot.py` uses original constructors and native APIs to initialize Resource, domain links, FontManager, Latin language selection, TopMenuController and its Show callback. A persistent 720×480 software buffer is allocated by the original heap; original input-buffer bind/activate/conversion APIs create a valid format-5 RGBA DrawSurface. Native `SetRect` and Canvas binding/drawing use that real buffer. This component draw bypasses the absent physical LCD readiness path, while the original physical DeviceFactory remains uninitialized. Original Log threshold API filters debug-level tree logging. These interventions are separately recorded and do not establish camera hardware readiness.

Original icons are a separate `ICONBIN` body of 14,480,480 bytes, followed exactly by the RES bundle. The parser validates the header, complete boundary and all 663 original icon descriptors (662 enabled). A read-only logical storage adapter exposes those real bytes with four-byte blocks; it is not a NAND controller model. Original `SystemControl`/`DataRom` code allocates its own destination and loads the body through that bounded adapter. The original validity marker is set by the original code only after a real successful read. QMP verifies the complete guest destination SHA-256 against the original source. The original glyph blitters remain unchanged.

The oracle pauses the owned guest and captures its actual Canvas/widget geometry, raw RGBA and PNG. It never supplies replacement pixels. **Focus Settings -> native Confirm -> Focus (Auto-area AF, Zone Select AF, etc.) -> native Cancel/Back is verified in the same guest.** Down changes the TopMenu highlight and Up restores the initial raw frame exactly. Confirm calls the original current-controller handler (TopMenu `0x531b38e8`, event `0xe0`); Back calls original `0x53182e9c`. The original AppMain task consumes the original `0x178` transition queue and executes the original Show callback. The harness waits for the original current-ID change and acquires/releases the original UIManager dispatch mutex before drawing that completed page. No target ID, selection, captions or pixels are written by the host.

The harness uses original TopMenu list completion `0x535713bc` to disable its transition animation; full RTOS animation timing remains unverified. A sequence-numbered mailbox is polled with ARM `YIELD`. The same real 720x480 software surface is bound to each original page with native SetRect/bind/draw APIs. The original DateTime constructor `0x538ea670` now supplies its original default object to the ServiceFactory and Log cache used by native package linking; physical RTC initialization is absent. This prevents the previously observed logging NULL fault without suppressing warnings/errors or inventing a camera timestamp. The earlier raw failure evidence remains local.

### Workbench and finite supervisor / 工作台与限时启动

Prepare the reviewed RTOS QEMU build above, then compile the authored UI bridge once (Clang and the ARM cross-binutils are required for this build step):

```bash
python3 scripts/boards/ricoh/build_ui.py \
  --qemu-manifest /absolute/path/ricoh-rtos-build/build-manifest.json \
  --work-dir /absolute/path/new-ricoh-ui-build
```

Alternatively provide `--archive` and `--qemu-work-dir` to build the pinned QEMU as well. The generated `ricoh-ui-manifest.json` pins QEMU, the bridge/backend and every runtime source; it contains no firmware. Configure the workbench home once:

```json
{"distro":"Debian","manifest":"/absolute/path/new-ricoh-ui-build/ricoh-ui-manifest.json"}
```

Save this as `ricoh-ui-lab.json` in the workbench data home. Import the exact official **GR IV1.11** update; the known input is transactionally preserved and decoded. Its SHA-256 is `a2f664dfca034059eb0fd6e18ab08684c326b4a034d85c164dad7e1ec9b5655f`. The Original UI lab selector then offers this assisted menu profile. Other Ricoh versions remain separate extraction/Linux profiles.

The standalone supervisor `run_ui.py` takes one JSON argument with `manifest`, `payload` (the decoded input), `destination` (a new directory), `mode` (`probe`/`interactive`), integer `seconds` (90-180 startup budget), and `port` (8792). It supports Python `-I`, emits bounded JSON ready/result/error/stopped events, and reads `stop` or EOF from stdin. It freezes the QEMU binary, payload and prebuilt platform into owned Linux-local snapshots before launch. Runtime startup requires no compiler.

Active guest images, logs and memory captures stay in Linux-native `/tmp`, including when the workbench data home is on Windows. A concurrent writer/reader diagnostic reproduced WSL DrvFS `ENODATA` in `Path.read_bytes()` for an actively appended log. After the owned guest is confirmed reaped, the supervisor exports closed artifacts to the requested Windows run directory with exact size/SHA-256 verification. Export is bounded to512 files,64MiB per file and256MiB total, rejects links, and preserves failure evidence when cleanup is confirmed. If reap is unconfirmed, artifact export is withheld. Saved host exceptions include a bounded traceback.

The outer `evidence.json` publishes `exported_frames` and frame paths relative to its own directory, so they remain usable after `/tmp` cleanup. Actual runtime paths are retained separately for audit; raw guest JSON/logs are exported byte for byte and retain their original runtime-path history. Qt reads the live frame over the owned loopback viewer before shutdown.

An interactive session expires after `session_seconds` (default900, allowed30-3600) and accepts at most256 commands. The loopback viewer exposes Up/Down, OK and Cancel/Back; its command endpoints require the owned session token. Stop/EOF/cancellation terminate and reap only the owned guest. Instruction traces are capped at16MiB and serial logs at4MiB. `original_ui_verified` requires native Up/Down pixel restoration, an actual new settings-page ID and exact returned pixels, plus the complete original icon-copy hash. A failed gate never becomes a live session.

实测为原厂 Focus Settings 菜单进入 Focus 设置页再原生返回，返回帧逐字节恢复；上下键也改变并恢复原高亮。原 AppMain 自行消费页面队列，harness 只提交输入与绑定真实软件缓冲，不写菜单状态或像素。工作台可用已配置的 GR IV1.11 原厂 UI 入口；会话限时并可取消。完整冷启动、物理 LCD/按键、实时取景、拍摄、Linux/RTOS IPC 和实际相机保存的设置仍未验证。

Source/hash evidence: [evidence/griv-1.11-menu-roundtrip.json](evidence/griv-1.11-menu-roundtrip.json). The earlier Up/Down-only history is preserved in [evidence/griv-1.11-topmenu.json](evidence/griv-1.11-topmenu.json). Firmware, fonts/icons and captured frames remain in the user's private run directory. Every local run records exact snapshot patches, adapter hashes, resource extents, actual copies, native events and frame hashes.

## Provenance / 来源

The board and builder are independently authored. USIO register behavior and vendor GIC startup requirements were checked against [Ricoh's official GPL source packages](https://www.ricoh-imaging.co.jp/english/products/oss/) (GRIIIx 1.21 package SHA-256 `9612dc9077be83cbccaf9b9553332806a197b0e9fb92cf74c3670557803583b9`). No vendor kernel code or firmware bytes are included.

The additional GR IV register definitions were reviewed from the official [GR IV 1.11 GPL package](https://www.ricoh-imaging.co.jp/resources/japan/products/oss/gr-4/GRIV_111.tar.gz), SHA-256 `b3bfb376b0580729ac8fafe0632618095a0bca85a4db19e9d969edc142d40918`, specifically `pinctrl-mlb01-cm0.c`, `clk-mlb01.c`, `milbeaut-hdmac.c`, `mb8ac0300-hdmac.c`, the USB2 DT bindings and IPCU definitions. Only register facts were used in independent device code.

Standard system-counter offsets and EN/HDBG bits were checked against [ARM's CSAL register definitions](https://github.com/ARM-software/CSAL/blob/5e7d85601f5744726037ddca37c6cbfd97ccbc23/include/csregisters.h). Only the architectural facts were used; its source is not copied into the board.

Earlier public [GR IIIx firmware exploration](https://github.com/hhornbacher/gr3x-fw-hack/tree/dc84ad50a0e5afc86d7273eb75e86ff889b83b19) identified the split Linux services and shared-memory/IPCU dependencies. Its unlicensed mock-library source is not copied or installed here.

The C board and DTS are GPL-2.0-or-later. QEMU's original license notices remain in the local build tree. Download firmware directly from the official [GR III](https://www.ricoh-imaging.co.jp/english/support/digital/gr3_s.html), [GR IIIx](https://www.ricoh-imaging.co.jp/english/support/digital/gr3x_s.html) and [GR IV](https://www.ricoh-imaging.co.jp/english/support/digital/gr4_s.html) support pages.
