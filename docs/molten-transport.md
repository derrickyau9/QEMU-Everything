# MOLTEN transport experiment contract

`devkit.adaptive.transport` records and replays bounded transport experiments
against an external QEMU adapter. It has no camera addresses, payload templates,
LLM or agent calls. It does not assert that a displayed image is an original
menu. The firmware's normal menu and input loop need separate visual and guest
execution evidence.

The contract is `molten-transport-v1`. Each recipe declares the source image and
QEMU build as immutable artifacts, the guest address space used by its adapter,
disjoint writable buffers, event/action/wall/payload budgets and a finite list
of event-triggered steps. A step can wait for a typed observation, execute one
action and wait for a typed postcondition. Unrelated observed events remain in
the attempt journal. `next_event` must capture a real target event and honor its
timeout; source identity and the transport adapter remain part of the evidence
boundary.

The six action forms are deliberately narrow:

| Action | Required observed cause | Concrete effect |
| --- | --- | --- |
| `gpio_mirror` | A named GPIO edge and level | Drive another named GPIO endpoint to the *same* level and read it back. |
| `dma_copy` | Source, destination and length in a DMA descriptor, then a live capability proof | Require connected link, observed doorbell and verified address translation before reading any guest memory. Copy actual source bytes into a declared writable region, then read back and match an independently observed `dma_commit` digest. |
| `queue_copy` | Source, destination and length in a queue descriptor, then a live capability proof | Require queue ownership, ready peer and verified address translation before reading any guest memory. Copy actual source bytes, read back and match an independently observed `queue_commit` digest. |
| `queue_priority` | A producer-owned, live queue message with queue, owner, opaque message ID, length, payload digest and current priority | Require a capability bound to that message and requested higher priority. Call the original priority setter, check native return and header/payload readback, then require an independent original consumer receipt. |
| `irq_route` | A nonempty DMA/queue commit with a payload digest | Assert the declared IRQ; an independent `irq_delivered` observation must follow to count as verified. |
| `deliver_input` | An input request with the same code | Deliver the code through the adapter's declared original input channel; an independent `input_delivered` observation with the same code must follow. A later original frame and pixel digest are separate UI proof. |

For input, a zero return from an OSAL send or queue enqueue is only a send
receipt. An adapter must not turn it into `input_delivered`; it needs a
consumer-side observation with event identity, such as an original worker
dequeue or callback. Even a witnessed dequeue does not by itself prove that
the original App produced a menu frame. The Sony experiments in
[molten-sony-pcie-followup-20261017.md](molten-sony-pcie-followup-20261017.md)
retain each of those boundaries separately.

`queue_priority` is a scheduling intervention, not a DMA or PCIe transport
repair. The recipe must select a `queue_message_pending` trigger with every
required field and a `queue_consumed` postcondition with the same queue,
message ID, length, payload SHA-256 and raised priority, plus a named consumer. The adapter may
emit the latter only from an independent original consumer witness. It must
explain how the opaque ID correlates producer and consumer observations; if
there is no native ID, it needs a bounded uniqueness argument using unchanged
bytes and a complete competing-message trace. A native setter or queue send
return alone cannot satisfy this postcondition. A recipe containing this
action reports `QUEUE_SCHEDULING_VERIFIED` only after every declared
postcondition is observed. That verdict does not assert peer link recovery,
original UI processing or a frame.

Before changing a live message, `authorize_queue_priority(event, priority)`
must prove `producer_owns_message`, `pre_send_live`,
`queue_binding_verified`, `native_setter_verified`,
`priority_field_mask_verified` and `payload_immutable`. The proof binds the semantic
message, queue, owner, ID and requested priority, and states the original
setter's maximum supported priority, successful return value and exact
priority-bit mask in its native message header.
`set_queue_priority(event, priority)` must call that original setter and
return its status, before/after priorities, payload digests and original header
bytes before and after. The core checks the header XOR against the proven
native priority-bit mask; unrelated header changes fail. Both payload digests
must match the observed original bytes. Its `witness_sha256` must equal the
canonical SHA-256 of every other receipt field, so the journal and independent
verifier reject an unbound digest, including after journal re-chaining. This
binds the reported receipt fields; it does not authenticate a native setter
call by itself. The adapter must retain the raw original setter/readback log
and make its source and trace provenance auditable. A denied, missing or contradictory
proof/receipt stops the run and retains the intent and failure. The adapter
must preserve raw setter, readback and consumer logs and pin its original
binary/source and proof witness in recipe artifacts. The core checks the proof
witness digest against those artifacts; the adapter is still responsible for
retaining the raw target logs behind its reported receipt. This generic action is
not a claim that a Sony live adapter has passed these gates.

The engine never generates a DMA payload or an ACK value. A copy receipt stores
the SHA-256 of the source and destination bytes. Before the first memory read,
the adapter's `authorize_copy(kind, descriptor)` must return a proof with exactly
`kind`, `descriptor_sha256`, `witness_sha256` and three Boolean gates. For
`dma_copy` the gates are `link_connected`, `doorbell_observed` and
`translation_verified`; for `queue_copy` they are `queue_owned`, `peer_ready`
and `translation_verified`. `descriptor_sha256` must bind the observed semantic
descriptor, and every gate must be true. A false or missing proof stops before
memory access. The proof itself is journaled and bound to the copy receipt.
Retain the raw witness file and include its SHA-256 in recipe artifacts: the
core checks the proof structure, digest and declared Boolean results, while
the adapter must substantiate the external link/doorbell/ownership facts.
`irq_route` records the commit digest that caused the interrupt. Every action
writes an intent record **before** touching the target, followed by its
capability proof when applicable, receipt or retained failure record.
Failure, identity drift, replay divergence and exhausted budgets are distinct
outcomes. `TRANSPORT_VERIFIED` means all declared transport postconditions were
observed; `QUEUE_SCHEDULING_VERIFIED` means all declared postconditions
including the independent queue consumer were observed. Neither is a menu or
hardware-equivalence verdict.

A minimal adapter supplies `next_event(timeout_seconds)`. It also implements
only the operations used by the recipe:

```python
from devkit.adaptive.transport import run

result = run('recipe.json', adapter, 'attempt-001')
print(result['status'], result['trace_sha256'])
```

The adapter methods are `drive_gpio(endpoint, level)`, `read_gpio(endpoint)`,
`authorize_copy(kind, descriptor)`, `read_memory(address, length)`,
`write_memory(address, bytes)`, `assert_irq(number)` and
`deliver_input(target, code)`, `authorize_queue_priority(event, priority)` and
`set_queue_priority(event, priority)`. Memory addresses are integers in the recipe's
`address_space_id`; observations and recipe selectors use canonical hexadecimal
strings. Source and destination addresses must refer to the same declared
guest address space, or the adapter must prove and record the cross-domain
mapping under `translation_verified`. A Sony adapter must map those methods to
its controlled QEMU/QMP instrumentation and preserve raw serial, QMP, module
and image evidence beside the MOLTEN attempt directory. That adapter has to
prove that its read and write methods operate in the declared address space.

Example recipe shape, with symbolic values to replace with observed ones:

```json
{
  "schema_version": 1,
  "contract": "molten-transport-v1",
  "address_space_id": "guest-physical",
  "artifacts": [
    {"path": "firmware.elf", "sha256": "<actual lowercase SHA-256>"},
    {"path": "qemu-system-aarch64", "sha256": "<actual lowercase SHA-256>"}
  ],
  "writable_regions": [
    {"start": "0x2000", "end": "0x3000", "owner": "private-shared-buffer"}
  ],
  "limits": {
    "max_events": 128,
    "max_actions": 4,
    "wall_seconds": 30,
    "max_payload_bytes": 4096
  },
  "steps": [
    {
      "trigger": {"kind": "dma_descriptor", "source": "0x1000", "target": "0x2000", "length": 16},
      "action": {"kind": "dma_copy"},
      "expect": {"kind": "dma_commit", "target": "0x2000", "length": 16,
                 "payload_sha256": "<observed payload SHA-256>"}
    }
  ]
}
```

The source and destination addresses in that example explain the schema; they
are not model parameters or discovered Sony locations. Actual experiments
must fill every digest from concrete data and use a private writable memory
range that has been reserved from guest allocation. The example is a schema
illustration; it is not a runnable Sony DMA recipe because it supplies no
link, doorbell, peer or translation witness.

After an initial attempt, freeze its exact semantic trace and observed status
into a recipe **beside the original recipe and relative artifacts**:

```sh
python3 -m devkit.adaptive.transport freeze recipe.json attempt-001 --output frozen.json
```

For a live target adapter, replay with a freshly started QEMU process and
adapter, then independently check the resulting chain and frozen trace:

```sh
python3 -m devkit.adaptive.transport verify frozen.json attempt-002
```

`events.jsonl` is a SHA-256 linked journal of observations, intents, receipts
and failures. `result.json` stores artifact hashes before and after execution,
the semantic trace hash, counters and actual outcome. Semantic trace comparison
excludes virtual time and external witness-file hashes; both remain in the raw
journal. Raw QMP and serial logs must be retained separately because the
journal is tamper-evident only against accidental edits, not a signed hardware
attestation. A frozen failed attempt can also be replayed as the same failure,
which preserves negative evidence rather than relabeling it as success.

The Sony v2 adapter in [molten-sony-pcie-v2.md](molten-sony-pcie-v2.md) runs over
already finished serial, QMP and plugin logs. Its separate-process replay is
an **offline evidence replay**; it does not start another Sony QEMU process.
