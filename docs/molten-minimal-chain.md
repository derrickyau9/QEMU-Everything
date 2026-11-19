# MOLTEN v0.1: executable research chain

This implements the blueprint's first AArch64 microfirmware chain. It is an
independent QEMU 10.2.4 research machine, not a Sony ordinary-menu result.
The runtime uses no LLM or agent. Existing camera profiles and Sony paths stay
separate; unknown ELF identities enter through structural program headers.

## Run in Linux / Debian WSL

Required: Python >=3.11, a C toolchain, Ninja, pkg-config, GLib development
headers, QEMU's build prerequisites, and `aarch64-linux-gnu-gcc`/binutils.
All commands below run from the repository root inside Linux/WSL.

The research machine reserves `0x47f00000..0x48000000` for virt's generated
DTB and `0x47e00000..0x47f00000` for the stack. ELF PT_LOAD segments may use
the lower admitted RAM, including its first MiB. This prevents the stock
virt DTB placement from invalidating legitimate random loading addresses.

```sh
python3 -m venv --system-site-packages .venv-molten
.venv-molten/bin/pip install capstone==5.0.6 z3-solver==4.15.4.0 pytest
python3 native/molten/build.py --work-dir "$HOME/.cache/qe-molten" --jobs 4
python3 research/fixtures/aarch64_menu/build.py --seed 17 --output .local/fixture-17
.venv-molten/bin/python -m devkit.cli explore .local/fixture-17/firmware.elf \
  --campaign .local/campaign-17 \
  --backend-manifest "$HOME/.cache/qe-molten/build-manifest.json"
.venv-molten/bin/python -m devkit.cli replay .local/campaign-17/recipe/recipe.json \
  --output .local/replay-17 --verify-interaction
```

The builder downloads the upstream archive and verifies the same SHA-256 pin
used by the Sony build. It never changes the installed QEMU. Use a fresh output
directory for a run, or `explore --resume` for the same campaign identity.
Replay requires only the Python standard library and the pinned QEMU binary;
Capstone and Z3 are exploration dependencies. The bundled ELF and native model
are hash checked before launch. `--backend-manifest` can relocate an identical
backend binary for replay.

## What executes

1. The generator relocates a stripped ELF and changes its unknown MMIO ports,
   register allocation, mask, secret, and instruction padding. `truth.json`,
   assembly, C source, and the debug ELF belong to the evaluator. Exploration
   copies only the stripped input into the campaign.
2. QEMU runs original AArch64 instructions, single vCPU, single-thread TCG and
   fixed icount. An unknown 32-bit MMIO load blocks synchronously before
   committing. Its PC, all GPRs, virtual time and ordered prefix are captured.
   This is a consistent **local boundary**, not a whole-machine checkpoint.
3. Zero responses expose a repeated polling condition. A bounded local
   disassembler/bit-vector interpreter derives the branch condition and Z3
   solves it. Bit influence compresses the relevant input bits. The first
   semantics envelope is LDR, NOP, unshifted 32-bit AND/EOR/ORR/MOV, CMP and
   a conditional backedge; other instructions become explicit gaps. This
   implementation does not substitute for the later Triton differential gate.
4. The smallest constant-response model is tried concretely. Different input
   generates a counterexample. New concrete observations constrain one shared
   `xor_latch` model: writes update a uint32 latch; reads return
   `(latch XOR key) AND mask`. The grammar has two model forms and no arbitrary
   scripts, PC-specific answers, pixel writes or unconstrained memory actions.
5. QEMU's native model executes the frozen rules. A native surface sink only
   observes pixel stores from input code. The firmware renders its own menu
   and glyph resources. Input is delivered through the original load channel.
6. No-input / Next / Enter controls must differ; Enter / Back must restore the
   initial surface. Three input sequences excluded from synthesis each replay
   five times in new Python and QEMU processes. Signatures include registers,
   PC, virtual time, MMIO events, inputs and original surface hashes. The child
   processes import no solver or disassembler. Tests also install an import
   blocker that rejects any attempted solver access.

The result is `SUCCESS_TESTED`, `scope=unclassified`, G0–G5 **under the recorded
finite microfirmware contract**. This contract has no 10-minute soak; it is not
the Sony acceptance contract. The report preserves that obligation along with
ordinary-menu discovery, full snapshots, fair multi-world search, wider ISA
semantics, DMA, IRQ, IPC and P3/P4 work. There is no arbitrary-firmware guarantee.
The generic research board ABI is declared prior knowledge, not an inferred
camera board. F0 here means the executable plus that generic backend, with no
per-variant addresses or truth given to the algorithm.

An external, finite GPIO/IRQ/DMA/shared-queue observation and intervention
contract is described in [molten-transport.md](molten-transport.md). It requires
live adapter witnesses for positive copies and retains failed attempts. The
Sony v2 adapter in [molten-sony-pcie-v2.md](molten-sony-pcie-v2.md) exercises its
read-only negative path over finished logs; it is not a native Sony transport
model or a fresh-guest replay.

## Bounded P4 branch-patch provenance

`devkit.adaptive.p4` records an assisted code-branch experiment separately from
the P0–P2 transport recipe. Its strict `molten-p4-branch-v1` recipe pins the
original component SHA-256, executable ELF relocated virtual offset and mapped
file offset, exact old/new bytes, a surrounding context of at most 256 bytes
with four bytes on each side and its SHA-256, plus a short reason. It accepts
a same-length change of at most 16
bytes. The module maps the relocated offset through the component's executable
ELF program header and checks every original byte before writing. It creates a
new, exclusive private component copy and never writes the source. Each attempt
retains a chained intent, applied readback, optional raw runtime-artifact
observation, failure and rollback receipt. Rollback removes only the verified
private copy; it refuses a copy changed after application and preserves that
failure for investigation.

```sh
python -m devkit.adaptive.p4 apply trial.json original/component.so trial-001
python -m devkit.adaptive.p4 observe trial-001/receipt.json trial-001/serial.log
python -m devkit.adaptive.p4 verify trial-001/receipt.json
python -m devkit.adaptive.p4 rollback trial-001/receipt.json
```

The `observe` command pins raw evidence by SHA-256 but does not interpret it.
Every P4 receipt has `ui_verdict=UNVERIFIED`; original guest-code execution, a
guest-produced frame, and an input-caused different frame require their own
independent oracle and fresh replay. This contract does not place the patched
component into a guest image, change QEMU memory, or call an LLM or agent at
runtime. A P4 run must be reported as assisted even when downstream UI proof
passes.

The later [A7 IV PCIe peer and input follow-up](molten-sony-pcie-followup-20261017.md)
records fresh QEMU trials, the `pcie_i` and PHY blockers, IRQ/queue observations,
and the rejected Menu input attempt. It does not claim ordinary-menu success.

## Evidence and resumption

`input-manifest.json` records identity, ELF segments and knowledge level;
`plugin-capability-probe.json` records actual API-v5 register/memory probes;
`frontier.sqlite` keeps unfinished tasks; `constraints/` contains SMT queries
and original instruction windows; `models/` contains content-addressed rules.
Each `workers/worker-0/` run owns its log, command, events, pixels and result.
`recipe/` contains a solver-free recipe and the original executable.

No incomplete snapshot is exported. Pending tasks resume through clean restart
and prefix replay. A completed campaign's `--resume` checks identities and
returns the existing result without rerunning successful tests. Worker death,
timeout or unsupported semantics is not an unreachable-firmware proof.

```sh
MOLTEN_BACKEND_MANIFEST="$HOME/.cache/qe-molten/build-manifest.json" \
  .venv-molten/bin/python -m pytest tests/adaptive -q
```

The native suite generates additional layout seeds and checks the inferred
model against evaluator-only truth on unseen uint32 commands. Negative cases
cover present-without-pixels, a noninteractive static logo, missing local
instruction semantics, an idle loop, changed input identity and replay drift.
These are regression variants, not an untouched Sony blind-transfer benchmark.

The API probe is grounded in the [fixed 10.2.4 plugin header](https://github.com/qemu/qemu/blob/v10.2.4/include/qemu/qemu-plugin.h).
It tests same-value register writes and temporary RAM read/write/restore; it
does not claim PC mutation, TB coherence, transactional writes or device-state
snapshot support. The synchronous device boundary fills only the needed first
research gap.
