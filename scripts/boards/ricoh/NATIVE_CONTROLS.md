# GR IV 1.11 original UI controls (assisted component lab)

This extends the existing TopMenu/Focus lab. The original ARM firmware draws the
720×480 Canvas, widgets, fonts and icons. The host submits commands and presents
captured guest pixels. It does not paint a replacement camera interface.

## Available paths

- Original LiveView HUD, without a sensor image, and original empty PlaybackError
  ("No image"). These two entries use the original factory and Show callback.
- TopMenu/SelectMenu navigation, confirmation and return, with pixel restoration.
- Original DISP mode cycle and ADJ quick settings (five tabs).
- Preview direction shortcuts: Macro, WB, ISO adjustment and Drive.
- Fn details from ADJ and WB; front/rear dial events; P/Av/Tv/M mode positions.
- Half-press/release events with an observed HUD response. This is not evidence
  of autofocus, lens operation or capture.

Raw keys go through original AppMain handlers, mode positions through KeyModel,
and native controller transitions through the existing AppMain queue. Dispatch
and drawing use the original UIManager synchronization. The original UI timer
callback refreshes the HUD. List transitions settle through a native helper;
complete RTOS animation timing is not validated. The image-resolution translator
uses original model selection/allocation and table initialization, without the
full image-utility initializer that requires unavailable hardware services.

## Build and run

Use your own exact GR IV 1.11 decoded payload, SHA-256
`c4e597c7c9ca1bc181b30e35139ed90a4fd876ddcca14f12be0e00b6702233ae`.
No firmware, resources, captured screens or memory dumps are distributed here.
Build the pinned QEMU RTOS lab as described in [README.md](README.md), then:

```sh
python3 scripts/boards/ricoh/build_ui.py \
  --qemu-manifest /absolute/path/ricoh-rtos-build/build-manifest.json \
  --work-dir /absolute/path/new-ui-build
python3 scripts/boards/ricoh/launch_ui.py \
  --payload /absolute/path/payload.bin \
  --manifest /absolute/path/new-ui-build/ricoh-ui-manifest.json \
  --destination /absolute/path/new-run --probe
```

Remove `--probe` for the interactive loopback viewer on port 8792. Ctrl-C stops
and reaps the owned session. Python 3.13/macOS was used for the recorded run;
Linux/WSL execution of this extension has not yet been verified. The existing Qt
workbench adapter uses the same runner; its configured UI manifest must be rebuilt
for profile `ricoh-griv-native-controls-v1`. Old manifests deliberately fail the
profile/hash gate. Existing session/event limits and origin/token checks remain.

## Verification and limits

The finite probe checks 38 native page/input properties, including exact return
frames, distinct mode frames and dial parameter changes. See
[evidence/griv-1.11-native-controls.json](evidence/griv-1.11-native-controls.json)
for a bounded summary; full per-run audit data remains local. Each new run writes
its own evidence and preserves the original menu verification gate.

This remains assisted execution, not a full cold boot. Hardware-startup guards,
resource/storage adapters and software-display setup are recorded by the existing
probe. Original GUI code and source firmware remain unchanged.

Empty-playback MENU return explicitly re-enters the verified empty component;
it does not implement the physical playback key or photo-loading services. DISP
modes 2 and 3 currently produce identical pixels. ISO dial events change the
original value, but one plus followed by one minus need not restore it; physical
repeat/debounce timing is unverified. Preview Fn/AF+AE, Delete, Movie, EV, full
shutter, power and user mode positions are not enabled in the viewer. There is no
sensor stream, image capture, album loading, hardware autofocus or persistence to
a real camera. The mode lock is mechanical and has no simulated GUI action.
