# Insta360 X3 original UI laboratory / 原厂 UI 实验室

Exact supported image: **X3 v1.1.4.1_build1**. Import and extract your own official
firmware with QEMU Everything. The project contains no manufacturer firmware,
fonts, images or keys.

This profile executes the original ARM32 emWin GUI in system QEMU. Its widgets,
fonts, menus and touch handlers come from `rtos.bin`. The browser displays the
352 × 476 guest framebuffer and forwards pointer events. It does not recreate
the camera screen in HTML.

此配置执行原厂 ARM32 emWin 界面。文字、控件、菜单、触摸处理均来自用户导入的
`rtos.bin`；网页只显示 QEMU 帧缓冲并传递输入。已在同一虚拟机中验证
**72MP → 18MP → 72MP**，并验证项目 GUI 启动、持续显示和取消清理。

## Build and run / 构建与启动

Build in Linux or your explicitly selected WSL distribution. Dependencies:
Python 3.12+ (tar extraction filters), C compiler, Ninja, pkg-config, GLib/GIO
development headers, and `binutils-arm-linux-gnueabihf`.

```sh
python3 -I -B scripts/boards/insta360/build_ui.py --jobs 2
```

The builder downloads QEMU **10.2.4**, verifies its pinned SHA-256, builds this
GPL board, assembles the platform adapter, and writes
`~/.cache/qemu-everything/insta360-x3-ui/build/insta360-ui-build.json`.
`--archive /absolute/path/qemu-10.2.4.tar.xz` supports a previously downloaded
verified archive; `--work-dir` changes the build cache.

Save `insta360-ui-lab.json` in the workbench data directory. For Windows, this
is the existing `%LOCALAPPDATA%\X2DII-DevKit` directory if present, otherwise
`%LOCALAPPDATA%\QEMU-Everything`. Use your own absolute Linux path:

```json
{
  "distro": "Debian",
  "manifest": "/home/YOUR_USER/.cache/qemu-everything/insta360-x3-ui/build/insta360-ui-build.json"
}
```

Select the extracted X3 entry and click **Original UI lab**. The live screen is
served on `http://127.0.0.1:8791`. Click/drag the camera screen; the separate
resolution-menu button requests the original screen handler. Closing/cancelling
the workbench task stops only the QEMU process owned by this session.

也可使用命令行，`DEVICE_DIRECTORY` 是导入条目的完整目录路径：

```sh
python -m devkit.cli insta360-ui-check DEVICE_DIRECTORY --seconds 30
python -m devkit.cli insta360-ui DEVICE_DIRECTORY
```

## Verified scope and interventions / 验证范围与适配

This is **assisted GUI component startup**, not complete ThreadX/SoC boot.
No photography, DSP, sensor, live view, media recording, networking or real
camera connection is implemented. Menu changes exist only in this guest RAM.

这是原厂界面组件的辅助启动；完整 ThreadX 开机、拍摄、DSP、传感器、实时取景和
存储写入均未实现。菜单操作只影响本次虚拟机内存，不连接或刷写真机。

Explicit platform interventions:

- Cortex-A15 executes the original ARMv7 GUI instruction stream. The real H22
  Cortex-A53 board is not reproduced faithfully. Two secondary CPUs stay off.
- A small independent bootstrap sets VFP, stack and an identity MMU, then enters
  original GUI task `0x8ca5f4`.
- Eight RAM hook regions, **68 bytes total**, supply task identity, cooperative
  GUI time/delay, LCD buffer/present, and synchronous delivery to original GUI
  and application handlers. The ThreadX scheduler is bypassed. All hook addresses
  and bytes are recorded in each run's `evidence.json`.
- The native LCD-present callback latches the original framebuffer. The LCD
  origin is preserved; the browser receives unmodified RGB pixels.
- An explicit diagnostic zero/latch peripheral bank remains. It is not a
  functional hardware driver implementation.
- Initial screen `0x1801` is requested through the original native handler.

The original firmware file is snapshotted without alteration and its SHA-256
checked before and after a run. Build artifacts and board sources are checked
against the manifest before launching. Probe evidence includes full PPM/PNG
frames, native input results, all RAM hooks and owned-process cleanup.

The initial full RGB frame has SHA-256
`22da241868da95972d6186d86c8565a67d8dceb3fef7e8ad30736ec3e80cd90b`.
Roundtrip checks use rows 210–329 (the original resolution selector), preserving
but excluding the independently timed original preset-change toast from the
comparison. No pixels are changed or masked in saved/displayed frames.

## Sources and license

QEMU source: <https://download.qemu.org/qemu-10.2.4.tar.xz>, SHA-256
`821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746`.
Board, build/runner and assembly adapter: GPL-2.0-or-later; see `COPYING`.
Firmware research in this profile was performed independently on the user's
official X3 package; no third-party X3 emulator support is claimed.
The surrounding container reader credits
[nneonneo/Insta360-X3-Firmware-Tools](https://github.com/nneonneo/Insta360-X3-Firmware-Tools/tree/3e95fa30f8ad10f374c98b06a950f05eeb294aaf)
for format research, as recorded in the main project credits.
