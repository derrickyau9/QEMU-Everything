# Nikon Z50 C2.60 assisted original menu

This is an independent QEMU Everything compatibility port of [WonderLantern/z30-qemu](https://github.com/WonderLantern/z30-qemu) at commit `0ba0c3547ccbec09e7c861eaca35db8e82795f15`. The upstream graphics implementation in that snapshot derives from its Z7 work. Original GPL-2.0-or-later notices are retained; `COPYING` contains GPL version 2. Public `source/` contains source code only, with no Nikon firmware or decryption keys. This project does not claim that upstream supports Z50.

## Scope and evidence

Verified with the exact official `Z_50_0260.bin`, SHA-256 `69745c9e8f3156e936918e62a4a1fad18e7078e9ea3b95d8b8e08144b1542e49`:

- Original reset code reaches the native InitTask checkpoint `0x100531bc` in the compatibility board.
- The original allocator, primary/auxiliary surface initialization and menu APIs produce real stock 720x480 pixels.
- One guest opens **MY MENU**, enters **Add items**, and returns to **MY MENU** through native input dispatch. The runner accepts only the measured full-frame hashes and saves the complete original PPM and PNG frames without masking or repainting them.
- The firmware file is not patched. The original CRC16 bytes `93c5` are recovered from the verified decrypted container; the complete MAIN SHA-256 is `57e834fac8f921c18c99abe0b1469edbaca71cb1957794bb9f138bd223d30fc1`.

**This is GDB-assisted menu execution.** Natural power-on, physical LCD scanout, live view, sensor operation and photography remain unverified. The inherited broad sparse RAM map is a research workspace, not a measurement of physical camera RAM. UART silence alone is not taken as a failure to execute CPU instructions.

At the checkpoint, the runner explicitly calls native `0x1012177a` with a temporary **12-byte zero configuration (profile 0)**, restoring the stack scratch afterwards. It then calls native `0x1041178a`, `0x10411c10`, and `0x10208d02(0xc1833138, 1)`. This is synthetic guest configuration and is recorded in the audit; no host-created menu pixels or manually assigned framebuffer addresses are substituted. Input uses original `0x1038449a`, with event IDs recovered from the live original dispatch table: up `242`, down `243`, left `247`, right `248`, back `249`, OK `24a` (hex).

The independent board adds only the observed Z50 C2.60 configuration RMW registers `0x0428003c/40` and display component enable latches (`0x20144000..20147000`). Enable reads retain the written bit; no interrupt is invented. Most hardware semantics and historical PC comments below those explicit changes remain inherited upstream hypotheses. Unknown unmodeled MMIO still stops the guest instead of silently claiming support.

## Build separately (Linux / WSL Debian)

Install a C compiler, Ninja, Meson, Python 3, pkg-config, GLib, Pixman and libfdt development packages. The script runs Python in isolation, verifies the shipped source hashes, uses **two build jobs**, requires new independent profile/work directories, and verifies the original QEMU 10.2.4 archive:

```sh
bash scripts/boards/nikon-z50/build.sh   /mnt/c/Users/YOU/AppData/Local/X2DII-DevKit/camera-boards/nikon-z50-c260-1   /home/YOU/.cache/qemu-everything/z50-build
```

Optional third argument: existing original `qemu-10.2.4.tar.xz`, SHA-256 `821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746`. Otherwise the builder downloads and verifies it. Existing profiles/build directories are refused; other Nikon binaries are not changed.

Configure `HOME/nikon-z50-lab.json`:

```json
{
  "distro": "Debian",
  "revision": "0ba0c3547ccbec09e7c861eaca35db8e82795f15",
  "adapter_revision": "z50-c260-1",
  "project": "C:/Users/YOU/AppData/Local/X2DII-DevKit/camera-boards/nikon-z50-c260-1"
}
```

Use the application to import and decrypt your own exact official update, with locally supplied Nikon XOR tables. Then `python -m devkit.nikon_z50_lab prepare DEVICE --home HOME` restores the CRC-checked original MAIN. `python -m devkit.nikon_z50_lab probe DEVICE --home HOME --seconds 60` performs the bounded original menu roundtrip. `qemu-everything --home HOME nikon-lab DEVICE` or the GUI **Nikon menu lab** starts the original menu at `http://127.0.0.1:8790`. Arrow keys, Enter and Escape dispatch native menu input. Cancel/Ctrl+C stops only this runner's owned QEMU process and preserves its evidence.

Launch validates exact source/container/MAIN hashes, all reviewed source hashes and the independently built QEMU manifest/binary. Linked paths, path escapes and unknown prepared MAIN files are refused. The loopback server has a per-run input token; an occupied port is reported without attaching to or stopping its owner. Timeout, cancellation, connection failure and output limits reap only owned processes.
