# Third-party notices

The DevKit application is AGPL-3.0-or-later. Copyright © 2026 Derrick Yao.
The MIT theme and existing small MIT example retain their original notices.
This repository's license does not relicense any third-party component.

| Component | License / source |
| --- | --- |
| Material theme, adapted `devkit/theme.py` and chevrons | MIT; Derrick Yao, [tsla-infotainment-lab](https://github.com/derrickyau9/tsla-infotainment-lab/tree/f590d8fd9a053ab072df94b994c0a58ee7b976ed) |
| PySide6 / Qt / Shiboken 6.9.3 | LGPL-3.0 option and component-specific licenses; [Qt source](https://download.qt.io/archive/qt/6.9/6.9.3/) and [PySide source](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.9.3) |
| Dissect extfs 3.15 | AGPL-3.0-or-later; [source](https://github.com/fox-it/dissect.extfs) |
| Dissect cstruct, util | Apache-2.0; [cstruct](https://github.com/fox-it/dissect.cstruct), [util](https://github.com/fox-it/dissect.util) |
| pyfdt 0.3 | Apache-2.0; Copyright 2014 Neil Armstrong; [source](https://github.com/superna9999/pyfdt) |
| pyelftools 0.33 | Public domain; [source](https://github.com/eliben/pyelftools) |
| Brotli 1.2.0 | MIT; [source](https://github.com/google/brotli) |
| cryptography 46.0.3 | Apache-2.0 OR BSD-3-Clause; [source](https://github.com/pyca/cryptography) |
| cffi | MIT; [source](https://foss.heptapod.net/pypy/cffi) |
| Python | PSF license; [source](https://www.python.org/downloads/source/) |
| PyInstaller bootloader | GPL with distribution exception; [source and license](https://pyinstaller.org/en/stable/license.html) |
| Sony fwtool constants in `devkit/camera_formats/_sony_vendor/constants.py` | MIT; Copyright 2015 ma1co; [fwtool.py](https://github.com/ma1co/fwtool.py), [DavidBuchanan314 XR work](https://github.com/DavidBuchanan314/fwtool.py/tree/72fbdd15ddcc2d2a24ccb52cf8e84b8de98c738a); full text in `third-party-licenses/Sony-fwtool-MIT.txt` |
| Canon updater XOR tables and algorithm adapted in `devkit/camera_formats/canon.py` | GPL-2.0-or-later; Copyright 2010 Arm.Indy; [Magic Lantern fir_tool2.py](https://github.com/reticulatedpines/magiclantern_simplified/blob/142472e95054d73640f50789ac0fd66325499d82/tools/indy/fir_tool2.py); full text in `third-party-licenses/Canon-GPL-2.0.txt` |

The portable distribution uses separate Qt DLLs in `_internal/PySide6`. Users may
replace compatible libraries and rebuild the application; no restriction is
imposed on reverse engineering for debugging modifications to LGPL components.
The release also includes the project's corresponding source and build scripts.
Installed wheel license texts are retained under `third-party-licenses`.

## Research references / 研究参考

**Radium Wang / radium-wang** — [Hasselblad X-System CIM Firmware Research & Feature Extensions](https://github.com/radium-wang/Hasselblad-X-System-CIM-Firmware-Research-Feature-Extensions).
This MIT-licensed upstream project (Copyright © 2026 Radium Wang) provided reference
and background for offline CIM firmware analysis and original UI/menu extension
research. This acknowledgement credits that research; it does not imply upstream
authorship or validation of the DevKit's QEMU implementation.

感谢 **Radium Wang / radium-wang** 公开分享哈苏 X 系列 CIM 固件研究及功能扩展项目，
为固件离线分析、原厂 UI 与菜单扩展研究提供参考。上游项目使用 MIT 许可证，
版权归 Radium Wang 所有；此致谢不表示上游作者开发或验证了本 DevKit 的 QEMU 实现。

### Multi-brand camera research / 多品牌相机研究

- **ma1co and DavidBuchanan314**: Sony UFU/FDAT and BIONZ XR research. MIT constants
  retain their license and attribution; the bounded stream and archive handling
  are implemented in this project.
- **WonderLantern**: [nikon-fw-key-tool](https://github.com/WonderLantern/nikon-fw-key-tool)
  (GPL-3.0), [z7-qemu](https://github.com/WonderLantern/z7-qemu) and
  [z30-qemu](https://github.com/WonderLantern/z30-qemu) and
  [z6-qemu](https://github.com/WonderLantern/z6-qemu/tree/419a1f2a91a1f2d3a0209027c29606475f7fe26f)
  (GPL-2.0-or-later). The independently implemented package reader uses tables
  supplied separately by the user. The key tool and custom QEMU are not bundled.
  These menu labs use assisted startup; their research is not credited as complete camera
  hardware emulation or an unmodified power-on path.
- **yeahnope** / [gr_unpack](https://github.com/yeahnope/gr_unpack) and
  **hhornbacher** / [gr3x-fw-hack](https://github.com/hhornbacher/gr3x-fw-hack): Ricoh
  frame-container and Linux/IPC research. No unlicensed source was copied;
  the bounded decoder and structural ROMFS extraction here are independent.
- **Radium Wang** / [Ricoh GR IV firmware analysis and feature expansion](https://github.com/radium-wang/ricoh-gr4-firmware-analysis-and-feature-expansion):
  GR IV research is credited alongside the retained Hasselblad credit. Current
  new material uses the GR IV Project Noncommercial Source License 1.0; older
  revisions through `a55a2c7` retain their Apache-2.0 grants. No restricted material
  was copied into this project.
- **Arm.Indy / Magic Lantern / reticulatedpines**: Canon FIR and EOS emulator
  research. Adapted updater code retains GPL-2.0-or-later and attribution; the
  project supplies corresponding source. The combined AGPL-3.0-or-later project
  uses the compatible GPL-3.0 option for that component.
- **peva3** / [magiclantern_70D](https://github.com/peva3/magiclantern_70D/tree/302806f5fb58a52ce458f65fb9612430ea0cf589):
  Canon EOS 70D ROM-layout, hardware and emulator research informed the separate
  1.1.2 original-menu verification. The prior 70D port by **nikfreak** and the
  Magic Lantern/qemu-eos authors are also credited. Canon ROMs and serial-flash
  data remain private research inputs and are not distributed by this project.
- **nneonneo** / [Insta360 X3 Firmware Tools](https://github.com/nneonneo/Insta360-X3-Firmware-Tools):
  X3 container research. No unlicensed source was copied; this project implements
  its own checked component reader.

Fixed research revisions, model-specific results and limitations are recorded in
[docs/CAMERA_FIRMWARE.md](docs/CAMERA_FIRMWARE.md) and `devkit/camera_catalog.json`.
感谢以上作者的公开研究；致谢不表示上游作者验证了本项目的实现。

### Independent board bring-up / 独立板级适配

The GPL-2.0-or-later board overlays in `scripts/boards/sony`, `ricoh` and
`insta360`, and `gopro` are independently implemented here and built against pinned QEMU
10.2.4 source. Builders and corresponding overlay source accompany the portable
app; the custom emulator binaries are built locally and are not bundled.
The Sony register/address and boot handoff analysis was checked against
[Sony's official ILCE-7M4 Ver.2.00 GPL source](https://oss.sony.net/Products/Linux/DI/ILCE-7M4_Ver200.html).
Downloaded vendor GPL source remains a separate research input; these board
models do not distribute proprietary camera drivers, encryption keys or firmware.
The independently reconstructed Sony resource codec `scripts/boards/sony/ldec.c`
and `ldec.h` use AGPL-3.0-or-later; see their separate verified-format description
in [README_LDEC.md](scripts/boards/sony/README_LDEC.md).

**marin-m / vmlinux-to-elf**, GPL-3.0, revision
`19683fb95b29cd31362d49e6f48ab8368f96cbdf`, was used separately for local kernel
symbol analysis. Its code and recovered proprietary kernel images are not
bundled. **Unicorn 2.1.4**, GPL-2.0, is an optional local Canon algorithm
verification dependency (`pip install .[canon-lab]`), not a camera emulator or a
required dependency of the portable workbench.

## Downloaded separately, not bundled in the portable app

- **QEMU**, GPL-2.0 and other included licenses, downloaded from [Stefan Weil](https://qemu.weilnetz.de/w64/). The extracted download includes its license and source information. [QEMU source](https://gitlab.com/qemu-project/qemu).
- **7-Zip / 7zr**, LGPL/BSD/unRAR restrictions or public domain as applicable, downloaded from [official release 26.03](https://github.com/ip7z/7zip/releases/tag/26.03). Its extracted license files remain with the runtime.
- **Android SDK system image and Platform Tools**, downloaded from Google's official repository after the user accepts the [Android SDK license](https://developer.android.com/studio/terms). [AOSP source](https://source.android.com/).
- **Android NDK**, optional and installed by the developer from [Google](https://developer.android.com/ndk/downloads).

## X1D Linux compatibility runtime

Pinned Debian Bullseye ARMhf packages are downloaded separately from `deb.debian.org`. They include Qt, glibc, Wayland and their dependencies, under each package’s own license. Package versions and hashes are recorded in `devkit/linux-downloads.json`; copyright and source references are retained in the runtime’s `/usr/share/doc/<package>/copyright` files. Debian source packages are available through [Debian Sources](https://sources.debian.org/). No Debian maintainer scripts run on the host.

## User-supplied firmware

The independent Sigma fp 5.02 DFI reader was checked against execution of the
original user-supplied IPL decoder. **ijigen / fpSup**, GPL-3.0, revision
`4e957b41e276e14f89472457cb3e7547ba62acf1`, provided native memory and function
references used to cross-check the extracted MAIN image; its source is not copied.
[Upstream research](https://github.com/ijigen/fpSup).

Hasselblad/DJI, Nikon, Sony, Ricoh, Canon, Insta360, GoPro and Sigma firmware binaries,
Qt assets embedded in them, proprietary libraries,
fonts and artwork are not included in the repository or release. Their respective
licenses and ownership remain with their owners. Import processes only a local
user-supplied package. Screenshots illustrating compatibility are not a firmware
distribution. Hasselblad and X2D II names identify the compatible product and do
not imply affiliation or endorsement.
