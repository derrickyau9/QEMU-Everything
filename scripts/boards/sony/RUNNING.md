# A7 IV / A7R V Linux lab bridge

`devkit.sony_lab` exposes:

```python
lab_available(device, home)  # quick model/config eligibility
run_lab(device, home, task, seconds=30, mode="shell")  # -> evidence.json Path
run_lab(device, home, task, seconds=30, mode="init")
```

Configure the workbench data home using `sony-lab.json`:

```json
{
  "distro": "Debian",
  "manifest": "/home/your-user/.cache/qemu-everything/sony-cxd90057-lab/build-manifest.json"
}
```

The supported inputs are the exact A7 IV 2.00 and A7R V 4.00 updates identified
by `devkit.sony_components.PROFILES`; `profile_for_device` selects their exact
source SHA-256, never an architecture guess. Both original bootloader device
trees were compared byte for byte before the A7R V kernel was actually tested.
Preparation rehashes the full source update and FAT partition, uses the normal unpack
pipeline and the verified FAT12 directory/cluster chains, then checks the
original kernel and ext2 initrd component fingerprints. Preparation completes
before the session lock, including first-run unpacking.

The launcher verifies the QEMU binary, generated board source, overlay and DTB
against the private build manifest. Exact original inputs and the development DTB are copied into a
temporary Linux directory and checked again; source hashes are rechecked after
the run. QEMU receives a single Cortex-A35 with the original 2 MHz counter
frequency, fixed bootloader parameter/DTB/initrd addresses, a development device
tree with a 2 MiB noncacheable DMA instruction pool, and the board's peripheral
models. The original initrd is loaded at `0x418000000` to avoid that pool.
Sony's USB initcall is explicitly
blacklisted until its device tree/controller support is implemented.
Only the frozen development DTB copy's exact `/chosen/linux,initrd-end` property
is adjusted to the selected initrd size (A7 IV `0x4184cc000`, A7R V
`0x4184fa000`). The original build DTB and all firmware files remain unchanged;
the before/after property values and DTB hashes are recorded in the evidence.

`shell` adds `init=/bin/sh` and checks native `uname`, `/proc/self/status`,
`/proc/mounts` and a fresh serial proof token. `init` requests the original
initialization chain; an attempted init or kernel boot does not imply that
camera services or original UI work. The returned evidence records the mode,
kernel/rootfs/shell milestones, observed oops/panic, interventions and cleanup.

Original initialization attempts on both models loaded the DMA and AES paths after reserving
the correct noncacheable pool. Later initialization still requires the private
eMMC key/hardware handoff used by `cryptEmmc_setKeyCovertly`; that hardware path
is not implemented. Offline inspection of A7 IV's original `crypt-emmc.ko`
(SHA-256 `a62c89b0f84ac3cc822d2e0cfa1c66bfe6e591e2b30b6de88f0f94af6f1bc147`)
locates the failing helper at text offset `0x54`: `ldrb w0, [x0, x5]`.
The helper initializes a base of `-0x4800` and reads 32 bytes through an offset
table; the actual fault has `x0=0xffffffffffffb8eb`. The development DTB's static
IO mapping implies physical address `0xffffb8eb`. That backing data is absent
from the current board. This identifies a missing read source, rather than
proving whether its contents originate in boot ROM, OTP or a prior bootloader
stage. No key contents or replacement keys are supplied. Full storage, camera
services and original UI are unverified.

The GPL-2.0-or-later runtime supervisor shares owned-process cleanup with the
H22 board at `../insta360/run.py`. No firmware or compiled emulator is included.
Sessions are limited to 5–120 seconds, use no network adapter and only a local
Unix QMP socket, and cancellation stops only the guest launched by the session.
