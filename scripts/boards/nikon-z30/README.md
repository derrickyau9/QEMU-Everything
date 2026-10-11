# Nikon Z30 C1.20 original-menu laboratory

This optional profile runs the original Nikon Z30 C1.20 main module on a
separately built camera-specific QEMU machine. The displayed pixels come from
the guest GPU buffers. The profile initializes diagnostic guest state and calls
original firmware menu/input functions using GDB. **Natural power-on, live-view,
sensor operation and photography are not verified.** No firmware or keys ship
with this repository; original firmware remains local and unchanged.

本实验 profile 已验证原厂菜单显示、进入子菜单和返回。启动和输入经过 GDB
辅助，不能作为自然开机、取景或拍摄成功的证据。

## Exact supported input and source

- Official `Z_30_0120.bin`, 34,496,798 bytes; SHA-256
  `1a5449ef505f1f347ed4e066a6a3f588ea542240680e83f4ebcae3b16da3434a`.
- Production extraction must include `decrypted.bin` and CRC-stripped MAIN
  (`module-003.bin`, 33,939,512 bytes). Preparation validates all directory and
  package CRC16 checks, matches the original MAIN at container offset 557268,
  and copies its existing `5bd5` CRC tail. It does not synthesize guessed bytes.
- Restored MAIN: 33,939,514 bytes, SHA-256
  `8a99ced2d8220729ba067587b0624bf30c81757abc5d728768134b1c7fd457fa`.
- Board/API implementation: [WonderLantern/z30-qemu](https://github.com/WonderLantern/z30-qemu/tree/0ba0c3547ccbec09e7c861eaca35db8e82795f15),
  commit `0ba0c3547ccbec09e7c861eaca35db8e82795f15`, GPL-2.0-or-later.
  Its display-session initialization informs this runner, and its original
  guest-call, context-restoration, frame and RSP methods are loaded from that
  separately installed, hash-checked source. The project credits
  [WonderLantern/z7-qemu](https://github.com/WonderLantern/z7-qemu/tree/97abf0b81cd3211f8374a38987a0beaf4549b90f)
  for the earlier board, UART, GIC and paused-call research.
- QEMU 10.2.4 original archive SHA-256
  `821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746`.
  Build manifest and all reviewed board/helper hashes are checked on launch.
  QEMU and upstream source retain their respective licenses.

No other model/version is accepted by this profile. Modern Nikon encrypted
formats are separate research targets.

## Build and configure on Windows + WSL Debian

Use the pinned source checkout and a local original QEMU archive. Do not copy an
already patched Z7 source tree. In WSL, run the checked builder below:

```sh
bash scripts/boards/nikon-z30/build-nikon-z30.sh \
  /mnt/c/path/to/z30-qemu /home/user/qemu-10.2.4.tar.xz \
  /home/user/.cache/qemu-everything/z30-independent-build
```

The separately installed project writes `.local/build-manifest.json` and
`.build/build/qemu-system-arm`. In your QEMU Everything data directory, create
`nikon-z30-lab.json`:

```json
{
  "distro": "Debian",
  "project": "C:\\path\\to\\z30-qemu",
  "revision": "0ba0c3547ccbec09e7c861eaca35db8e82795f15"
}
```

Import the official update and run production extraction using your local Nikon
table JSON. Then, from the repository directory:

```powershell
python -m devkit.nikon_z30_lab prepare C:\path\to\library\1a5449ef505f1f34 --home C:\path\to\data
python -m devkit.nikon_z30_lab probe C:\path\to\library\1a5449ef505f1f34 --home C:\path\to\data --seconds 60
python -m devkit.nikon_z30_lab run C:\path\to\library\1a5449ef505f1f34 --home C:\path\to\data
```

The interactive page listens on `http://127.0.0.1:8788`; direction/Enter/Back
inputs call original firmware APIs. Use Ctrl+C or the workbench Stop action to
stop the owned guest. A busy port is rejected; existing programs are not killed.
The APIs `lab_available`, `prepare_lab`, `run_lab(on_ready=...)` and
`run_menu_probe(seconds=60)` are available for workbench integration.

## Evidence and boundaries

Evidence is saved under each guest's `nikon-z30/runs/<unique-id>`: exact command,
owned PID, guest API calls with return/context restoration, screenshots, original
PPM hashes and cleanup results. The bounded probe requires the verified initial
menu, Add items submenu and restored initial frame in a single QEMU instance.
An empty/flat framebuffer or exit code zero alone cannot establish UI success.

The runner owns every child it creates, cleans up constructor failures and
cancellation, and caps logs/frames to 64 MiB and 512 files per run. It does not
attach any physical camera, network device or host disk. Firmware source,
extraction manifest, module, helper scripts and QEMU binary are checked again
before launch. Symlinks, junctions, hardlinked inputs, escaped paths and changed
prepared files are refused.
