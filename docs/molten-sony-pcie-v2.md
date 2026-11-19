# Sony A7 IV PCIe transport: bounded read-only evidence

This record concerns the F3 private AP/CP development run
`70d215d855e9433c98f916860e4cac9e`. The original A7 IV kernel and
`pcie-if.ko` ran in a copied runtime image with the separately documented
development boot and CP UART sidecar. The run ended with QEMU and bridge exit
code 0, both owned processes reaped and no tracked CP source changes. It did
not show the original ordinary menu or an input-to-original-frame loop.

The local adapter `.local/molten/sony-transport-evidence.py` reads the finished
run's original serial log, read-only QMP snapshot and QEMU plugin trace. It
emits MOLTEN `cpu_access` observations for **AP CPU** GPIO-register and shared
RAM reads/writes, plus `irq_count_snapshot` for guest IRQ 91/92 counters. It
does not emit `gpio_edge`, `dma_commit`, `queue_commit` or `irq_delivered` from
an AP write. The plugin does not instrument the Unicorn CP sidecar or a PCIe
peer, and its retained queue records reached the 512-entry cap. A missing peer
record in that file cannot prove that no peer ever acted.

The bounded observations are:

| Source | Actual result | Scope |
| --- | --- | --- |
| QEMU plugin API 5 | 49 GPIO accesses (47 writes, 2 reads); 580 AP queue RAM accesses, 506 nonzero, 512 retained | AP CPU transactions only |
| Original guest serial and `/proc/interrupts` | `pcie_if` live; IRQ 91 `pcie_ep_int_hi` and IRQ 92 `pcie_ep_int_lo` registered, both with four CPU counts of zero | Guest snapshot during this run |
| Read-only QMP | Original public callback table and async/sync pointers matched their module sections; first 256 bytes at physical `0x404c00000` were zero | A stopped-guest snapshot, not peer transfer proof |

The negative MOLTEN recipe checks an AP queue write, callback registration and
both zero IRQ counts, then reaches the bounded end-of-source marker and asks
for an independently observed peer queue commit. No such typed observation is
available from the captured sources. The actual outcome is
`BUDGET_EXHAUSTED`, with **zero transport actions**. It means the peer commit
remains unresolved under this observation contract; it is not an unreachable
firmware proof. The frozen recipe records that failed status and exact semantic
trace so a later source change or parser drift is detected.

For a later positive Sony DMA model, an accepted `pcidma_transfer_async`
request or an AP queue write is insufficient. The model needs independently
observed PCIe link connection, WR/RD DMA doorbell operation, hardware DMA
completion through the original callback, and AP/CP shared-memory alias and
ownership proof before copying bytes or routing an IRQ. This F3 run supplies
none of those prerequisites. No synthetic ACK, peer payload, GPIO level or
interrupt is introduced by the adapter.

The recipe's artifact list hashes the private runtime image, its original base
image, original A7 IV kernel, CPAPP and `pcie-if.ko`, built QEMU, board sources,
trace plugin/source, private runner and CP sidecar sources, evidence logs,
MOLTEN transport core and adapter. `adapter-provenance.json` maps each role to
the exact path, byte count and SHA-256. The recipe and two attempt directories
are in `.local/molten/sony-transport-v2-verified-70d215d855e9433c98f916860e4cac9e/`.
The two attempts replay the **evidence parser** in separate Python processes
over immutable finished logs. They are not independent fresh Sony QEMU boots.
Both attempts recorded 567 observations and zero actions, with identical
semantic trace SHA-256
`1605df92ba29cca831273a7b0e5ebe486ee92de891bdd2df009f3fe14113ae68`.
The frozen recipe SHA-256 is
`44a113a6666074e4294def48b9022fd8da0d3f0c69b6966ab1370cf29211efe6`.
The bound generic transport core SHA-256 is
`eb19f31042c64987be98247009caa11d5cfa0f66e32386ce141aa4cb790cc06f`.

From the QEMU-Everything repository root in Debian WSL, record and freeze a new
copy with a fresh output directory:

```sh
python3 .local/molten/sony-transport-evidence.py record \
  --run '/mnt/c/Users/derri/Documents/New project/Hasselblad-X-System-QEMU-DevKit/.local/sony-pcie-coupled-trial-v2/runtime-runs/70d215d855e9433c98f916860e4cac9e' \
  --output .local/molten/sony-transport-v2-new
python3 -m devkit.adaptive.transport freeze \
  .local/molten/sony-transport-v2-new/recipe.json \
  .local/molten/sony-transport-v2-new/attempt \
  --output .local/molten/sony-transport-v2-new/frozen.json
python3 .local/molten/sony-transport-evidence.py replay \
  --run '/mnt/c/Users/derri/Documents/New project/Hasselblad-X-System-QEMU-DevKit/.local/sony-pcie-coupled-trial-v2/runtime-runs/70d215d855e9433c98f916860e4cac9e' \
  --frozen .local/molten/sony-transport-v2-new/frozen.json \
  --output .local/molten/sony-transport-v2-new/independent-evidence-replay
python3 -m devkit.adaptive.transport verify \
  .local/molten/sony-transport-v2-new/frozen.json \
  .local/molten/sony-transport-v2-new/independent-evidence-replay/attempt
```

`verify` returning 0 confirms artifact identity, journal chain and equality of
the frozen negative trace/status. Its reported transport status remains
`BUDGET_EXHAUSTED`. All private firmware and runtime files remain local.
