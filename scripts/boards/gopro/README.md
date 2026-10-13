# GoPro HERO8 original Tara UI laboratory

This runs the **original HERO8 Black HD8.01.02.51.00** ARM32
`RootDialogsTav::ApplicationTav` code, fonts, icons, widget events and rasterizer.
The original shooting screen, VIDEO preset menu and original Back transition are
verified in one guest. This is an **assisted UI component**, not a complete camera
power-on or a working sensor/recording pipeline. The original Tara composition is
1058 × 540 and includes the original auxiliary tool/front/remote regions; the
rear camera display is 480 × 320 within that composition.

中文：运行原厂 Tara 程序、字体、图标、控件与绘图代码，已验证拍摄界面、VIDEO
预设菜单和原生返回键。这里是界面辅助启动实验室；完整 RTOS 开机、传感器、实际拍摄、
RTC、电池及物理 LCD 尚未实现。画面右侧是原固件自带的辅助区域，不是仿制界面。

## Firmware and prerequisites

Download your own firmware from the [official HERO8 update page](https://gopro.com/en/us/update/hero8-black).
Firmware, resource images, vendor binaries and keys are not bundled. The DevKit
imports the exact official 2.51 `UPDATE.zip` or extracted `DATA.bin` and checks its
MILBEAUT volume directory. This runner requires the original RTOS volume:

| Input | Exact value |
| --- | --- |
| Firmware | HD8.01.02.51.00 |
| RTOS volume length | 70,503,296 bytes |
| RTOS SHA-256 | `49e0d40253f517fc6512f4bc9ff59a9d63c9a86e7cd84a6b0c97f523278de2e5` |
| QEMU source | Official QEMU 10.2.4 `qemu-10.2.4.tar.xz` |
| Archive SHA-256 | `821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746` |

Use Linux or WSL with Python 3.12+, a C compiler, Ninja/Meson, QEMU build
dependencies, Clang and `binutils-arm-linux-gnueabihf`. The runner needs only
Python's standard library; PNG output is an encoding of captured native RGBA
bytes. Building never executes extracted vendor/upstream helper binaries.

## Build

From the repository root in Linux/WSL:

```sh
python3 scripts/boards/gopro/build_ui.py \
  --archive /absolute/path/qemu-10.2.4.tar.xz \
  --work-dir /absolute/path/independent-gopro-build --jobs 2
```

Use a dedicated work directory. The builder excludes one known, unused upstream
EDK2 absolute symlink, applies the standard safe tar extraction filter, and writes
its completion marker only after successful extraction. An incomplete existing
source tree must be replaced with a fresh work directory. `gopro-ui-manifest.json`
pins every platform source, the compiled board, the bridge and symbol addresses.

## Run

`run_ui.py` accepts one JSON argument with absolute Linux paths:

```sh
python3 -u -B -I scripts/boards/gopro/run_ui.py \
  '{"manifest":"/absolute/path/gopro-ui-manifest.json","rtos":"/absolute/path/rtos.bin","destination":"/absolute/path/new-run","mode":"probe","seconds":120,"port":8794}'
```

Keep stdin open while the runner owns its guest. Write `stop` or close stdin to
end the owned session. `probe` verifies three shooting → native VIDEO menu →
native Back cycles in the same QEMU process, then exits and reaps it. Failure,
timeouts, invalid input, heap exhaustion and failed native assertions cannot pass
the verification gate. Final evidence is saved as `destination/evidence.json`.

For interaction set `mode` to `interactive` and add `session_seconds` (default
900, maximum 3600). `seconds` remains the 60–180 second startup budget.
Interactive startup verifies one native roundtrip before emitting the loopback
viewer URL. The viewer displays captured guest pixels and sends real native
pointer events. `/status` (`/state` alias) and `/frame.png` are read endpoints;
`/pointer` and `/stop` require the session token and same loopback origin.

The host protocol is JSON lines: `ready {url, session}`, probe-only `result`
with `original_ui_verified`, `native_touch_verified` and
`single_instance_menu_roundtrip_verified`, then `stopped`. Errors are explicit.
No pre-existing process is killed to free the port.

## Recorded interventions and limits

The original firmware file is frozen and SHA checked before execution, then an
owned temporary image receives 20 checked eight-byte branch veneers. The full
address/original/replacement/semantic list is in each evidence record:

- Real 64 MiB bounded allocation and checked calloc; freeing is deferred until
  session cleanup. This is a finite laboratory heap. Repeated menu changes can
  exhaust it before the time limit; a clean new session is then required.
- A real single-active-CPU recursive mutex backend and an actual 40 MHz QEMU
  architected counter. CPU1/2 are parked; proprietary RTOS tasks are bypassed.
- Original formatter output is captured. Hardware/NV errors remain failures.
- Original HERO8 static model record is explicitly selected; original settings
  initialization encounters empty NV and chooses its own ROM defaults.
- A software display copies only the original rendered pixels. No replacement
  menu, text, icon, selection state or screenshot is written into the guest.
- Original CoreRoot cursor time, pointer handlers, EW timers, signals, draw and
  EndUpdate execute. The original Tara garbage collector runs after EndUpdate;
  an original temporary Canvas is created each update, as in the native loop.
- Original data/BSS/MMU initialization is used, with diagnostic RAM banks and
  the original root explicitly retained. The verbose debug flag is disabled;
  original error logging is preserved.

The separate strict original reset attempt still stops at the unimplemented
DRAM-configuration register `0x28828240` (original read at `0xA0000320`). The
component does not pretend that this full power-on, physical DDR wiring, LCD,
battery, RTC, NV persistence, audio, USB, sensor or video capture succeeded.

## Provenance

The board/platform sources here are authored under GPL-2.0-or-later and extend
the official QEMU build. Original firmware remains user supplied. Format research
consulted [Hypoxic/GoPro-Research](https://github.com/hypoxic/GoPro-Research)
at `a9a35a453c6008134c3ac12c9ce0b127cd5360ca` read-only; its unlicensed code was
not copied or executed. Native ABI checks were reconciled with the original
firmware instructions and official [Embedded Wizard main-loop documentation](https://doc.embedded-wizard.de/main-loop).

Verified hashes, native frame/input gates and scope are recorded in
[`evidence/hero8-2.51-tara-ui.json`](evidence/hero8-2.51-tara-ui.json).
