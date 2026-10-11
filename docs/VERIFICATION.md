# Verification record / 验证记录

## QEMU Everything v0.3.0 — 2026-10-11

The latest board gate passed **493 host tests, with 2 skipped**. Nikon Z30 C1.20
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

最新板级关卡 **493 项测试通过，2 项跳过**。Z30/Z6 已辅助启动原厂菜单并验证导航；
上述索尼、理光及 X3 已启动原厂 Linux/rootfs 和 root shell，相机 UI 尚未验证。
佳能 50D 已执行原厂升级器，拍摄 UI 仍需 ROM 转储；具体证据见上述记录。

### Earlier generic-workbench baseline / 较早的通用工作台基线

- 146 host tests pass under Windows / Python 3.11.8 / PySide6 6.9.3. New tests cover bounded image identification, malformed headers, CRCs, generic boot validation, architecture mismatch, qcow2 external dependencies, library preservation, support exports, runtime selection, and camera/generic GUI separation.
- `scripts/verify_generic.py` imported a project-authored 512-byte x86 BIOS image, selected the PC disk profile and booted it in the installed QEMU. Serial output contained `QEMU_EVERYTHING_BOOT_OK`. The source and copied disk hashes remained unchanged. The Qt session separately received the marker, stopped its QEMU process and released its guest lock.
- ARM64 `virt`, ARM32 `virt`, x86-64 `pc` and RISC-V 64 `virt` accepted their configured machine/CPU/display/input options in real QEMU initialization checks. This establishes argument compatibility, not a completed firmware boot for all four architectures.
- At this earlier baseline, a local Sony A7R V 4.00 DAT (754,525,608 bytes; SHA-256 `51e1176a255a9a4b4782dfbcfa6c5f332b71173bdfe9ed2bcf4fca6bcbc6c2e2`) was identified and imported without modifying its source. Kernel startup was subsequently verified by the board gate above; camera UI is still unverified.
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
