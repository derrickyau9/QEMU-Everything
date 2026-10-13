# Canon EOS offline labs

## EOS 70D 1.1.2 original ROM menu

The exact tested 70D ROM set boots from the original reset vector in the pinned
qemu-eos machine. Its original 720×480 settings menu, firmware-version label,
and Down/Up selection roundtrip have been verified from guest pixels. No
Magic Lantern binary, GDB startup hook or replacement menu is used. The
emulator supplies its existing recorded MPU replies and virtual peripherals;
the temporary BOOTDISK flag is set to zero. This is not a claim of complete
camera hardware, photography or LiveView support.

**Current limits:** only the tested settings-page Up/Down roundtrip is a
verified input gate. Battery-information entry, tab switching and further
submenus have not passed; some may leave the guest waiting. Cancel/restart
the lab to restore its original private ROM copy. The physical camera is
never connected, and changes to temporary ROMs/cards are discarded.

A normal update FIR cannot substitute for the complete ROMs and serial-flash
data. Supply your own files separately; no vendor firmware or ROM is included
or downloaded by this adapter. The exact matching private entry is imported
with:

```sh
python -m devkit.canon_ui import-roms --rom0 /path/ROM0.BIN \
  --rom1 /path/ROM1.BIN --sf /path/SFDATA.BIN --output /path/new-private-entry
```

The reviewed file lengths are 8 MiB, 16 MiB and 16 MiB respectively. Import
checks all three full SHA-256 values, preserves inputs and refuses overwrites,
links and other ROM sets. A separately built qemu-eos is required (see below).
Its build writes `canon-ui-build.json`; create `canon-ui-lab.json` in your
workbench data directory with its `revision`, absolute Linux `qemu` path,
`qemu_sha256`, and an explicit `distro` such as `Debian`.

```sh
python -m devkit.canon_ui check /path/private-entry --home /path/workbench-data --seconds 30
python -m devkit.canon_ui run /path/private-entry --home /path/workbench-data
```

Windows uses the configured WSL distribution. The interactive viewer binds
only `http://127.0.0.1:8793`; `/status` and `/frame.png` expose the same live
guest. Each probe uses one QEMU instance and requires the original menu,
changed Down frame and exact Up return pixels; an exit code or key log alone
does not pass. Cancellation sends a bounded stop to the owned supervisor,
which closes its viewer and reaps its own QEMU process. Other sessions are
not stopped.

The private research input's provenance is the author's documented physical
70D 1.1.2 dump in
[peva3/magiclantern_70D](https://github.com/peva3/magiclantern_70D/tree/302806f5fb58a52ce458f65fb9612430ea0cf589),
commit `302806f5fb58a52ce458f65fb9612430ea0cf589`. That provenance is an author
statement, not Canon authentication. Its proprietary ROM bytes are not
covered by the emulator's GPL license and are not redistributed here.
This success does not establish EOS 50D or other Canon-model UI support.
The [hash-only verification record](70d-112-ui-verification.json) records the
exact sources, guest frames, input sequence and boundaries of this result;
it contains no Canon image, ROM, secret or screenshot bytes.

70D 原厂 ROM 已在固定 qemu-eos 板型中从 reset 启动，实际显示原厂菜单，
上下键选中条像素往返已验证。需要另外提供匹配的自有 ROM0、ROM1 和串行
闪存数据；仅有官方 FIR 升级包不足。电池页、换页和更深子菜单尚未通过，
拍摄、LiveView 及完整硬件均未验证。关闭实验后会删除临时 ROM 和虚拟卡。

## EOS 50D 1.0.9 extraction/updater lab

This adapter uses [qemu-eos](https://github.com/reticulatedpines/qemu-eos/tree/4b667a1d3c08ab7a55835d15ddbd884fa754946d), pinned to commit `4b667a1d3c08ab7a55835d15ddbd884fa754946d` (GPL-2.0-or-later). Stock QEMU does not implement its `50D` machine. The independent build below runs in Windows WSL Debian or Linux and does not replace another camera's QEMU.

```sh
bash scripts/boards/canon/build-canon-eos.sh
```

Build prerequisites are Git, Python 3, GCC/G++, Make, pkg-config, GLib/Pixman development files and libfdt. The tested local build used Debian GCC 14 and Python 3.13. It has a VNC display backend; it does not require SDL/GTK. Firmware and ROMs are not bundled.

## User-owned ROM import / 自有 ROM 导入

A normal firmware update does **not** contain the complete ROM pair needed for original boot. EOS 50D requires an exact 4 MiB `ROM0.BIN` plus 8 MiB `ROM1.BIN`. The boot and exception-vector region is in ROM1's final 64 KiB. For the tested 1.0.9 update, the updater copies a 32-byte decryption seed relative to a header at physical address `0xF87C0000` (ROM1 offset `0x7C0000`). These missing bytes cannot be recovered by padding the update with zeros/FF.

Use files you already own. No physical camera action is part of this lab. Create an empty output directory first; existing files, links and wrong sizes are rejected.

```sh
python -m devkit.canon_lab inspect-roms --rom0 /path/ROM0.BIN --rom1 /path/ROM1.BIN
python -m devkit.canon_lab import-roms --rom0 /path/ROM0.BIN --rom1 /path/ROM1.BIN --output /path/empty-canon-workspace
```

Size/key-header validation is not model authentication or proof of boot. The matching seed is verified by the FIR's encrypted-key digest during payload decryption.

## Optional full payload decryption / 主 payload 解密

Only the official EOS 50D **1.0.9** FIR with SHA-256 `68a223845bd23a4aa35509ca3454f68a6128ef68c0f7d3dcc31ffa470690d298` is accepted. The independently inspected updater hash is `fa7640db657dee18959259c4a285fefc9050918332cb844c67bb266cf4bab711`. An optional bounded Unicorn dependency performs its seed-mixing function as guest ARM code; no Canon native executable runs on the host.

```sh
python -m pip install unicorn==2.1.4
python -m devkit.canon_lab decrypt /path/50D00109.FIR --rom1 /path/ROM1.BIN --output /path/empty-payload-directory
# Alternatively: --seed-file /path/user-owned-exactly-32-byte-seed.bin
```

The implementation requires a matching user seed, verifies the decrypted key digest, payload checksum and record bounds before writing. It writes fixed `patch-NNN.bin` names and a report without secret bytes. Successful extraction still does not prove a complete original ROM or camera UI; flash-patch application is not implemented. A wrong seed fails with no output files. This optional path has corruption/cancellation/synthetic cipher tests; a real main-payload success has not been verified because a user-owned 50D ROM is not available locally.

## Original boot plan / 原厂启动命令预检

Supply **virtual** SD/CF image files. The images use temporary QEMU snapshots and networking is disabled. Do not provide physical disks.

```sh
python -m devkit.canon_lab plan-boot --workspace /path/canon-workspace \
  --qemu "$HOME/.cache/qemu-everything/canon-eos-build/build/arm-softmmu/qemu-system-arm" \
  --sd /path/virtual-sd.img --cf /path/virtual-cf.img
```

The returned environment sets `QEMU_EOS_WORKDIR`; the command uses `-M 50D,firmware=boot=0`. A plan is not a boot result. Keep the user-owned ROM pair private. Firmware capture, live view and hardware fidelity are not claimed.

## Actual offline experiment

The assisted updater experiment is reproducible with the original decoded
`updater1.bin`. This command accepts only its exact 50D 1.0.9 SHA-256, limits
runtime to 1–30 seconds and each diagnostic log to 2 MiB. Guest files and
synthetic virtual media are removed after the owned QEMU process exits.

```sh
mkdir /path/empty-probe-directory
python -m devkit.canon_lab run-updater /path/extracted/updater1.bin \
  --qemu "$HOME/.cache/qemu-everything/canon-eos-build/build/arm-softmmu/qemu-system-arm" \
  --output /path/empty-probe-directory --seconds 10
```

For WSL, using an output directory in its Linux filesystem avoids slowing the
emulated card accesses through a Windows mount. The JSON report distinguishes
observed updater UART milestones from original boot/UI and records the exact
guest intervention. A zero exit code alone never sets a success flag.

The pinned custom emulator was built and ran the **real decoded 50D updater**, with explicitly synthetic ROM placeholders and snapshot virtual cards. A 20-byte guest prelude cleared the CPU's high-vector bit and branched to the original updater at `0x00800120`. Its DryOS tasks, CF, MPU and display controller ran; UART printed `Welcome to Update program`, `Program Ver.Slave 0.2.0`, `Initialized` and `BackLightOn`. Missing ROM properties, model information and fonts produced a gray screen. This is updater runtime evidence; it is **not** successful original camera UI boot.

Credits: Arm.Indy / Magic Lantern `fir_tool2.py` supplied the GPL-2.0-or-later updater XOR algorithm; qemu-eos is the custom hardware emulator. Main-payload reverse engineering and bounded host validation here were independently implemented from the user-supplied official updater. No Canon secrets, ROM dumps or vendor executable are distributed.
