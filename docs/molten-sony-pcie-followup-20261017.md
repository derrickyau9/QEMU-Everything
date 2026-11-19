# A7 IV PCIe peer and input follow-up, 2026-10-17

This is an F3 **private development** experiment using an original A7 IV
2.00 kernel, original `pci_ep_cp.ko` and `pcie-if.ko`, a copied RAM disk,
the existing CP UART sidecar and the `sony-cxd90057-lab` QEMU machine. It
does not establish an original storage boot or ordinary-menu execution.
The copied guest and DTB are changed only in private run directories;
the source image is SHA-256
`d6daa13290e0b158166abc7c69f3a325459acee271b5a654c9d39895d525a30a`.

## Causal findings

The original `pci_ep_cp.ko` registers a PCI driver for Sony
`1556:1100` and requests `pcie_root_ctrl(0,1)`. The original Sony DTS
names controller 0 `pcie_i`. Loading that module with the development DTB
returned zero from `insmod`, yet the guest logged `The node(pcie_i) didn't
exist!` and `pcie_root_ctrl:ch=0,enable=1:ret=-22`. It never proved a
PCI device probe or `pcidma_connect`. The original PCIe DMA path can queue
a request while disconnected, but cannot start a doorbell or complete it
until the real link/connect path runs. A software enqueue is not a DMA
completion.

The original `pcie-if.ko` drives AP GPIO port 18 bit 9 for channel 0 and
samples AP port 17 bit 4. Guest IRQ 91 and 92 correspond to AP port 17
bit 3 and 4 and are separate from PCIe DMAC IRQ 198. The read-only TCG
plugin saw AP output and queue RAM CPU accesses; it did not observe a
same-run CP GPIO edge, an AP input transition, a peer commit or a DMA
transaction. Its physical-address memory callbacks cover successful CPU
loads/stores, not arbitrary DMA. The queue interface flags at `+0xffe0`
and `+0xffe8` were zero in a stopped-guest QMP snapshot, and both IRQ
counters were zero. A valid original queue interrupt requires a flag
value of one **and** a causal peer edge; driving an IRQ without those
events would invent a reply.

The original reserved AP region is 4 MiB,
`[0x404c00000, 0x405000000)`. Local queue blocks occupy its first
512 KiB; `0x404c80000..0x404d00000` is the outbound/peer alias candidate.
The QEMU trials reserve all 4 MiB in their private DTB. The AP/CP address
translation and ownership relationship still need a live proof before
copying bytes. MOLTEN's transport contract now refuses DMA and queue
copies without descriptor-bound, action-time adapter evidence for the
appropriate link, doorbell, peer readiness and translation gates. The
Sony adapter in this record is read-only and takes zero actions.

Sony's [A7 IV Ver.2.00 source distribution](https://oss.sony.net/Products/Linux/DI/ILCE-7M4_Ver200.html)
lists its Linux kernel and open-source packages, but does not list the
proprietary `libObj.so`/GRM implementation. The [Sony menu guide](https://helpguide.sony.net/ilc/2110/v1/en/contents/TP1000640145.html)
defines the user-visible target: MENU displays the menu, wheel directions
move between items, and the center button selects. Original menu-object
construction remains below that target until real guest-produced frames and
an input-caused frame change are captured. The read-only PC probes use
instruction-execution callbacks; [QEMU's plugin documentation](https://www.qemu.org/docs/master/devel/tcg-plugins.html)
distinguishes these from mere translation and notes that memory callbacks
only report successful accesses. Each absence claim is specific to its
bounded run and observed original App mapping.

## Live trial records

| Trial | Bounded result | Raw evidence |
| --- | --- | --- |
| v3, 100 guest seconds, `dfdf16d2b6d04ab09746cb153c55435f` | Both supervisors exit 0. `pcie_i` missing; callback table registered; queue CPU accesses are AP-local; IRQ 91/92 zero. The input command was sent at the deadline, so no execution status was observed. | `.local/sony-pcie-coupled-trial-v3-input/runtime-runs/<nonce>/` in the private DevKit |
| v3, 160 guest seconds, `034dcd131ce44155a4fd19fe6b72ed7b` | Both supervisors exit 0. A Menu press/release file copy returned 0 after the original key list bound; the compatibility bridge printed two delivery messages. Guest shell lacked standalone `od` and `sha256sum`, so status words and frame hash were not captured. Those messages alone did not prove acceptance. PCIe peer alias CPU accesses and IRQ 91/92 remained zero. | Same private DevKit run directory, with `serial.log`, `pcie-access.jsonl`, `pcie-causal-observation.json`, `evidence.json`, `orchestrator-result.json` |
| v4, 160 guest seconds, `4aeeb4afbfbe41b2b224a7a18fcd2a24` | Both supervisors exit 0. The 72-byte Menu record copy returned 0, but this guest also lacked the attempted `busybox od` and `sha256sum` applets; no status words were captured. No frame guard marker or frame file was produced. The bridge's delivery wording was later shown to be insufficient evidence. AP queue accesses remained local; IRQ 91/92 stayed zero. | `.local/sony-pcie-coupled-trial-v4-input-evidence/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `18124f554a3ef8b2684c72e6e6399a7cc895d35295f7f7b2895ace3057cf2037` |
| v5 status, 160 guest seconds, `f27c51dfe30a4998ab3e1525923bcb59` | Both supervisors exit 0. A read-only static AArch64 guest helper read four status words after Menu press/release: `2 0 2 4294967258` (last word signed `-38`). The 72-byte input file was present, but no accepted event was confirmed; frame metadata and frame raw files were absent. The bridge again printed two delivery messages, so its wording conflicts with the status file. No ordinary menu or input loop was verified. | `.local/sony-pcie-coupled-trial-v5-input-status/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `c787ebda381d32f1d06d55080f8660d783288e3fed09fe21afd0ea4f00ee746c` |
| v6 first diagnostic build, `64a882e99b32496ba96f30b874befbee` | The private gate-telemetry provider introduced an unsupported `__aarch64_swp4_acq` dynamic symbol. The guest stopped at `bootin.elf` symbol lookup before input binding, so no Sony input gate or menu inference is possible from this run. This is a diagnostic build failure; the original frozen provider and source image were retained. | `.local/sony-pcie-input-gate-v6/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `c82cebc56f975eca50f8aae9b47c560cac1be1141e48bf1617976330ac8650da` |
| v6 corrected diagnostic, 160 guest seconds, `212689627286440cac4a5a32444a18b3` | Both supervisors exit 0 and are reaped. For each Menu record, gate telemetry shows owner, symbol, node, active worker, key count 1085 and distinct queues present. The original node's `+0x10` field is `0x20e`; the frozen bridge's zero-valued gate fails, so `native_async_reply` is **not called** (`attempted=0`). Status remains two rejected, zero accepted; frame metadata/raw files are absent. No original menu or input loop was verified. | `.local/sony-pcie-input-gate-v6/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `4eb01cf88441ad911816df3ec370fd112432ba65d37e61512c34de3f55ab1cef` |
| v7 corrected input gate, 160 guest seconds, `bbf2ec4f5ffc4230aa74b3429682a4e7` | Both supervisors exit 0 and are reaped. The private bridge requires a positive original thread TID and retains all other gates. Both Menu records call `native_async_reply`, each returns 0; status is `2 2 0 0`. This proves accepted OSAL queue sends only. No post-input App receive log, original frame metadata or raw frame was observed. The guest later entered `im.elf` exception monitor, also seen in the earlier baseline; causality from these keys is unproven. | `.local/sony-pcie-input-gate-v7/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `dab65092542fec37545e5efaadbada3db9e4c331600ecf6ad11d2b7a8f2b84e7` |
| v8 original worker consumer, 160 guest seconds, `f8fa84990b704931997c042e8bc5fefb` | The exact `replay.ps1` command completed; both supervisors exit 0 and are reaped, with no exception monitor or kernel panic. A transparent receive interposer witnessed the original `libObj.so` worker twice return success from queue `0x7ae0c4`, with source `0x7ae0c3`, 32-byte Menu key `0x1ab`, states 1 then 0. FNV-1a 64-bit payload digests match the pinned host records. Send status is `2 2 0 0`; frame metadata/raw files are absent. This proves original worker dequeue, not original App callback or menu display. | `.local/sony-pcie-input-consumer-v8/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `2c5ef12c07d340c6ab67d2871554b1393604383e16569fbffd71e93ec8fab78f` |
| v9 original App callback, 160 guest seconds, `46f530481fa74863be8c55372df5d2b0` | The exact `replay.ps1` command completed; both supervisors exit 0 and are reaped, with no guest panic, assertion or exception monitor. The original worker dequeued both Menu events. A transparent callsite-filtered observer then saw original `appFw.so+0x1157bb4` allocate a 32-byte message and `appFw.so+0x1157c74` send it to queue `0x5503e0`, with both native returns zero for press and release. This proves original App callback entry and onward enqueue. Frame metadata/raw files remain absent, so ordinary menu and visual input loop remain unverified. | `.local/sony-pcie-input-callback-v9/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `84f0b6dd7c22694af145cb682fe9bbd9e3af59cc6f532d8b759748220802fca2` |
| DT-only, 50-second limit, `ff298bc967794899a4572f9a3e4a4262` | The original `pcie_i` node and dependency phandles cleared the missing-node gate. The guest then hit a synchronous external abort at `dw_pcie_readl_phy+0x8` on the unmapped PHY register consistent with physical `0xf11e0018`, before endpoint enumeration. QEMU was reaped after 29.9 seconds. The stopped-guest callback check was unavailable because its UIPC marker was never reached in this trial. | `.local/sony-pcie-dt-only-trial/runtime-runs/<nonce>/` in the private DevKit |
| v10 original App receive witness, 160 guest seconds, `184bd0adb2734ae99752e22928d4a1d4` | The original `App::recv_event` loop ran and received/freed 16-byte system messages from `0x830128` on queue `0x5503e0` while `GraphicsEngine::processSystemMessage` repeatedly returned 1. Both original Menu callbacks independently sent 32-byte messages to that same queue and returned 0. The bounded observer saw no exact Menu key or payload digest among its logged receives; its sampling does not rule out an unsampled or transformed record. Input status was `2 2 0 0`; frame metadata/raw files remained absent. A later `infra_wrapper_bb.cpp:786` assertion entered the exception monitor, with no proven causal link to Menu. Both supervisors exited 0 and were reaped. | `.local/sony-pcie-app-consumer-v10/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `85681ecf7dfa4538eec45560c5e821f215a0d76a5dff3610344045f2c8e51e2` |
| PHY/SCU read-only probe, 50-second limit, `475542e1a22443cbab506b9cf360e4af` | An isolated TCG plugin saw 35 completed SCU accesses, including original clock-set and reset-clear writes, then the DT-enabled kernel reported `PCI0: handover` and aborted at `dw_pcie_readl_phy+0x8` on the unmapped `0xf11e0018` PHY register. There were no completed PHY or controller accesses. No MMIO values, link state, GPIO input, IRQ or DMA completion were fabricated. The probe establishes a stop in this DT path, not the cause of the separate input/menu failure. | `.local/sony-pcie-phy-scu-trace-v10/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `953c915e1b5402a05b750ad174fff1ee3a5f0fa38d7e4c08d8d8bac639da852e`; private report `.local/molten/sony-pcie-phy-probe/RUN-REPORT.md` |
| v10-based guarded P4 panel trial, 160 guest seconds, `37c925b6d68046fb8144819b17d54a14` | Both supervisors exited 0 and were reaped, but the one-shot artificial `0x1100` completion was **not injected**: this run issued no original front-panel request, so the guard never opened. It followed HDMI and updater requests, then showed graphics-backend `-38` and an original `infra_wrapper_bb.cpp:786` assertion. There was no menu frame. This is a recorded negative intervention attempt, not a test of an injected completion. | `.local/sony-pcie-panel-p4-1100/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `597b69eb5884ed0382e2359d64666b4152244cccd9c5f3c40cadf1ade2f2dcf6`, CP wire SHA-256 `6a69e75af793e43b1407f50ede36b574634c4d8bc53eb9e202306bbf7c390a6f` |
| v9-based guarded P4 panel trial, 300 guest seconds, `1827fc6b1e3049bc9b01767fbb0d9942` | A unique actual front-panel request opened the guard. The sidecar injected exactly one **artificial** wrapped `0x2001/0x1100`, 36-byte status-zero packet through original CP allocation/OSAL transport. Original CP then resumed its panel task and emitted a genuine `0x200d/0x1118` completion to actual AP destination `0x81cf618e`; guest OSAL enqueue returned 0. This proves assisted CP progress, not original LCD/EVF hardware completion or AP consumption. Only four original OSD `1001` registrations were logged; no `1002/1003` submit or frame metadata/raw file appeared. Menu input status was `2 2 0 0`. No assertion or exception monitor was reported; both supervisors exited 0 and were reaped. | `.local/sony-pcie-panel-p4-1100-v9/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `30d6ad140ebec51c3d1f334e2c317fd937dc77fef4b645c44925ead52bb5cd93`, CP wire SHA-256 `0c612d5c694d5ef02d1a054f07fe147292cf21219c392b93f42cc55c1704e0b5` |
| v11 continuous App queue witness, 160 guest seconds, `745afcfbf8d34dff80f92e9ea63799f0` | Original Menu press/release callbacks sent 32-byte messages with header priority 1 to original queue `0x5503e0`, both native returns zero. The recurring 16-byte timer sends had the same priority. Read-only kernel queue statistics around the press were 77→81 messages and around release 88→91; late depth reached 1469 while the original App received only 21 observed messages, none Menu. This establishes a severe backlog in this heavily instrumented run, not a proven loss of the Menu bytes or the same backlog magnitude in v9. Frame metadata/raw files were absent. Both supervisors exited 0 and were reaped. | `.local/sony-pcie-app-consumer-v11/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `07b771dadcafa9360dfe972f3a3032e2822c1039b7e1a8f31f8855f9e250dafe` |
| Guarded P4 Menu priority, 160 guest seconds, `00f66143074e4d49842e694ed3d762f5` | Both exact original 32-byte Menu callback messages passed strict caller, owner, key/state, header and one-shot guards. The original `libosal_uipc.so` priority setter returned zero for priority 1→4 twice, leaving each 32-byte payload digest unchanged; both original sends returned zero. Original `App::recv_event` then received the **press** (key `0x1ab`, state 1, priority 4) on queue `0x5503e0`. This establishes original App receipt in this assisted run. The release was not witnessed at App receive before a separate `model::sequence_power_save_control::run()` worker entered an original assertion and exception monitor. Causality from the Menu press to that worker failure is unproven. No frame metadata/raw file appeared. Both supervisors exited 0 and were reaped. | `.local/sony-pcie-menu-priority-p4/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `a484c3441f3270910865dc1321fa7d3b601eed7d77e23129ff0f3fdb924e2b8e` |
| Read-only App owner/model v1, 160 guest seconds, `1dea52688d6344ca8522560d266175e5` | The original App process was identified, but the helper returned `PROCESS_OR_LOAD_BIAS_GUARD_FAILED` (RC 4), so no owner/model value was obtained. Its single `/proc/PID/maps` read likely missed the deep module row; v1 did not log map bytes, so that cause remains an inference. No input was injected. The guest later entered an unrelated original assertion/exception monitor; no frame appeared. Both supervisors exited 0 and were reaped. | `.local/sony-app-owner-readonly-v9/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `988502eaa1204d75595cd29b71fd7ee1119cd814bc2becbe10860183e9a04284` |
| Read-only App owner/model v2, 160 guest seconds, `be945b72cf0f4989ab5fdc2e09b30bcd` | A bounded in-guest reader uniquely identified the original App process, its executable `appFw.so` mapping and App object/vptr relationship. Two snapshots two seconds apart showed layer-0 original boot screen `0x253` with live presenter/controller and raw model property `0x33e=0`. No input was injected and no memory written. Later the original CEC power request received a 16-byte reply and proceeded to HDMI traffic, so these early snapshots do not prove a permanent CEC stall or late screen state. The guest later asserted in a separate model/transport path; no frame appeared. Both supervisors exited 0 and were reaped. | `.local/sony-app-owner-readonly-v9b/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `919cd1b6557dc5349bbc6f49d76759f77ec3ab081e73297d0090d341bb0d4965` |
| Read-only App owner/model v3, 160 guest seconds, `b963af518f8c40568b8cbf0cb78e9693` | The guarded original App PID, load bias, object and interface pointers remained stable across early and post-HDMI two-second snapshot pairs. Even after the original HDMI inactive-timecode config reply returned zero, layer 0 still held screen `0x253` with the exact original boot controller and presenter vptrs; raw property `0x33e` remained zero. This establishes a persistent boot owner at that checkpoint, not the cause of the missing transition. No input or owner write was made. Both QEMU and bridge exited 0 and were reaped; no assertion/exception monitor was recorded, and no ordinary-menu frame was established. | `.local/sony-app-owner-readonly-v9c/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `6239069ef9077ef17e5134a52f134697d6aadf92c60b3b29b0c65bbd665f31e8` |
| Extended CP-bridge lifetime plus guarded panel/post-panel Menu, **incomplete runner**, `c6f7ff1a94f94a67a1ffd6c61af5af89` | The private guest bridge deadline was extended from 100 to 360 seconds, with original Sony modules unchanged and Menu priority left at 1. An artificial one-shot panel `0x1100` reply let original CP emit `0x200d/0x1118`; original AP worker then directly dequeued that 12-byte status-zero completion and finished its publish path. A second physical Menu press/release entered the original callback; input send status was `2 2 0 0`. At guest uptime 133.32 seconds the CP ready file still existed. F030, F023, F024 and F029 component traffic reached the original transport without the earlier `-38`, but component/device consumption and an OSD commit were not verified. No frame metadata/raw file or original-menu visual appeared before the supervisor stopped the run at 173.5 host seconds because its conditional frame-export branch raised `UnboundLocalError` for `re`. QEMU/bridge were reaped; this is retained as a failed, partial run, not a completed 300-second trial. | `.local/sony-pcie-panel-postmenu-p4/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `22719a5aeb6f18498ea1a423e6ada933264f537cfe6e5edbc1111319c5b9ecf4`, `supervisor-error.txt` SHA-256 `f01f38e429c0a1634a143efeb8d62354ad8d0bd20f87aa3d7bc894f8138dc237` |
| Extended CP-bridge lifetime plus guarded panel/post-panel Menu, **corrected full run**, 300 guest-second limit, `759cc7a7a7b34b019316386f4dc2f6bf` | The corrected private runner completed in 338.046 host seconds; QEMU/bridge exited 0/0 and were reaped, source inputs unchanged. The one-shot artificial `0x1100` again led original CP to send `0x200d/0x1118/status0`; original AP worker directly dequeued it and completed its publish/free path with pending counter zero. The initial and post-panel physical Menu pairs were accepted (`QE_INPUT_STATUS_WORDS 4 4 0 0`), and the CP ready file was present at the later component probe. F030/F023/F024/F029 fanout was forwarded to original CP, but no independent component-worker dequeue, later driver application or OSD submit was verified. There was no guest frame file, assertion or exception monitor; `original_ui_verified=false`. Menu priority remained 1, so this run isolates panel and bridge lifetime without claiming App Menu receipt. | `.local/sony-pcie-panel-postmenu-p4/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `2fa63ff05d7355470eb450a866d53009ab698fe4b36fd7c325096f8c9cc36ee2`, CP wire SHA-256 `0940ae209b0dbe2ffabb24432ac7c00b434ebc28b066e3b4c4c89d9dce20a7a1` |
| Read-only boot-gate v4, **late-trigger failure**, 160 guest seconds, `04edb5e865f64b3abb9b49edb08e84d9` | Two guarded early samples found original screen `0x253` and `controller+0xcd32=0`, BIS/shutdown 0 and date-time status 2. The private launcher waited for a stale HDMI log phrase, so it never launched the intended post-HDMI sample. This run cannot establish late POWER_ON processing. QEMU/bridge exited 0/0 and were reaped, with no source changes or guest writes. A corrected, separately pinned v4b replay follows; the failed trigger is retained. | `.local/sony-app-boot-gate-readonly-v9d/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `d7d521886c3e1ae48b9d74daf33c6a71bc98065c74c5a36e7cb86b78d34821d4` |
| Read-only boot-gate v4b, **corrected trigger**, 160 guest seconds, `75e442fe667d4a33ab473c0f41d88fe3` | The private launcher captured the actual HDMI config-reply line and ran two early plus two post-HDMI guarded snapshots. Both late snapshots retained the original layer-0 boot screen `0x253` and matching controller/presenter vptrs, with `controller+0xcd32=0`, pending screen/child 0, BIS/shutdown 0, date-time status 2 and rep-boot ready flags/status/timestamps all 0. Thus original POWER_ON had not reached the controller's entered-byte write by that checkpoint; whether the event was queued, missed or blocked earlier remains unresolved. No input or guest write was made, no original frame appeared, and QEMU/bridge exited 0/0 and were reaped with source inputs unchanged. | `.local/sony-app-boot-gate-readonly-v9e/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `ebb74997d6d285bb9133b72d42f0f441e696a81d21f8fde661ea490b390c2ce5`, `boot-gate-analysis-v4b.json` SHA-256 `5e4f401797843091945fe5d446e5589d99a8bef90989b96c0a1a46481eefb44b` |
| Original POWER_ON priority P4, 160 guest seconds, `12f79bfbda164048abfaff0a7adf1756` | At the exact original pre-send callsite, a guarded one-shot native `libosal_uipc` setter changed only the header priority bits 1→4; the 16-byte `0x30069` body was unchanged and original send returned 0. An original App receive on queue `0x5503e0` matched PID, source, priority and body. This is a receive **candidate**, because the native queue has no proven stable message ID and subsequent competing-send capture overflowed. Two late guarded snapshots still showed boot owner `0x253`, controller entered byte 0 and no original frame. No assertion or exception monitor appeared; QEMU/bridge exited 0/0 and were reaped. This bounds the effect of priority alone, without claiming handler dispatch or ordinary menu. | `.local/sony-pcie-power-priority-p4-v9f/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `4eebca59277bd9cdc11be5e29a5b5733fffc7ede484b0fc9b74e255e5cbf712e`, corrected `power-priority-analysis-v9f.json` SHA-256 `be27cc022b9436565302ccee21b4d1de7a7be988e00b0dacd6c0ab65ee56085b` |
| Forced original menu-child entry P4, 200 guest-second limit, `97e773be9f554634a808018f4dc7256c` | A private clone changed only one original `App::main` screen-ID instruction from `0x253` to `0x21f`; the immutable source `appFw.so` was unchanged. Two read-only snapshots found layer-0 screen `0x21f`, original `scr7001_1001` presenter final vptr at load bias `+0x314ab00`, and controller final vptr at `+0x32bbfa8`. Physical Menu down/up reached the original input worker and App callback, with native send status `2 2 0 0`, but App key dequeue was not witnessed. No OSD `1002/1003` or guest frame metadata/raw appeared. The guarded panel `0x1100` intervention was **not triggered** in this run; the later post-panel Menu pair was not sent. After an original `scr7001_1001 → rep_shooting` open-screen log, `im.elf` entered a model-system-error assertion/exception monitor; its causal relation to absent pixels is unproven. QEMU/bridge exited 0/0 and were reaped with no source changes. This proves original menu object construction, not a visible menu or input-to-frame loop. | `.local/sony-menu-entry-p4/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `56084b4792a9838a8f16fa640b6858d9dc2c6046a8343751fd846ddf6f6c18e2`, CP wire SHA-256 `5d2ec703a477bfa8ce690b1ea5c5691df1eeaaf95b13c7a55bc51ce14e79082f`, `RUN-97e7-REPORT.md` SHA-256 `e07bebfbda433f8acbc5f82061498ff872fabb4308718af882da64421ef5665f` |
| Forced original menu-parent entry P4, 200 guest-second limit, `03ad95fb121d4ce2ac6332aabd1476d8` | A fresh private clone changed that same single screen-ID instruction from `0x253` to original `rep_menu` `0x27f`. The original App logged `open screen: rep_menu -> scr7001_1003`; a first read-only snapshot caught transient layer-0 `0x27f` without a presenter, and the second found child screen `0x221` with original presenter/controller final vptrs at load bias `+0x314ab80/+0x32bd0a8`. Original resources and fonts decoded, and a 96-byte OSD `1001` request stored a pending registration. Separate original 80-byte requests to destination `0x8280074d` returned `-38` from the private graphics backend's unsupported branch; their body/type were not captured in this run. Physical Menu press reached the original worker and App callback, but callback send return, App key dequeue and visual effect were not verified. No OSD `1002/1003` commit or guest frame artifact was observed before `im.elf` entered a model-system-error assertion at guest uptime 100.75 seconds; the scheduled frame probe did not complete. QEMU/bridge exited 0/0 and were reaped with source inputs unchanged. This establishes original parent-to-child menu routing, not ordinary-menu pixels or an input loop. | `.local/sony-rep-menu-entry-p4/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `3df3444dda373e597e9d487202d1e869f9ef85d9faa68586a36723bf336de809`, CP wire SHA-256 `92ea0afbf588cca9b0374ee659cf3328f0739ad7672dfaddfb7051162510f8cd`, `RUN-03ad-REPORT.md` SHA-256 `428d1db1853fef82e1771a24cf305b4e2c232c9e702dee8e9d961651c2a33146` |
| Passive original OSD reject capture on menu-parent P4, 200 guest-second limit, `649f22f30475476d93d795e8ad996c05` | The private graphics worker kept its original unsupported return `-38` and logged at most four matching rejected requests without altering payload, ACK or pixels. Two actual 80-byte messages had destination `0x8280074d`, source `0x9088076b` and type `0x1000`, with original body FNV-1a digests `6534181cb54125f2` and `e64c3d0580fd0c19`. Their GUI-format words at offset `+8` were `0` and `0x0b`; handle counts at `+12` were both zero. Independent reconstruction of the raw 80-byte bodies matched both digests and retained per-body SHA-256 values. This resolves the prior type ambiguity and identifies a missing private `SetGuiInfo` backend semantic. Original AV-CAM disassembly says `0x1000` records pending GUI format/count/handles and dirty state without replying or drawing; `0x1002` commits separately. The original `rep_menu → scr7001_1003` route and later child owner recurred, with no original-menu frame verified. QEMU/bridge exited 0/0 and were reaped. | `.local/sony-rep-menu-graphics-observer/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `60830adb3493202a85bd3e676655adb51099df533665decbba0ab0fdf6fd82c6`, `osd1000-raw-audit.json` SHA-256 `191386addeeaa2e4c63d2736920231c49f658ccee57a836f43b90e7d5760c616` |
| Delayed original POWER_ON pointer P4 v9g, 160 guest seconds, **guard did not trigger**, `a000b82ff9634d6f8006ebd9dd39eb99` | This private build intended to withhold one original OSAL message until the boot owner existed, but the runtime logged no exact pre-send guard, capture, skip or release marker. The compiled provider and private image matched the staged hashes; the offline analyzer records `intervention_count=0` and `PRE_SEND_OBSERVER_NOT_REACHED_OR_PRETRACE_GUARD_FAILED`. An original App receive of priority-1 `0x30069` is an unpaired candidate, not evidence that this branch delayed it. Late original owner remained `0x253`, entered byte 0, with no verified frame; a model-system-error assertion occurred separately. QEMU/bridge exited 0/0 and were reaped. This is a retained negative **observation-gap** run; it does not test the hold/release effect. | `.local/sony-pcie-power-delay-p4-v9g/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `cae7ef77f32fb104d11da6de2d246924742b6c9823ec548809eeaf01f9cf6cfc`, `power-delay-analysis-v9g.json` SHA-256 `67d73a9eb39a78691362a4fc61afc680a21f7516e04fdbe12a81933259654b66` |
| Passive POWER_ON pre-send sampling v9h, 160 guest seconds, **sample overflow**, `af404be308504e1eab351d171fb42c2b` | The read-only observer sampled eight earlier original App sends at `appFw.so+0x1158708` to queue `0x5503e0`; all eight native sends returned zero, while each self `process_vm_readv` of the UIPC message returned `-1`. The ninth call overflowed its bounded sample, so the exact POWER_ON callsite `+0x2296654` was **not sampled**. This suggests a concrete reason that v9g's memory-read guard could have failed, but does not establish that it did. No v9h mutation or input was made. Original boot owner later remained `0x253`, no frame was verified, and the separate model-system-error assertion recurred. QEMU/bridge exited 0/0 and were reaped. | `.local/sony-pcie-power-presend-passive-v9h/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `5b5579c99b5d977aa2cbb32a5839e75d87f184b90a01caf84d1c6152d555303a`, `power-presend-analysis-v9h.json` SHA-256 `a16a813b802b94da119693a45520d9f92838b9c3b96e015e44366766aba9dfa4` |
| Guarded private OSD `SetGuiInfo` backend P4 on forced menu-parent entry, 200 guest-second limit, `6312fe4542c44e49b9b306baea5cfa2f` | A private provider with exact destination/source/type/80-byte guards accepted two original `0x1000` registrations and recorded pending GUI format 0 then 11, each with zero handles; the previous graphics-backend `-38` messages disappeared. Original App again opened `rep_menu → scr7001_1003`, the original OSD `1001` registration was pending, and physical Menu press reached its original worker and App callback with native send return zero. No original `1002` commit, `1003/6003` transfer, guest frame, App key dequeue, release or input-to-frame change was observed. The same original power-save model assertion occurred at guest uptime 102.25 seconds. The panel `0x1100` guard did not fire. QEMU/bridge exited 0/0 and were reaped; `original_ui_verified=false`. This removes one confirmed private metadata blocker without proving original menu pixels. | `.local/sony-rep-menu-osd1000-p4/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `3a480eda6ac556a628b2da2644804fc8c7cdfedd3079994d170c1dc916ead9f0`, CP wire SHA-256 `6ddf65cfcfff83c6277b4c47a02f36ccd4ed024ac61bc7120c6d3938d304f434` |
| Combined guarded OSD1000 and original-UPM-policy P4 on forced menu-parent entry, 200 guest seconds, `a609bb4aecde48c7b840e5e68196ab3a` | The private UPM backend included a source-backed `upm.ko` Backup `0x00920085` kind-normalization branch while retaining original 16-byte requests; this run did not export a request proving that branch executed. Original UPM inhibitor operations `0x00920114` succeeded with recorded pair counts 1→2→2; the prior power-save model assertion did not recur during 200 seconds. Original App opened `rep_menu → scr7001_1003`, two guarded `0x1000` GUI registrations and further widget registrations were accepted, and a read-only snapshot found child screen `0x221`. The runner sent Menu down/up early (`2 2 0 0` send status, original worker/callback witnessed); later original routing went `scr7001_1003 → rep_shooting → rep_boot`, with no proven causal link to the key. No `0x1002/0x1003/6003` or guest frame was observed. The exact UPM request body was not exported, so kind normalization is source-derived rather than a separate runtime byte witness. QEMU/bridge exited 0/0 and were reaped; `original_ui_verified=false`. | `.local/sony-rep-menu-upm-policy-p4/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `afacbe191eb21670f3ee5143582b508057bcd09a9d6240ac383a9c89659aa98d`, CP wire SHA-256 `ef2cf8e3d88d6f83b9ee7ff55e73289b1d84c70b4efb1e714bdcfb31086771ee` |
| Corrected exact-source OSD `0x1002` backend with 300-second no-input menu control, `04ce738490cc4df089c94f13b932aea2` | A separate private image kept the guarded UPM/OSD1000 changes and corrected the original `0x1002` source/length/selector guard, but the original sender did not reach `0x1002` during this run. The runner sent no early Menu or Down. Original App opened `rep_menu → scr7001_1003`; two guarded snapshots found layer-0 child `0x221`, and no later screen transition or original assertion appeared through 300 guest seconds. OSD `0x1000/1001` registrations were pending, with no observed `0x1002/1003/6003` or frame. The strict frame oracle wrote `complete=false` because no complete BEFORE metadata block was exported; this does not identify the missing draw trigger. A read-only guest export recovered an actual 16-byte UPM `0x00920114` request with raw kind 0 and SHA-256 `1a277965d7f47b2f5355499f0662218c8387a71beb761b26ff7e974636a11931`; it does not prove kind-2 policy normalization. QEMU/bridge exited 0/0 and were reaped with source inputs unchanged. | `.local/sony-rep-menu-upm-osd1002-frame-p4/runtime-runs/<nonce>/` in the private DevKit; serial SHA-256 `995b843185b3843760c0b90be42c664a0e002dccd9cb081456ec5a2c12619b77`; strict frame result SHA-256 `8f1dd3897fd705556186770c4bb52055f8a61c3134c41ea262636649416c8731` |
| Read-only original GRM swap/guard instruction probe, 300 guest seconds, `26cbde74891044bc99ce32486a1473a8` | A QEMU API5 plugin observed exact instruction addresses only after an offline join to the original App PID 274 `/usr/lib/libObj.so` mapping (bias `0x7fadd1e000`) and pinned original module SHA. Its bounded journal had 41,906,908 page-residue candidate events and zero key overflow. At the exact App PCs it recorded zero `GraphicsContextGRME::swap` entries, zero manager entries or `manager+0x48` loads, and zero ExternalOSDIF/send entries. Thus the guard value was never read in this observed App window; its value and any suspend-state cause remain unknown. Original menu child remained reachable, but no `0x1002/1003` or frame was verified. The first offline maps parser rejected pathname padding; only the parser was corrected after the VM exited, with both parser hashes/summaries and unchanged raw journal retained. QEMU/bridge exited 0/0 and were reaped. This is a bounded negative witness, not proof swap can never occur. | `.local/sony-swap-guard-readonly-v1/runtime-runs/<nonce>/` in the private DevKit; raw plugin SHA-256 `f257d4b5d3470d5d1046b6f4eef634cc57d15185132f76baf3c91ea07b27f093`, corrected summary SHA-256 `71183f46ea3a11f25f4b8190664ee32055172af5c386ad168d135f533e39a379`, serial SHA-256 `896206d1600645b98ad6fd7d73f27b36e72d0d2446b5425afc1ce45049f70644` |

The next 300-second read-only upstream-frame run
`sony-upstream-frame-readonly-v2/runtime-runs/e05b1be2b72944879e2711ad8df6d625`
joined original App PID 275 to exact `appFw.so` and `libObj.so` mappings.
`App::init` entry executed once, but the exact original App frame-tick
entry, `notifyDraw`, `drawFramebuffer`, swap and OSD send sites all had zero
execution count. The offline result is `APP_FRAME_TICK_NOT_ENTERED` with no
plugin overflow or failed join obligations; it does not yet prove that
`App::init` returned or which later scheduler condition prevented the tick.
The corrected `upstream-frame-summary.json` is SHA-256
`f8a1d61d502d162e1ebddbdab1bc105e622bea653ed981f440acb2e6a5d20738`.
The copied legacy analyzer also emitted an `INDETERMINATE` summary because
it expected old map-marker names; both artifacts and the unchanged raw
plugin/serial logs are retained. QEMU/bridge exited 0/0 and were reaped.
The full-invalidation branch candidate in `libObj.so` remains unapplied
because this run never reached it.
The fixed v2 replay first checks its pinned baseline image and runner, then
starts a fresh bounded QEMU process:

```powershell
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-upstream-frame-readonly-v2\replay.ps1' -Seconds 300
```

The subsequent 160-second read-only scheduler probe
`sony-app-scheduler-readonly-v3/runtime-runs/0f53b9eac1fb43db88245857d0c21298`
resolved the upstream gate in the original App PID 273. `App::init` and its
caller each returned once; the original main loop continued 540 times. The
instruction at `appFw.so+0x1158e90` was reached 539 times and its
`b.gt +0x1158ef0` skip target was reached 539 times, while the next time
gate, frame-tick call and frame-tick entry were each reached zero times.
The instruction reads the original outstanding-timer counter at `App+0xa00`
and skips frame work when it exceeds two. The exact source bytes are
`0c030054`. The offline owner/map join had no failure obligations or plugin
overflow; QEMU and bridge exited 0/0, were reaped, and source inputs were
unchanged. The read-only summary SHA-256 is
`cecd43fc9ebfe35a517a9cca577b01f28f7c6fefe28efb9e3dfd6b02d817c49b`;
raw plugin journal SHA-256 is
`4ee4ecf04bc0c33dc75883ea15fab7fdc3eb094163e0b2259cfdf6255d7172dc`.
The replay script was repaired after the recorded run, so this exact wrapper
has passed preflight but has not yet been rerun end to end:

```powershell
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-app-scheduler-readonly-v3\replay.ps1' -Seconds 160
```

The first bounded force-frame P4 trial used a new private ext2 clone,
`runtime-force-app-frame-v4/development.ext2` (SHA-256
`c84accdc0d263f9defc015a2c5cfd67188447f9d6da7ea42255989c7186ec777`).
It retained the menu-entry P4 instruction and changed only four image bytes
at `0x3ec3e90..93`, corresponding to original App scheduler
`appFw.so+0x1158e90`, `0c030054` to `1f2003d5` (NOP). The original parent
image and full rollback copy both remain SHA-256
`3de7c9efb2802f835270ff5229acc7aedc15b43da4b62320e0088ea685979ce1`;
the extracted guest AppFw matches the separately patched private ELF clone,
SHA-256 `32ba59e81e1a935351f7ca37741f1ac42ab686df9ad2fdb3903e39c1bfdc48e0`.
The generic P4 receipt and complete four-byte image diff are retained in
`runtime-force-app-frame-v4/offline-patch-report.json` in the private WSL
runtime directory. This is an assisted experiment, not an automatically
discovered MOLTEN fix.

In the 160-second v4 run `0c59636bc9984170a7912eb0ea1b5960`, the original
App PID 272 returned from init and entered the frame tick 82 times; original
`libObj.so` reached notifyDraw 82 times, its swap/GRM send path twice, and
the OSD `0x1003` choice twice. The CP backend logged two captures of an
original OSD raw frame of 614400 bytes, and the guest shell reported a
32-byte metadata file and 614400-byte raw file. The strict extractor still
reported `complete=false`: frequent original logs interleaved with the
nonce-bounded size probe, whose parser required exactly one line between
markers, so no base64 export was sent and no pixels or Down input were
verified. This is a capture-protocol failure after reaching the original
render path, not evidence of an ordinary menu. The scheduler summary SHA-256
is `37eac28ef4cc978b1dda4bd0ed5343b12221901ba1cb5941743166f7cc9c2b27`,
draw/swap summary SHA-256 is
`c741bc9692ade495040d1805d7fd032ac51fe645bb3db1fd2a380c3b606e4501`,
and original serial SHA-256 is
`c8af016059be36625fe6336645edcbc70eb9852d04cabd6b5bc073e9956f1bf6`.
QEMU and the bridge exited 0/0, were reaped, and all pinned source inputs
remained unchanged. The recorded v4 recipe is:

```powershell
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-force-app-frame-v4\replay.ps1' -Seconds 160
```

The v5 300-second replay `3d3c75fb0c21479c8d85f7690b188a73` reused the
same pinned four-byte P4 image. Original App frame tick and draw entry each
executed 486 times; original swap, GRM send and OSD `0x1003` each executed
twice. Both real OSD transfers logged 614400 bytes and physical bases
`0x43a5b4100` and `0x438816100`. During the running VM, a separate read-only
QMP `pmemsave` of each logged base recovered two nonuniform 614400-byte raw
buffers (SHA-256 `568b586ec424e14928037eca67d120a8ab8683aef26262d928b366c02b60b4bb`
and `030940c46afdf5c3736b3c623fb64f6490ea7f63c630fd4e700167a281967567`).
RGBA4444 decoding under the original OSD format-0 contract visibly yields
Sony's ordinary **My Menu / My Menu Setting** page; the two PNGs are
`runtime-runs/3d3c75fb0c21479c8d85f7690b188a73/qmp-first-physical.png`
and `qmp-second-physical.png`. The saved QMP raw files, original serial and
`qmp-physical-readback-report.json` bind the addresses and hashes; the report
SHA-256 is `3d85b2fbf4b92f9d730df63bfec76e20232cee3c7549902aaca28cd80e36e855`.
The guest's separate 32-byte metadata file was not exported, so the decoder's
geometry/pixel-format assumption remains explicit. This is authentic menu
pixel evidence, not yet an input closure or an independent repeat of the
QMP capture.

V5's serial snapshot command copied both guest files, but its long single
UART input line was cut after the `SNAPSHOT_COPY_END` marker (followed by a
partial next marker and shell prompt). The required copy/source-after markers
never arrived, the strict serial oracle stayed `complete=false`, and no Down
key was sent. Its negative frame-verification SHA-256 is
`d0ca344d5a87a6d94a0ef5788c8d86e5a8578539ecf699673dff449908476364`;
serial SHA-256 is
`d3c518b1219855a2f2c0c1372b30759febc220028b110fdfc26040f9f1e4cecb`.
QEMU and bridge exited 0/0, were reaped, and pinned inputs were unchanged.
The v5 wrapper replays the same bounded attempt with a new nonce:

```powershell
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-force-app-frame-v5\replay.ps1' -Seconds 300
```

The v6 300-second replay `ece67c7af3db48d1ace99f80beab6a13` kept the
same pinned four-byte App scheduler patch and split each guest shell action
into a command of at most 240 bytes, with one unique completed UART ACK before
the next action. Its original OSD producer again reported two 614400-byte
transfers at `0x43a5b4100` and `0x438816100`. A read-only live QMP capture
after the Down input, and a second capture after stopping the guest, returned
exactly the same two raw SHA-256 values as v5: `568b586ec424e14928037eca67d120a8ab8683aef26262d928b366c02b60b4bb`
and `030940c46afdf5c3736b3c623fb64f6490ea7f63c630fd4e700167a281967567`.
No third OSD transfer occurred after Down, so these saved menu pixels do not
show input-driven movement.

The v6 UART parser rejected all three snapshot attempts. A separately tested,
read-only v7 parser found a real source/copy race in S0 but stable source,
copy and source-after SHA-1 in S1 and S2. The v6 runner consequently never
sent size or base64-export commands, and no strict guest metadata/raw frame
export exists for that run. Its bounded experimental Down trigger was an exact
original producer size and physical-base witness, explicitly marked
`original_producer_unverified`. Offline input-chain verification confirms
bridge sequences 3/4, original InfraInput worker dequeue of key `0x30`
press/release, and original App callback sends to queue `0x5503e0`, with the
expected 32-byte payload digests and zero returns. It does not establish App
main-loop consumption or a changed menu frame. The frozen v6 input oracle
rejects this valid overlap because the bridge consumed appended records before
the shell emitted `QE_DOWN_APPEND_RC_0`; a separately versioned v7 oracle
records that ordering and one interleaved UART return line without promoting
the result to G5. QEMU and the bridge returned 0/0 and were reaped; all 86
pinned source inputs remained unchanged. Serial SHA-256 is
`f1c07cdb13535d0e466bc6540d13c9a5e7604c44e29c4ce21fb685212f04a4a3`.
The v6 `evidence.json`, `qmp-original-osd-readback.json`, and v7 read-only
reparse receipts retain the full negative record. Direct replay:

```powershell
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-force-app-frame-v6\replay.ps1' -Seconds 300
```

The fresh v7 300-second control `2abdd096b4104560adefaa246fa9ec00` used
the same private image and the corrected shell-noise parser. Original App
frame tick and draw each ran 486 times, with two original OSD transfers.
Snapshot S0 raced the second transfer; S1's guest source, copy and source-after
SHA-1 agreed for both 32-byte metadata (`f7535370bfdf7d55dcdd2368ef24e9ec9fd70513`)
and 614400-byte raw frame (`2052e53aaadee3ee2e7f98b633d31f63b979764b`).
The guest metadata decoded to 640 by 480 at physical `0x438816100`, and
the independent QMP dump of that exact physical buffer has the same raw SHA-1.
This cross-check authenticates original menu pixels from two read paths.
The read-only verifier and rendered original **My Menu / My Menu Setting**
PNG are in the completed v7 run's `offline-crossproof/` directory; its
`crossproof-receipt.json` SHA-256 is
`a18eb082be74305e5b8665b328efd021149cd35e4883ec7619ac6e93f5bc3f97`.
The receipt explicitly records that the UART raw export is incomplete and
that no post-input redraw or G5 proof exists.

The raw base64 UART block reached its END marker but contained only 10768 of
10779 required chunks (613773 of 614400 decoded bytes). Its export return
and after-hash commands were retired by the 70-second experimental-input
fallback, so strict serial frame verification correctly remains false. The
Down command echoed once while raw data was still streaming and again when
the shell executed it; the conservative input oracle rejects the duplicate
echo. The genuine provider logs still show the two Down sequences, but no
third OSD transfer or changed QMP buffer followed. Live post-Down QMP buffers
are byte-identical to v5/v6, with the two SHA-256 values above. QEMU and the
bridge returned 0/0, were reaped, and all 87 source inputs stayed unchanged.
Serial SHA-256 is
`34ed20d43390b406107b7dcc020eb4bb9e56fdd943e0cd96a6564bc55be2f4ac`.
The run's `offline-frame-verification/frame-verification.json` and
`offline-input-chain-v7.json` retain both fail-closed verdicts. Direct replay:

```powershell
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-force-app-frame-v7\replay.ps1' -Seconds 300
```

The fresh v8 priority intervention `91a49803cadb4142a8db407487af5373`
used a separate ext2 clone and retained the same one-instruction original App
scheduler patch. A guarded provider changed only the low six OSAL message
priority bits from 1 to 4 for the exact original Down press/release callback
pair; the native original sends returned zero. The original App receive trace
then matched queue `0x5503e0`, the two 32-byte callback digests, priority 4,
and the Down press/release order. The original OSD producer reported a new
614400-byte frame after those receives. The guest's stable source/copy/source
SHA-1 was `2052e53aaadee3ee2e7f98b633d31f63b979764b` for BEFORE S1 and
`93ab52ac4e95dc0d5ba4631f395ed476bec31224` for AFTER S0. Independent
read-only QMP dumps of the producer-reported physical buffers matched both
SHA-1 values. The decoded original **My Menu / My Menu Setting** frame moved
its orange focus from **Add Item** to **Sort Item**: 446 pixels changed, all
in those two rows. The source-bound v9 offline crosspath receipt over the
unchanged v8 serial/QMP bytes has `native_menu_navigation_verified=true` and
`G5_verified=false` because this run had no direct original App controller
dispatch PC trace. Its SHA-256 is
`e9d55ae75cdbddda742c272077d26d4ea593690efaf4da98b26654c5f2fafbdb`.

The v8 run remains a **negative strict-pipeline result**. Its final evidence
writer raised `NameError: down_native_pair_observed`; `runtime_exit_code=1`,
while the bridge returned 0, both processes were reaped, and all 95 source
inputs stayed unchanged. Its v8 input parser rejected a complete provider
value followed on the same UART line by a kernel UDIF log, and its serial
frame extractor rejected missing base64 chunks despite stable guest frame
hashes. The original failure files, serial SHA-256
`87bf802ddaa2ea85af76d053d86049064fc6ccfd0eb1159ccb801c8f7b7c9d11`,
QMP raw buffers and screenshots remain under the v8 run nonce. The v9
read-only parser accepts only a bounded, source-tagged complete value with a
recognized kernel-log suffix; its negative fixtures reject arbitrary or
multiple appended records. The crosspath proof does not silently repair or
overwrite the failed v8 verdict.

The first fresh PC-trace replay, v9 nonce
`78cc192ba31c49a3845c8621b4cbd099`, stopped before Down injection.
Its guarded original App owner was PID 272 with bias `0x7f6e035000`, but the
owner probe printed the same valid appFw executable map twice (candidate
inventory and owner detail). The runner incorrectly required exactly one
*record* and rejected the two identical records as ambiguous. The
`supervisor-error.txt` preserves
`ValueError('Original App guarded PID/bias lacks one exact RX map')`;
`orchestrator-result.json` retains `G5_verified=false`, zero bridge exit,
reaped processes, and unchanged 105 source inputs. No input or frame claim
comes from this run. A separate revision will require one distinct matching
RX range, still rejecting conflicting ranges, and will propagate the
supervisor error as a nonzero runtime status.

The next fresh run, v10 nonce `094b5947191844f49ed6c5cc2fe9764b`,
passed that owner gate: PID 274, bias `0x7f508a9000`, and two identical RX
rows. Its read-only QEMU plugin recorded the exact original priority-4 Down
press/release messages at App receive, followed by original App dispatch
calls, controller entries with matching return addresses, controller event
codes `0x40003`/`0x40004`, and forward calls. Those ten executed sites and
their registers are in `app-dispatch-v9.jsonl`. The v9 PC oracle had demanded
two additional intermediate sites that did not execute, so its 12-site
verdict correctly remained false as written. A separately versioned ten-site
oracle over the frozen v10 trace passed after checking raw payload digests,
converted key/state, call-frame linkage, owner map, ordering, and no plugin
read errors. This reparse does not alter the original strict verdict.

The original OSD again transferred a 614400-byte frame after Down at
`0x43a5b4100`; the stopped-guest QMP readback SHA-256 was
`34a4491300beab65eb3a1476d7b808faa491617cf1a42fd041f2748cbc7e7f77`,
while the BEFORE menu buffer at `0x438816100` was
`030940c46afdf5c3736b3c623fb64f6490ea7f63c630fd4e700167a281967567`.
However, `AFTER_S0_SNAPSHOT_RAW_COPY_RC_0` shared a UART line with a kernel
UDIF message; the frozen shell ACK parser did not advance to the completed
guest after-frame hash. The v10 wrapper therefore exited nonzero with
`G5_verified=false`, preserving the strict missing-after-frame result.
QEMU and bridge exited 0/0, were reaped, and all 109 source inputs remained
unchanged. A manual second QMP client connected but received no greeting
within 15 seconds; it issued no read. The stopped-guest QMP readback is the
only v10 post-input raw capture and is not by itself a transfer-time hash
witness. The serial SHA-256 is
`a59e7a93fc1cfec4bb3351869593861acb25491e3956fc49e9b39a8e2c7f61fb`.

The fresh v11 run `8664e45fe49f4860bd67fd0fa65c14da` passed the strict
single-key ordinary-menu gate. The private replay first passed 113 preflight
tests and froze 117 source inputs. The private image and provider retained the
assisted menu-entry selector `0x253` to `0x27f`, one App scheduler branch
patch, and one deterministic guarded CP status-zero response. The additional
input-specific intervention changed only the
low six priority bits, 1 to 4, on the original Down press/release callback
messages; the original guest sent and received both messages. A read-only
QEMU PC trace then witnessed the original `appFw.so` receive, dispatch,
controller entry, event codes `0x40003`/`0x40004`, and forward call for each
message. The ten-site oracle checks the original App load bias, raw payload
digests, converted key/state, register/stack continuity, and order. The
plugin does not directly read the current guest PID: its owner label is
bound by the guarded `/proc` PID/start-ticks and unique executable mapping.

The original OSD producer supplied a 640 x 480 RGBA4444 frame before input
at `0x438816100` and a new frame after original App release at
`0x43a5b4100`. Source/copy/source guest SHA-1 checks matched read-only live
QMP reads: respectively
`2052e53aaadee3ee2e7f98b633d31f63b979764b` and
`93ab52ac4e95dc0d5ba4631f395ed476bec31224`. The corresponding QMP
SHA-256 values were
`030940c46afdf5c3736b3c623fb64f6490ea7f63c630fd4e700167a281967567`
and `34a4491300beab65eb3a1476d7b808faa491617cf1a42fd041f2748cbc7e7f77`.
The original **My Menu / My Menu Setting** page changed its orange selection
from **Add Item** to **Sort Item** (row 0 to 1; 446 changed pixels).
`crosspath-verification-v11.json` has `G5_verified=true`, and a separate
offline reparse of the frozen serial, PC trace, QMP receipts and raw buffers
also returned `G5_verified=true` in `independent-crosspath-v11.json`.

The v11 wrapper exited zero; runtime and bridge exited 0/0 and were reaped,
the supervisor wrote no error, and no frozen source input changed. The
runtime used deterministic code and original guest execution, with no
LLM/Agent in its runtime path. The
legacy full-UART base64 frame extractor still returned 2 because unrelated
UART logs interleaved with a raw export. That diagnostic failure is retained
in `orchestrator-result.json`; it is not used to infer a frame. The G5 claim
rests on the bounded guest hash records, original producer events, live QMP
receipts and direct App/controller trace. This proves one assisted original
ordinary-menu Down-input loop, not complete camera or original PCIe peer
hardware emulation. All run files are under the private DevKit
`.local/sony-force-app-frame-v11/runtime-runs/8664e45fe49f4860bd67fd0fa65c14da/`;
the serial SHA-256 is
`b7e004924eec17a60b3ea2b0f6c5cc86d78352e083f8e44b88b6857044fbf9aa`.
The exact fresh replay command is:

```powershell
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-force-app-frame-v11\replay.ps1' -Seconds 540
```

The forced-menu no-input control is audited in the private
`sony-rep-menu-upm-osd1002-frame-p4/RUN-04ce-REPORT.md` (SHA-256
`3acccdb143f335444367edcf3b90b101eb5b5bbec247d578da0c8c5dd1edbbac`);
the preceding combined run is audited in
`sony-rep-menu-upm-policy-p4/RUN-a609-REPORT.md` (SHA-256
`f0b9d0416c97529430cf3caa6996d238445705ba0d3b6b847ae20df8c0c40ea8`).
Both reports distinguish source-derived backend semantics from original guest
events and retain the exact replay commands.

The later read-only swap/guard report is
`sony-swap-guard-readonly-v1/runtime-runs/26cbde74891044bc99ce32486a1473a8/RUN-REPORT.md`
(SHA-256 `b75af6cf7a710079d2f83e27d4279ab4d228fc1bf2338d6d4e4cdd4d766e5939`).
Its bounded diagnostic can be rerun independently from PowerShell:

```powershell
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-swap-guard-readonly-v1\replay.ps1' -Seconds 300
```

The corrected run's private `RUN-759c-REPORT.md` (SHA-256
`451da3738ce03dc743bf8f81830c4feeca7c2c455193fe5f158d85fce2e4f34a`)
audits the 17 CP component requests: F030×3, F023×9, F024×3 and F029×2.
All 17 retained `pending=true`; that is not a failure count. The original
F030 phase-2 path returns zero and emits framework pending and descriptor
packets without a `0x1158` reply, matching this run's internal CP outbox.
Original synchronous dispatch therefore did work, while an independent
component-worker dequeue and later framework/driver application remain
unobserved. The original component constructors and receiver tasks were
started; their queue depth was not exported. Four OSD `1001` registrations
had no observed commit/draw. A guest-side transport-array dump failed because
that BusyBox lacks `od`; the raw per-request host wire records are retained.

The pinned original `osal_uipc.ko` source/disassembly shows that sends to
`0x5503e0` enter a priority-ordered queue and receives remove its head;
neither path filters by message source or length. The original App timer
produces the repeatedly observed 16-byte `0x830128` messages. The v10
observer samples only selected receive ordinals plus exact Menu matches, so
it could not distinguish an unsampled transformed Menu message, queue
backlog, a competing reader, or a provider routing defect. The private
UIPC audit is `.local/molten/sony-app-queue-v10-analysis.md`. The continuous
v11 witness shows that Menu and timer messages enter the same original ID at
the same priority while the queue grows far faster than the App consumes it.
It still does not prove Menu was dequeued, so input delivery remains unverified.
The original kernel queue insertion code preserves FIFO order within a
priority. The P4 trial used the firmware's own `osal_set_msg_pri` at
`libosal_uipc.so+0x6da8` on just the two exact Menu callback messages, raising
their header priority from 1 to 4 without altering the event bytes. Original
App receipt of the press is a stronger input milestone than v11 enqueue, but
there is still no witnessed original Menu handler, release consumption or
input-to-frame transition. The later assertion stack belongs to the original
power-save model worker, not directly to the statically identified Menu
handler; the run does not establish whether the input triggered that worker.
The full intervention guards, original receive lines, source hashes and
failure trail are retained in `.local/molten/sony-menu-priority-p4-report.md`.

The v3 and corrected v4b late snapshots narrow the UI question: original HDMI
configuration returned zero, yet layer 0 still owned the original `0x253`
boot controller. Static original `appFw.so` shows `App::main` itself constructs
and sends a 16-byte `0x00030069` POWER_ON event to its App queue; the boot
controller handles that code and may select ordinary screen `0x280` or
non-UI power/shutdown paths based on native model state. In v4b, its
handler-entered byte stayed zero across two guarded post-HDMI samples, so
the observed bottleneck precedes that write. This does not distinguish an
unconsumed event from a blocked dispatch. The send precedes construction of
the boot controller, so a separate pre-send P4 trial cannot check the late
entered byte at action time; it must use exact live message guards and
independent downstream observation. Physical Menu is not evidence that the
boot controller has opened the shooting/menu owner.

The priority P4 follow-up shows that changing the original POWER_ON message's
scheduling priority did not enter that boot controller: its guarded late
entered byte remained zero even though the original App receive loop saw a
matching 16-byte event. Original `App::main` sends POWER_ON before creating
owner `0x253`; this timing is a plausible explanation, not yet a traced
dispatch cause. The receive is not a MOLTEN `queue_priority` success: the
native queue has no demonstrated stable message ID and the bounded
competing-send observer overflowed. A delayed-send experiment first needs
to prove OSAL message ownership and lifetime.

The DT-only trial isolates the next structural blocker. The Sony GPL
`pcie-designware` source defines PHY sideband offset `0x20000` and
register `+0x18`; with original PHY base `0xf11c0000`, the faulting read
is `0xf11e0018`. The guest printed `PCI0: handover` before the fault.
In the original Sony `pcie-designware-plat.c`, that branch follows a
`PWRUP_RST` reset-status check and skips PHY SRAM programming and the later
PHY-ready wait. The current development SCU reset latch may therefore claim
an initialized controller when none has been modeled. The next narrow
device-model gate is source-defined reset-status and PHY-config register
behavior with honest powerdown/readback, retaining link-down until a peer
handshake is actually observed. Merely mapping the register to zero or
inventing a ready bit would not establish a working host bridge or endpoint.
The source comes from the locally retained original Sony Linux 2.00 archive
(SHA-256 `8521c6ff1c8760516b53ad318b95b5c868171e3a3b85d94b09730289cae1aa6f`):
`linux-kernel/drivers/pci/dwc/pcie-designware-plat.c` lines 180, 191–205,
243–250, 301–319 and 599–603, plus `pcie-designware.h` lines 146–159.

The v5 helper only reads `/tmp/qe-normal-input-status.bin` and calls
`stat` on input/frame files. It does not produce an input event, write a
frame or alter PCIe state. Its source and binary SHA-256 are respectively
`3716bca5d0692f09126ec67172a9c1b3c011a14299d7033f6ecdadc31bc750b8`
and `b08d6771a766045d1670d7097ba7c73f7b7e156b8605101b0621097c2792a2c7`.
The private guest inode is executable mode `0755`. The original image and
driver script hashes were unchanged after the v5 run. The frozen provider
binary has SHA-256
`6c7e3f0ff5380fe977c57e5aa42398d72e7e72c7939dd0cb134fb343546495c7`;
its included input source has SHA-256
`5b84350b02ff765bf4b7189c69445c457e691a4f8959677b86d488f43d840a8a`.
That source parses each 36-byte record, starts each delivery result at `-38`,
and writes status words `{last_sequence, accepted, rejected,
last_delivery_result}`. The v5 words therefore mean **two records consumed,
zero accepted and two rejected**. The bridge prints its generic "delivered"
line even when the Boolean success field is zero. The `-38` can be either a
pre-send sentinel or the original `native_async_reply` return; the current
log cannot distinguish those causes. Neither message proves the original App
received the Menu key. The single 72-byte file copy does not prove acceptance
or an input-to-frame loop.

The v6 telemetry resolves that ambiguity: `-38` is the bridge's **pre-send**
sentinel, and no native call was attempted. An independent disassembly audit
of the original `libObj.so` and `libosal_utm.so` identifies `node+0x10` as
the positive thread ID returned by `osal_create_waitable_thread`, rather than
an error field. The worker sets `node[0x21]=1` before receiving its queue.
The exact guest binaries hash to
`9044c88ca1688bbbd0dc7e769cde25d880c7a1df51078080a026c4877ba622d3`
(`libObj.so`) and
`d9510b4ce1d7e59a0f2e9b5be14624a4cece6f514879e8a6d23ce890a46d25eb`
(`libosal_utm.so`); the read-only disassembly trail is retained in
`.local/molten/sony-input-thread-field-v6-analysis.md`. The positive TID is
only a readiness clue: worker state can later change, and a successful native
enqueue would still not prove that App handled the event.
Thus the frozen bridge's zero-valued gate is inverted for this original
runtime. A private follow-up can test the native send with the original
owner, active-worker, key-list, callback and distinct-queue gates retained;
its return and a guest App/frame observation remain separate acceptance gates.

The v7 follow-up did make that native call twice. Its zero returns are
send/enqueue acknowledgements; the last logged `actual App receive` predates
input binding, but that particular probe watches queue `0x81cf61c2`, not the
InfraInput destination `0x7ae0c4`, so its silence cannot prove the InfraInput
worker did not dequeue. Both frame files were absent. Do not map returns to
MOLTEN `input_delivered`, which requires an independent consumer observation.
Original `libosal_uipc.so` (SHA-256
`4750dafb656157244738ba6fa5f7bb270912e620985a73b8cdac4db2905f657c`)
routes queue IDs with bit 15 set through its native local receive path; the
input queue has that bit set. The source-grounded receive-path audit is in
`.local/molten/sony-input-v7-consumer-audit.md`. A consumer-side observation
must see the original worker return from `osal_rcv_msg_tmo`, or the registered
callback, with matching event bytes.
The v8 receive witness observed exactly that return twice, from original
`libObj.so+0xd2994`. The raw log label `FNV32` refers to a 32-byte event;
the computation is **FNV-1a 64-bit**. The retained
`.local/sony-pcie-input-consumer-v8/payload-verification.json` independently
recomputes `cb6f241031f39328` and `0fea1169c72604b8` from the pinned
72-byte host file, whose SHA-256 is
`9caad9b081037ba90ed5a739756810aee902fc5852558adf7a600a710004f017`.
In v8, the subsequent callback and original menu frame remained unobserved.
The v9 follow-up independently observed both original App callback calls.
For the press, the callback-send payload digest was
`510a161d47cfaf04`, rather than the host/dequeue digest
`cb6f241031f39328`. Original `libObj.so+0xd2ad4` writes the registered
key-list word to event offset `+12`; setting those four bytes to
`ff ff ff 7f` (`0x7fffffff`) reproduces the callback-send digest exactly.
The release digest remains `0fea1169c72604b8`. These observations confirm
the original input path transformed and forwarded the two records; they do
not show the recipient of App queue `0x5503e0` or a menu frame. The inferred
press field comes from the original write instruction and a constrained
digest calculation, not a direct dump of the outgoing bytes. The full v9
report is `.local/sony-pcie-input-callback-v9/RUN-REPORT.md` (SHA-256
`f097ee05249c447bd5da1152eb06138bb5ec1444971be3768113b4bd814c926d`).
The private provider and image SHA-256 are
`0e1335d716b409c0e3abd8d4b90afc923a807c4f4535cdf7af7458a5f4ebb62d`
and `55831bf47ffc2ddbf72e54f695dcd409836696511d99d4f54ef4e1ef1c037dc6`.
For v7, the private delta and run notes are retained in
`.local/sony-pcie-input-gate-v7/gate-correction.diff` (SHA-256
`1f91bd3cc9fb290392c64643b8e154f002530ad68a315f031575dae116fcec62`)
and `RUN-REPORT.md` in that directory. The private provider and image hash to
`211aebe16cd2210d43d03d465ab069c6687e2aa84367d23d567615adeb33d12e`
and `1b2c5e498704e6185b6f143aefa77d279246beaf568c2d9c264742116bb91371`.

## MOLTEN negative replay

`docs/molten-sony-pcie-v2.md` documents the frozen read-only recipe and
exact offline evidence-parser replay command. The frozen trace is
`1605df92ba29cca831273a7b0e5ebe486ee92de891bdd2df009f3fe14113ae68`.
Two separate parser processes over the finished v2 logs produced 567
observations, zero interventions and the same `BUDGET_EXHAUSTED` status
waiting for an independent `queue_commit`. This is a reproducible
**negative evidence** result, not a fresh-QEMU replay or a positive menu
result. See the separately retained live trial logs above for fresh
QEMU executions.

The private launchers use QEMU, Python and the existing CP sidecar;
no LLM or agent is invoked at run time. They pin the source image and
original modules by hash, record private changes, impose a guest-time
limit and reap only the processes they own. Run them from PowerShell in
the repository root:

```powershell
wsl -d Debian -u root --exec python3 '/mnt/f/New project/QEMU-Everything/.local/molten/sony-pcie-input-status-trial.py' 160
wsl -d Debian -- python3 '/mnt/c/Users/derri/Documents/New project/Hasselblad-X-System-QEMU-DevKit/.local/sony-pcie-dt-only-trial/replay_dt_only.py'
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-input-gate-v7\replay.ps1' -Seconds 160
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-input-consumer-v8\replay.ps1' -Seconds 160
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-input-callback-v9\replay.ps1' -Seconds 160
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-app-consumer-v10\replay.ps1' -Seconds 160
wsl -d Debian -- python3 '/mnt/c/Users/derri/Documents/New project/Hasselblad-X-System-QEMU-DevKit/.local/sony-pcie-phy-scu-trace-v10/replay_dt_only.py'
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-panel-p4-1100\replay.ps1' -Seconds 160
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-panel-p4-1100-v9\replay.ps1' -Seconds 300
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-app-consumer-v11\replay.ps1' -Seconds 160
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-menu-priority-p4\replay.ps1' -Seconds 160
& 'F:\New project\QEMU-Everything\.local\molten\sony-app-owner-probe\replay_owner.ps1' -Seconds 160
& 'F:\New project\QEMU-Everything\.local\molten\sony-app-owner-probe\replay_owner_v2.ps1' -Seconds 160
& 'F:\New project\QEMU-Everything\.local\molten\sony-app-owner-probe\replay_owner_v3.ps1' -Seconds 160
& 'F:\New project\QEMU-Everything\.local\molten\sony-app-owner-probe\replay_boot_gate_v4.ps1' -Seconds 160
& 'F:\New project\QEMU-Everything\.local\molten\sony-app-owner-probe\replay_boot_gate_v4b.ps1' -Seconds 160
& 'F:\New project\QEMU-Everything\.local\molten\sony-app-owner-probe\replay_power_priority_v9f.ps1' -Seconds 160
& 'F:\New project\QEMU-Everything\.local\molten\sony-app-owner-probe\replay_power_delay_v9g.ps1' -Seconds 160
& 'F:\New project\QEMU-Everything\.local\molten\sony-app-owner-probe\replay_power_presend_v9h.ps1' -Seconds 160
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-pcie-panel-postmenu-p4\replay.ps1' -Seconds 300
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-menu-entry-p4\replay.ps1' -Seconds 200
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-rep-menu-entry-p4\replay.ps1' -Seconds 200
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-rep-menu-graphics-observer\replay.ps1' -Seconds 200
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-rep-menu-osd1000-p4\replay.ps1' -Seconds 200
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-rep-menu-upm-policy-p4\replay.ps1' -Seconds 200
& 'C:\Users\derri\Documents\New project\Hasselblad-X-System-QEMU-DevKit\.local\sony-rep-menu-upm-osd1002-frame-p4\replay.ps1' -Seconds 300
```

Each invocation creates a new private image and run nonce. The first
command adds the read-only input helper to the private image. Its current
wrapper also terminates only its own process group if the coupled
supervisor fails or times out; the recorded v5 run completed normally
and reaped both owned processes. The exceptional-cleanup change was syntax
checked after that v5 run; the recorded live outcome used the earlier wrapper
on its normal completion path. The second
command is a separate DT-only failure probe without the CP sidecar; its
wrapper also accepts `--preflight-only` to check inputs and compile the
DTB without launching QEMU. The actual
QEMU command, guest serial, plugin trace, QMP reads, process result and
input intervention, when applicable, are retained in that nonce's run directory. For the
MOLTEN offline replay, follow the commands in `docs/molten-sony-pcie-v2.md`.

The generic MOLTEN regression, including native randomized-layout and negative
cases, was run from the repository root with the pinned backend manifest:

```powershell
wsl -d Debian -- /bin/sh -lc 'MOLTEN_BACKEND_MANIFEST="/mnt/f/New project/QEMU-Everything/.local/molten/build-manifest.json" /home/dy09/.venvs/qe-molten/bin/python -m pytest tests/adaptive -q -rs'
```

An earlier regression that refuses to count input send/enqueue without a
later independent `input_delivered` observation returned `62 passed` in
35.45 seconds. The current pinned Debian WSL suite, after the generic
`queue_priority` contract and randomized-layout replay cases were added,
returned `95 passed` in 35.61 seconds. The frozen Sony v2 read-only
evidence replay was rechecked with `python -m devkit.adaptive.transport
verify` (exit code 0): trace
`1605df92ba29cca831273a7b0e5ebe486ee92de891bdd2df009f3fe14113ae68`,
567 journal records, zero actions, observed and expected status both
`BUDGET_EXHAUSTED`. That verification checks a retained negative trace; it
does not boot Sony again.

After adding the generic P4 provenance contract, the same pinned Debian WSL
command above returned `102 passed` in 35.97 seconds. That run includes the
randomized-layout, negative-oracle and independent replay tests; the Sony
force-frame trial is a separate firmware-specific experiment.

The later generic `queue_priority` addition, including receipt-field witness
binding, was checked on Windows with `python -m pytest tests/adaptive -q -rs`:
`78 passed, 10 skipped` in 1.58 seconds. The skipped cases need Z3 or the Linux cross-compiler/QEMU backend;
this Windows check is not a replacement for the pinned Linux native chain.
The unchanged frozen Sony v2 negative recipe was independently verified again
under explicit Debian WSL with `/usr/bin/python3 -m devkit.adaptive.transport
verify`; it still has 567 journal observations, zero actions, four completed
steps, trace SHA-256 `1605df92ba29cca831273a7b0e5ebe486ee92de891bdd2df009f3fe14113ae68`
and `BUDGET_EXHAUSTED`. Its historical transport source hash remains
`eb19f31042c64987be98247009caa11d5cfa0f66e32386ce141aa4cb790cc06f`.
No old recipe, journal or result was rewritten to obtain this check.

A wider Sony regression attempt was not green: collection of
`tests/test_camera_sony.py` required an unavailable `cryptography` package in
the WSL test environment, and the separate historical LDEC packaging check
found that tracked `ldec.c` currently hashes to
`c555c83c09f24d249ceec7a02a42337e8a8028e56d5efb31d670531b5e81c3cc`
while `ldec-validation-20261011.json` records
`44ad5bbd609695361e6a2ccf868eede046203a82ef2ad6159713cf01a663897e`.
Neither was changed merely to make this PCIe follow-up appear green; those
checks are not included in the `95 passed` adaptive-suite result.

The v11 trial meets the bounded ordinary-menu acceptance gate for a single
Down input: identifiable original menu content, witnessed original App and
controller delivery, a subsequent different guest-produced frame, and an
independent source-bound replay. The original PCIe peer transport and broader
camera operation remain outside that result.
