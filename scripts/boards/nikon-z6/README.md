# Nikon Z6 C3.80 assisted menu

The menu and input execute in the user's original Z6 MAIN. This profile uses
explicit GDB startup/API calls and the upstream GPU surface probe. Natural
power-on, physical LCD scanout, live-view and photography are not verified.

Upstream: [WonderLantern/z6-qemu](https://github.com/WonderLantern/z6-qemu/tree/419a1f2a91a1f2d3a0209027c29606475f7fe26f),
commit `419a1f2a91a1f2d3a0209027c29606475f7fe26f`, GPL-2.0-or-later.
Credits include WonderLantern and the shared Z7 emulator work by R3tr0ooo.
The DevKit runner is AGPL-3.0-or-later supervision glue; it does not ship firmware,
decryption tables or the upstream executable. Consult the external checkout's
COPYING and NOTICE for its licensing and attribution.

## Separate build under Debian / WSL

Review the pinned checkout, then run:

```sh
git clone https://github.com/WonderLantern/z6-qemu /your/z6-qemu
git -C /your/z6-qemu checkout --detach 419a1f2a91a1f2d3a0209027c29606475f7fe26f
bash scripts/boards/nikon-z6/build.sh /your/z6-qemu /your/independent-z6-build /your/qemu-10.2.4.tar.xz
```

Use a new independent build directory. Do not reuse a Z7/Z30 source or binary.
The optional original archive is verified with SHA-256
`821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746`.
The builder copies its executable into the external checkout and records both
the original build path and its SHA-256. All source hashes are checked again
before a guest starts.

Create `nikon-z6-lab.json` in the DevKit data home:

```json
{"revision":"419a1f2a91a1f2d3a0209027c29606475f7fe26f","distro":"Debian","project":"C:/path/to/z6-qemu"}
```

Download Nikon's original `Z_6_0380.bin` locally, import and decrypt it through
the production Nikon extractor with your local tables. `prepare_lab` checks
the exact source, decrypted container and module hashes, all original CRC16s,
and copies MAIN plus its **original** `f92d` CRC tail. It never alters the input.

```sh
python -m devkit.nikon_z6_lab prepare DEVICE --home HOME
python -m devkit.nikon_z6_lab probe DEVICE --home HOME --seconds 60
python -m devkit.nikon_z6_lab run DEVICE --home HOME
```

The interactive server binds only `127.0.0.1:8789`. Arrow keys, Enter and
Escape use original firmware input functions; POST input requires the session
token. Cancellation signals only owned processes. Each run saves its command,
private frames, API audit and cleanup result in the imported device's
`nikon-z6/runs/` directory.

## Menu rendering correction

At the pinned revision, upstream always enables the experimental YUV LCD
scanout. With menu-only initialization and no RAW producer, it replaces the
GPU menu surface with a black screen. This runner omits that experimental
flag and uses the already implemented original-GPU surface probe. The evidence
explicitly records `lcd_scanout_verified:false`; it does not synthesize menus.

The fixed probe runs one guest: Playback menu / Delete → Down / Playback
folder → Up / Delete. It verifies RGB fingerprints against the observed C3.80
profile before reporting success. A measured 32×4 blinking disabled slide-show
marker is excluded from the verification fingerprint; the complete original
frame and its full hash are still saved and displayed without modification.
No camera USB connection is used.
