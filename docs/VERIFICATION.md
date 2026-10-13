# Verification record / 验证记录

## Ricoh GR IV original UI integration — 2026-10-11

The exact official GR IV 1.11 BIN passed a fresh workbench-library import and
offline extraction. Both the Windows source Qt workbench and rebuilt portable
EXE run the original TopMenu, complete native Confirm into Focus settings and
Back, and stop their owned QEMU processes cleanly. Their 720 × 480 PNGs are
byte-identical. The combined host gate passes **145 tests, with 3 skipped**.
[Host evidence](ricoh-griv-host-ui-verification-20261011.json),
[native menu evidence](../scripts/boards/ricoh/evidence/griv-1.11-menu-roundtrip.json).

This is assisted original-component startup with software display and explicit
transition completion. Full camera cold boot, physical LCD, animation timing,
capture and Linux/RTOS IPC remain unverified.

理光 GR IV 1.11 已从官方 BIN 重新完成导入和解包。Windows 源码工作台与重新构建的
便携 EXE 均能显示原厂菜单、确认进入 Focus 设置并返回，关闭时正常回收自己的虚拟机。
两版截图字节一致，相关测试 145 项通过、3 项跳过。辅助启动与完整硬件启动的范围见上述证据。

### Ricoh repeat-launch repair

A repeated launch exposed `ENODATA` when Windows-backed WSL storage was read
while QEMU appended its log. Active guest files now live on Linux temporary
storage. Bounded artifacts are copied and hash-checked after the owned guest
has exited. Source Qt and the rebuilt Fix08 EXE both passed native menu input,
frame export and cleanup again. This repairs host transport reliability;
the assisted component and hardware limits above are unchanged.

重复启动时发现 WSL 读取 Windows 目录内正在写入的日志可能报 `ENODATA`。
运行文件已改放 Linux 临时目录，客体退出后再限量导出并校验哈希；源码版和
Fix08 便携版均已重新通过菜单交互、截图导出及清理验证。

## Sigma fp 5.02 MAIN extraction — 2026-10-11

The public offline extractor processed the official 25,692,160-byte update
without changing its SHA-256. Its 49,481,728-byte `main.bin` has SHA-256
`acead79a338de2d8d7485d036b0a020b8eebcbad29d05741540130c6acb75bfb`.
The two declared DFI ranges match an independent execution of the original
IPL decoder, including zero-initialized history and codecs 1 / 0x11. Outer and
packed byte SUM checks pass; expanded padding outside the declared ranges is
excluded. The importer accepts only the reviewed input fingerprint.
The Sigma parser and existing inspection tests pass **51 cases**. This gate
verifies extraction only; successful RTOS and original UI startup remain pending.

适马 fp 5.02 已通过公开离线解包流程，完整主程序与执行原厂 IPL 解压函数的结果一致；
原升级文件未变，相关解析与分析测试 51 项通过。此项只证明解包成功，原厂界面仍待验证。

## GoPro original UI integration — 2026-10-11

**GoPro HERO8 Black 2.51:** the original Tara shooting screen opens its VIDEO
preset menu and returns through native Back, three consecutive times in the
same QEMU instance. The returned RGBA frame exactly matches the initial frame.
The adapter follows the original per-frame Canvas lifetime and calls the original
garbage collector; it does not draw replacement widgets or write selection state.
The exact official ZIP or DATA.bin is imported without modifying its source.
[Input hashes, interventions and native frame evidence](../scripts/boards/gopro/evidence/hero8-2.51-tara-ui.json).

The actual Windows source Qt workbench starts this public runner, captures the
visually reviewed original screen and cancels cleanly: transport exit 0, owned
guest reaped, no cleanup errors. The related importer, runner, routing and library
tests pass **93 cases**. This is an assisted original component, with a combined
1058 × 540 Tara viewport; full RTOS cold boot, physical LCD, sensor and recording
remain unverified. Build/setup instructions are in the
[GoPro board guide](../scripts/boards/gopro/README.md).

The rebuilt portable Windows EXE separately passes the same launch/cancel check
with `frozen: true`; its guest PNG is byte-identical to the source workbench
capture. [Host integration evidence](gopro-hero8-host-ui-verification-20261011.json).

GoPro HERO8 2.51 原厂拍摄界面已在同一 QEMU 中连续三次完成“VIDEO 预设菜单 → 返回”，
返回帧与初始帧完全一致。修复了适配循环遗漏的原厂对象回收和临时画布生命周期。
实际 Windows 源码工作台启动、截图和退出清理通过，相关测试 93 项通过。
重新构建的便携 EXE 也通过同样验证，原厂画面截图与源码版字节一致。
这是原厂组件辅助启动；完整 RTOS 冷启动、实机屏幕、传感器与录像尚未验证。

## Additional original UI verification — 2026-10-11

- **Canon EOS 70D 1.1.2:** original ROM reset boot displays the original
  720 × 480 settings menu. Native Down changes the selected row and Up restores
  the exact original frame. The production runner and actual Qt workbench
  both start the same menu and reap their owned guests on close. The fixed
  qemu-eos board provides emulated peripherals and recorded MPU responses;
  no new firmware instruction patch or assisted GUI function call is used.
  Requires the exact full ROM0/ROM1/serial-flash set. Deeper subpages, tab
  navigation, live view and capture remain unverified.
  [Exact input and frame evidence](../scripts/boards/canon/70d-112-ui-verification.json).
- **Nikon Z50 C2.60:** original MY MENU → Add items → MY MENU in one system
  QEMU instance, through both the probe and interactive HTTP controls. Public
  source build, production import, GUI routing and owned cleanup verified.
  [Exact evidence and interventions](nikon-z50-lab-verification-20261011.json).
- **Insta360 X3 v1.1.4.1_build1:** original 352 × 476 emWin resolution menu;
  native touch changes 72MP → 18MP → 72MP in one instance. Full initial RGB
  and selector roundtrip hashes match the visually inspected original frames.
  The workbench GUI preserves the same pixels across a 5.265-second check and
  cancels cleanly. [Exact evidence](insta360-x3-ui-verification-20261011.json).
- **Sony A7 IV 2.00:** original diagnostic widgets, icon and native font render
  actual 640 × 480 pixels; two pointer events change the original framebuffer.
  This separate component experiment uses software peripherals and developer
  supplied diagnostic text. [Exact evidence and limits](sony-a7iv-diagnostic-ui-verification-20261011.json).
- **Sony A7R V 4.00:** separate original diagnostic widgets, icon and native
  font render actual 640 × 480 pixels; two original pointer callbacks change
  the framebuffer. All 29 graphics requests complete successfully.
  [Exact evidence and limits](sony-a7rv-diagnostic-ui-verification-20261011.json).
- **Ricoh GR IV 1.11:** original 720 × 480 Focus Settings TopMenu, fonts and
  icons in an assisted system-QEMU RTOS component. Native Down changes the
  selection pixels; Up restores the frame. Confirm enters the original Focus
  settings page and native Back restores the initial raw frame exactly. The
  full 14,480,480-byte icon copy and eight original resource copies match their
  source hashes. A finite probe and an interrupted viewer reap their owned
  guests. [Exact evidence and limits](../scripts/boards/ricoh/evidence/griv-1.11-menu-roundtrip.json).

X3 uses eight explicit RAM platform hooks (68 bytes) and synchronous original
handlers. Full ThreadX cold boot, camera pipeline and capture are unverified.
Z50 also uses assisted original-function startup. Neither result establishes
complete hardware emulation. Sony's normal camera menus and complete boot
remain unverified; these diagnostic experiments are not integrated into
the workbench's Original UI lab button. Ricoh's integrated workbench/CLI component
uses a software display and calls the original list transition-completion API;
full animation timers, physical LCD, camera cold boot, imaging and Linux/RTOS
IPC remain unverified. An earlier background AppMain data abort is preserved
in the historical evidence. The current public roundtrip records no data abort
after constructing the original default DateTime object for its log cache;
this component result does not establish a complete camera boot.

新增 Z50 与 X3 原厂 UI 及同一虚拟机输入往返验证。二者均属于辅助启动；X3
保留原厂控件、字体与事件处理，使用明确记录的内存适配层。完整开机和拍摄
尚未实现。未将 Linux shell、模拟画面或绘图空操作计作原厂 UI 成功。
索尼 A7 IV 与 A7R V 另已验证原厂诊断控件、图标、字体及指针输入出帧；诊断文字
由开发者提供。普通相机菜单与完整开机未验证，这些实验尚未接入工作台按钮。
理光 GR IV 组件已显示原厂 Focus Settings 菜单、字体与图标，完成原生上下键、确认
进入设置页与返回的像素往返，并接入工作台按钮。原厂默认 DateTime 构造函数修复了
日志缓存依赖，当前公开往返测试未发生数据异常；历史异常记录保留。辅助接口调用原
列表完成函数关闭过渡动画；完整计时、物理 LCD、冷启动、拍摄与跨核 IPC 未验证。
佳能 70D 1.1.2 已从原厂 ROM reset 启动并显示原厂设置菜单，原生上下键像素往返、
实际 Qt 工作台启动和取消清理均通过。使用固定 qemu-eos 外设和 MPU 响应模拟；
本次未新增固件指令补丁或直接调用 GUI 入口。完整 ROM 必须另行提供；子页面、
标签切换、取景和拍摄尚未通过。

## Canon integration host gate — 2026-10-11

**710 passed, 5 skipped** on Windows after integrating the Canon original-menu
runner, CLI and workbench routing. The skipped cases require separately supplied
firmware or POSIX/QEMU environments. The actual source GUI displayed the original
Canon menu and stopped with transport exit 0, the guest reaped and no cleanup
errors. The rebuilt Windows portable EXE independently repeats this check with
`frozen: true`, using its bundled Canon runner. Its PNG is byte-for-byte identical
to the visually inspected source-GUI PNG; its owned guest exits cleanly.
Proprietary ROM bytes remain outside the public source and packages.

佳能运行器、CLI 和工作台路由合并后，Windows 主机测试 **710 项通过、5 项跳过**。
实际源码 GUI 显示原厂菜单，关闭后传输退出码为 0、guest 已回收且无清理错误。
重新构建的 Windows 便携 EXE 使用随包运行器独立复现，PNG 与已目视核验的源码 GUI
截图字节完全一致，关闭后正常回收 guest。
专有 ROM 未加入公开源码或安装包。

## Previous host regression gate — 2026-10-11

**631 passed, 5 skipped** on Windows. The skipped cases require a user-owned
Canon updater fixture or separate POSIX/QEMU environments; actual UI evidence
is recorded independently above. The updated source and board resources are
packaged with the existing verified portable EXE, whose bytes are unchanged.

最终 Windows 主机测试 **631 项通过、5 项跳过**；跳过项依赖用户提供的佳能升级器或
单独的 POSIX/QEMU 环境。真实 UI 的验证范围分别见上述证据。此次更新公开源码、
板级资源与文档，沿用已验证且字节未变的便携 EXE。

## Earlier QEMU Everything v0.3.0 board gate — 2026-10-11

The earlier board gate passed **493 host tests, with 2 skipped**. Nikon Z30 C1.20
and Z6 C3.80 display original menus with assisted startup and verified navigation.
Sony A7 IV 2.00 / A7R V 4.00, Ricoh GR III 2.10 / IIIx 1.60 / IV 1.11 and
Insta360 X3 v1.1.4.1_build1 boot their original Linux/rootfs into root shells;
their camera UI remains unverified. Canon EOS 50D's original updater executes,
but its shooting UI still requires ROM dumps. Exact runs, interventions and
remaining faults are in [current board evidence](camera-board-verification-20261011.json).

The final installed wheel finds all six board-resource families outside the
source checkout. The final portable EXE separately launches the Z6 menu and
GR IV root shell, preserves source hashes and reaps both guests on close. Its
board documentation links and source/license bundle are checked independently.

此前板级关卡 **493 项测试通过，2 项跳过**。Z30/Z6 已辅助启动原厂菜单并验证导航；
上述索尼、理光及 X3 已启动原厂 Linux/rootfs 和 root shell，相机 UI 尚未验证。
佳能 50D 已执行原厂升级器，拍摄 UI 仍需 ROM 转储；具体证据见上述记录。

### Earlier generic-workbench baseline / 较早的通用工作台基线

- 146 host tests pass under Windows / Python 3.11.8 / PySide6 6.9.3. New tests cover bounded image identification, malformed headers, CRCs, generic boot validation, architecture mismatch, qcow2 external dependencies, library preservation, support exports, runtime selection, and camera/generic GUI separation.
- `scripts/verify_generic.py` imported a project-authored 512-byte x86 BIOS image, selected the PC disk profile and booted it in the installed QEMU. Serial output contained `QEMU_EVERYTHING_BOOT_OK`. The source and copied disk hashes remained unchanged. The Qt session separately received the marker, stopped its QEMU process and released its guest lock.
- ARM64 `virt`, ARM32 `virt`, x86-64 `pc` and RISC-V 64 `virt` accepted their configured machine/CPU/display/input options in real QEMU initialization checks. This establishes argument compatibility, not a completed firmware boot for all four architectures.
- At this earlier baseline, a local Sony A7R V 4.00 DAT (754,525,608 bytes; SHA-256 `51e1176a255a9a4b4782dfbcfa6c5f332b71173bdfe9ed2bcf4fca6bcbc6c2e2`) was identified and imported without modifying its source. Kernel startup and the separate diagnostic GUI component were subsequently verified above; normal camera menus remain unverified.
- The preserved Hasselblad X2D II 1.3.16.2 adapter displayed the original main menu under the new workbench. The guest frame and workbench screenshot were visually reviewed; the session shut down normally.
- The Windows portable executable also completed its camera UI smoke check with `ok: true` and `frozen: true`; its original guest image was visually reviewed. The source workbench's Chinese platform-support page was checked for readable text and accessible configuration controls.

中文摘要：通用导入、启动配置、Qt 会话与停止流程已验证；四种 QEMU 平台的参数可初始化。
自写 x86 测试镜像实际启动成功，哈苏原厂主菜单复测正常。当时索尼升级包仅分析，
没有宣称所有固件或所有专有硬件可直接启动。测试不需要附带厂商固件。

Host: Windows x64 on AMD x86-64. Python 3.12, PySide6 6.9.3, QEMU Windows build
20260811, Android API 28 ARM64 image revision 2, Android NDK r27.
Recorded: 2026-09-29. These checks use local virtual machines only.

## Firmware profiles

| Firmware | SHA-256 of official input | Verified |
| --- | --- | --- |
| 1.2.7.16 | `68aa9bce63c303a7d13ca4c14aae76be935925d596094bdcb92549cff42e1bc1` | Import, original UI, main menu, mouse tap to Display submenu, frame capture, shutdown |
| 1.3.16.2 | `9789d6a9843d388034bc12341ad9a97585c25cb98cf11a0da8b768399b4842f6` | Import, original UI, main menu, mouse tap to Display submenu, frame capture, shutdown; compiled Hello app |

Both packages were imported from `.cim` using the new pipeline, without reusing
the older experiment's extracted system directories. QEMU, SDK image and platform
tools were downloaded, checksum-verified and extracted by the new runtime setup.
No preinstalled QEMU/Android runtime was required for those checks.

The portable PyInstaller EXE also started the original 1.2.7.16 UI and captured
a valid guest frame with `frozen: true`; Python was provided by the distribution.
A second portable EXE check imported the original `.cim` into a fresh data
directory, prepared new guest disks, displayed a valid frame and exited cleanly.

The original and adapted ELF `.text` and `.plt` sections compare byte for byte
identical for both versions. Only the two intended embedded QML strings differ.
The original `.cim` files remain unchanged.

## Automated checks

23 unit tests cover Windows path traversal/device names/ADS, archive collisions,
full OTA block reconstruction and malformed/truncated input, cancellation,
loopback-only QEMU transport, guest disk locking, and fragmented/invalid frame
streams. Unit tests do not require or download proprietary firmware.

The real guest integration script checks that a valid frame arrives and captures
it for visual inspection. The captured original UI showed the Display submenu;
the guest app showed the expected `HELLO X2D` drawing. Guest shutdown completed.

## Limits of this evidence

No claims are made about physical camera operation, real capture/AF/ISP, GPU
acceleration, other firmware revisions, or correctness of every original menu.
Menu startup and navigation success do not imply all underlying services work.
No input-to-display latency or FPS guarantee is made.

中文摘要：两个官方固件版本均从 `.cim` 导入并实际启动过原厂界面，点击进入
Display 子菜单；1.3.16.2 还验证了 NDK 编译示例并在虚拟机显示。23 项自动测试
通过。相机实机功能、硬件加速和其他固件版本未验证。

## Multi-model preview — 2026-09-30

The Windows source build imports 14 distinct known package hashes (the X1D II and CFV II 1.5.2 downloads share one hash). Each input is supplied by the user. This adds no physical-camera control.

| Profile | Versions | Checks |
| --- | --- | --- |
| X1D 50C | 1.20.0, 1.21.0, 1.25.0 | Legacy CIM/rootfs import, original UI in Linux ARMhf/Qt 5.15, 640×480 main menu, virtual F1; General Settings navigation on 1.21.0 and 1.25.0 |
| X1D II 50C | 1.0.1, 1.0.2, 1.1.0, 1.2.0, 1.3.0, 1.4.0, 1.5.2 | Raw Android OTA import, ARM32 UI, upward swipe to main menu, mouse tap to camera settings |
| X2D 100C | 1.0.5, 4.2.0 | Original UI with X2D identity `4.1.0`, Power / Display submenu navigation |
| 907X / CFV II 50C | 1.5.2 | Shared-package model selection, CFV identity `14.1.0`, main menu and Focus settings page |
| X2D II 100C | 1.3.16.2 | Regression check after adding multi-model support; prior 1.2.7.16 evidence remains above |

Three separately compiled Hello clients were run and visually checked: Android ARM32 with 1.5.2, Android ARM64 with X2D 4.2.0, and Linux ARMhf with X1D 1.25.0. The demonstration still draws `HELLO X2D`; it is not a device identity test.

38 host unit tests pass. Added cases cover legacy versus Android CIM IV derivation, shared-package model selection, kernel identity and isolated Linux disk selection, bounded legacy QML adaptation and Linux archive path validation. Linux runtime reconstruction from the pinned cached Debian packages was also exercised.

During verification, earlier Eagle buffers required a byte-stride adapter. Later image review also exposed partial repaint loss: the compositor now retains unchanged pixels, applies Wayland damage regions, and presents committed buffers before releasing them and completing frame callbacks on a 16 ms display tick. Delaying callbacks alone was insufficient. Pending releases follow Wayland resource destruction so a destroyed buffer is not used later, and repeated commits of a pending buffer accumulate their damage. X1D 1.21.0 and X1D II 1.0.2 / 1.4.0 were additionally observed through repeated captures for 34 seconds after readiness, including settings-page interactions. Blank-frame rejection alone is insufficient; retained screenshots were reviewed for complete menus. The original X1D QML root-window adaptation changes a compressed data resource, without altering ELF instructions. `.text` and `.plt` compare byte for byte for all 14 imported profiles.

The Windows portable EXE was also checked: a fresh data directory imported CFV II 1.5.2 and displayed the 907X main menu; the Linux path displayed the X1D 1.25.0 main menu. Both recorded `frozen: true`.

The launcher allows the opening menu transition and asynchronous loaders to settle before readiness. Menu filtering also needs a synthetic body type: firmware Qt metadata defines Xsystem as `1024` and Cfv907x as `1048576`, rather than sequential enum values. The mock supplies these masks to `Seq`, with the GUI exposure capability, while hardware methods remain unsupported. X1D II 1.3.0 Quality and 907X Focus pages were checked after this change.

The firmware/UI ABI paths were tested on Windows. The additional models have not been verified on macOS. No 907X / CFV 100C image was supplied; that model is unsupported. Hardware operations, all menu values, settings persistence, and physical camera behavior remain outside these checks. There is no GPU acceleration claim.
