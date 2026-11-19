"""Transport tests exercise the recipe through a fake concrete bus adapter."""

import hashlib
import json
import subprocess
import sys
from collections import deque

import pytest

from devkit.adaptive import transport


class Bus:
    def __init__(self, payload=b'original-guest-payload', *, bad_readback=False,
                 gate_overrides=None):
        self.events = deque([dict(kind='gpio_edge', endpoint='producer.pin', level=1),
                             dict(kind='dma_descriptor', source='0x1000', target='0x2000',
                                  length=len(payload), payload_sha256=hashlib.sha256(payload).hexdigest()),
                             dict(kind='queue_descriptor', queue='messages', source='0x2000',
                                  target='0x3000', length=len(payload)),
                             dict(kind='queue_commit', queue='messages', length=len(payload),
                                  payload_sha256=hashlib.sha256(payload).hexdigest()),
                             dict(kind='input_request', code=1)])
        self.memory = {0x1000: payload}
        self.gpio = {}
        self.bad_readback = bad_readback
        self.gate_overrides = gate_overrides or {}
        self.calls = []

    def next_event(self, timeout_seconds):
        assert timeout_seconds > 0
        return self.events.popleft() if self.events else None

    def drive_gpio(self, endpoint, level):
        self.calls.append(('gpio', endpoint, level))
        self.gpio[endpoint] = level
        self.events.appendleft(dict(kind='gpio_edge', endpoint=endpoint, level=level))

    def read_gpio(self, endpoint):
        return self.gpio[endpoint]

    def authorize_copy(self, kind, descriptor):
        gates = {gate: True for gate in transport.COPY_GATES[kind]}
        gates.update(self.gate_overrides.get(kind, {}))
        return dict(kind=kind,
                    descriptor_sha256=transport.identity(transport._semantic_event(descriptor)),
                    witness_sha256=hashlib.sha256(b'concrete-adapter-proof').hexdigest(),
                    **gates)

    def read_memory(self, address, length):
        value = self.memory.get(address, b'')
        return (b'wrong' if self.bad_readback and address == 0x2000 else value)[:length]

    def write_memory(self, address, payload):
        self.calls.append(('copy', address, len(payload)))
        self.memory[address] = payload
        if address == 0x2000:
            self.events.appendleft(dict(kind='dma_commit', target='0x2000', length=len(payload),
                                        payload_sha256=hashlib.sha256(payload).hexdigest()))
        else:
            self.events.appendleft(dict(kind='queue_commit', queue='messages', length=len(payload),
                                        payload_sha256=hashlib.sha256(payload).hexdigest()))

    def assert_irq(self, number):
        self.calls.append(('irq', number))
        self.events.appendleft(dict(kind='irq_delivered', irq=number))

    def deliver_input(self, target, code):
        self.calls.append(('input', target, code))
        self.events.appendleft(dict(kind='input_delivered', code=code))


def recipe_file(tmp_path, payload=b'original-guest-payload'):
    original = tmp_path / 'original.elf'
    original.write_bytes(b'original-firmware-input')
    payload_hash = hashlib.sha256(payload).hexdigest()
    recipe = dict(schema_version=1, contract=transport.CONTRACT,
                  address_space_id='guest-physical',
                  writable_regions=[dict(start='0x2000', end='0x4000', owner='private-shared-buffer')],
                  artifacts=[dict(path='original.elf', sha256=hashlib.sha256(original.read_bytes()).hexdigest())],
                  limits=dict(max_events=30, max_actions=5, wall_seconds=5, max_payload_bytes=128),
                  steps=[
                      dict(trigger=dict(kind='gpio_edge', endpoint='producer.pin', level=1),
                           action=dict(kind='gpio_mirror', target='peer.pin'),
                           expect=dict(kind='gpio_edge', endpoint='peer.pin', level=1)),
                      dict(trigger=dict(kind='dma_descriptor', source='0x1000', target='0x2000',
                                        length=len(payload), payload_sha256=payload_hash),
                           action=dict(kind='dma_copy'),
                           expect=dict(kind='dma_commit', target='0x2000', length=len(payload),
                                       payload_sha256=payload_hash)),
                      dict(trigger=dict(kind='queue_descriptor', queue='messages', source='0x2000',
                                        target='0x3000', length=len(payload)),
                           action=dict(kind='queue_copy'),
                           expect=dict(kind='queue_commit', queue='messages', length=len(payload),
                                       payload_sha256=payload_hash)),
                      dict(trigger=dict(kind='queue_commit', queue='messages', length=len(payload),
                                        payload_sha256=payload_hash),
                           action=dict(kind='irq_route', irq=91),
                           expect=dict(kind='irq_delivered', irq=91)),
                      dict(trigger=dict(kind='input_request', code=1),
                           action=dict(kind='deliver_input', target='original-key-port', code=1),
                           expect=dict(kind='input_delivered', code=1)),
                  ])
    path = tmp_path / 'recipe.json'
    path.write_text(json.dumps(recipe))
    return path, recipe


def test_causal_transport_freezes_and_replays_independently(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    first_adapter = Bus()
    first = transport.run(recipe_path, first_adapter, tmp_path / 'first')
    assert first['status'] == 'TRANSPORT_VERIFIED'
    assert first['steps_completed'] == first['actions_attempted'] == 5
    assert first_adapter.memory[0x3000] == b'original-guest-payload'
    assert first_adapter.calls == [('gpio', 'peer.pin', 1), ('copy', 0x2000, 22),
                                   ('copy', 0x3000, 22), ('irq', 91),
                                   ('input', 'original-key-port', 1)]
    frozen = tmp_path / 'frozen.json'
    transport.freeze_recipe(recipe_path, tmp_path / 'first', frozen)
    second = transport.run(frozen, Bus(), tmp_path / 'second')
    assert second['status'] == 'TRANSPORT_VERIFIED'
    assert second['trace_sha256'] == first['trace_sha256']
    assert transport.verify_attempt(frozen, tmp_path / 'second')['journal_records'] > 10


def test_input_send_without_independent_delivery_observation_is_not_verified(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)

    class SendOnlyBus(Bus):
        def deliver_input(self, target, code):
            self.calls.append(('input', target, code))

    adapter = SendOnlyBus()
    result = transport.run(recipe_path, adapter, tmp_path / 'send-only')
    assert ('input', 'original-key-port', 1) in adapter.calls
    assert result['status'] == 'BUDGET_EXHAUSTED'
    assert result['steps_completed'] == 4
    assert result['actions_attempted'] == 5


def test_failed_copy_retains_intent_and_never_routes_irq(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    adapter = Bus()
    adapter.memory[0x1000] = b'changedx-guest-payload'
    result = transport.run(recipe_path, adapter, tmp_path / 'bad')
    assert result['status'] == 'ADAPTER_FAILED'
    assert 'descriptor digest' in result['error']
    assert not any(call[0] in ('copy', 'irq') for call in adapter.calls)
    lines = [json.loads(line) for line in (tmp_path / 'bad/events.jsonl').read_text().splitlines()]
    assert any(line['kind'] == 'intervention_intent' for line in lines)
    assert any(line['kind'] == 'failure' for line in lines)
    assert not any(line['kind'] == 'intervention_receipt' and line['value']['step'] == 1 for line in lines)


def test_bad_destination_readback_is_a_failure(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    result = transport.run(recipe_path, Bus(bad_readback=True), tmp_path / 'bad')
    assert result['status'] == 'ADAPTER_FAILED'
    assert 'destination readback' in result['error']


@pytest.mark.parametrize('gate', transport.COPY_GATES['dma_copy'])
def test_dma_copy_denied_without_live_gate(tmp_path, gate):
    recipe_path, _ = recipe_file(tmp_path)
    adapter = Bus(gate_overrides={'dma_copy': {gate: False}})
    result = transport.run(recipe_path, adapter, tmp_path / gate)
    assert result['status'] == 'ADAPTER_FAILED'
    assert 'capability denied before memory access' in result['error']
    assert not any(call[0] == 'copy' for call in adapter.calls)
    records = [json.loads(line) for line in (tmp_path / gate / 'events.jsonl').read_text().splitlines()]
    assert any(record['kind'] == 'copy_capability' and
               record['value']['proof'][gate] is False for record in records)


@pytest.mark.parametrize('gate', transport.COPY_GATES['queue_copy'])
def test_queue_copy_denied_without_peer_gate(tmp_path, gate):
    recipe_path, _ = recipe_file(tmp_path)
    adapter = Bus(gate_overrides={'queue_copy': {gate: False}})
    result = transport.run(recipe_path, adapter, tmp_path / gate)
    assert result['status'] == 'ADAPTER_FAILED'
    assert 'capability denied before memory access' in result['error']
    assert any(call[:2] == ('copy', 0x2000) for call in adapter.calls)
    assert not any(call[:2] == ('copy', 0x3000) for call in adapter.calls)


def test_copy_requires_descriptor_bound_live_adapter_capability(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)

    class UnboundBus(Bus):
        def authorize_copy(self, kind, descriptor):
            proof = super().authorize_copy(kind, descriptor)
            proof['descriptor_sha256'] = '0' * 64
            return proof

    adapter = UnboundBus()
    result = transport.run(recipe_path, adapter, tmp_path / 'unbound')
    assert result['status'] == 'ADAPTER_FAILED'
    assert 'not bound to the observed descriptor' in result['error']
    assert not any(call[0] == 'copy' for call in adapter.calls)


def test_copy_refuses_adapter_without_capability_method(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    adapter = Bus()
    adapter.authorize_copy = None
    result = transport.run(recipe_path, adapter, tmp_path / 'missing-gate')
    assert result['status'] == 'ADAPTER_FAILED'
    assert not any(call[0] == 'copy' for call in adapter.calls)
    records = [json.loads(line) for line in (tmp_path / 'missing-gate/events.jsonl').read_text().splitlines()]
    assert any(record['kind'] == 'intervention_intent' for record in records)
    assert any(record['kind'] == 'failure' for record in records)


def test_transport_accepts_typed_cpu_and_irq_observations():
    assert transport.validate_event(dict(kind='cpu_access', region='queue-ram', address='0x1234',
                                         operation='write', width_bits=32, value='0xabcd',
                                         pc='0x4000', vcpu=0), 4096)
    assert transport.validate_event(dict(kind='irq_count_snapshot', irq=91, count=0), 4096)
    with pytest.raises(ValueError, match='operation'):
        transport.validate_event(dict(kind='cpu_access', region='queue-ram', address='0x1234',
                                      operation='commit', width_bits=32, value='0xabcd',
                                      pc='0x4000', vcpu=0), 4096)
    with pytest.raises(ValueError, match='width'):
        transport.validate_event(dict(kind='cpu_access', region='queue-ram', address='0x1234',
                                      operation='write', width_bits=24, value='0xabcd',
                                      pc='0x4000', vcpu=0), 4096)


class PriorityBus:
    def __init__(self, *, gate_overrides=None, proof_overrides=None,
                 receipt_overrides=None, consumed=True, consume_overrides=None):
        self.payload = b'original-queue-message'
        self.payload_sha = hashlib.sha256(self.payload).hexdigest()
        self.pending = dict(kind='queue_message_pending', queue='app-events',
                            message_id='producer-seq-7', owner='original-producer',
                            length=len(self.payload), payload_sha256=self.payload_sha,
                            priority=1)
        self.events = deque([self.pending])
        if consumed:
            event = dict(kind='queue_consumed', queue='app-events',
                         message_id='producer-seq-7', consumer='original-app-loop',
                         length=len(self.payload), payload_sha256=self.payload_sha,
                         priority=4)
            event.update(consume_overrides or {})
            self.events.append(event)
        self.gate_overrides = gate_overrides or {}
        self.proof_overrides = proof_overrides or {}
        self.receipt_overrides = receipt_overrides or {}
        self.calls = []

    def next_event(self, timeout_seconds):
        assert timeout_seconds > 0
        return self.events.popleft() if self.events else None

    def authorize_queue_priority(self, event, priority):
        gates = {gate: True for gate in transport.PRIORITY_GATES}
        gates.update(self.gate_overrides)
        proof = dict(kind='queue_priority',
                     message_sha256=transport.identity(transport._semantic_event(event)),
                     witness_sha256=hashlib.sha256(b'original-setter-disassembly').hexdigest(),
                     queue=event['queue'], owner=event['owner'],
                     message_id=event['message_id'], requested_priority=priority,
                     max_priority=7, success_status=0,
                     priority_mask_hex='00000700', **gates)
        proof.update(self.proof_overrides)
        return proof

    def set_queue_priority(self, event, priority):
        self.calls.append(('native-setter', event['message_id'], priority))
        receipt = dict(queue=event['queue'], owner=event['owner'],
                       message_id=event['message_id'], length=event['length'],
                       before_priority=event['priority'], after_priority=priority,
                       native_status=0,
                       payload_sha256_before=event['payload_sha256'],
                       payload_sha256_after=event['payload_sha256'],
                       header_before_hex='00000100',
                       header_after_hex='00000400')
        receipt.update(self.receipt_overrides)
        receipt.setdefault('witness_sha256', transport.identity(receipt))
        return receipt


def priority_recipe_file(tmp_path, bus):
    original = tmp_path / 'original.elf'
    original.write_bytes(b'original-firmware-and-queue-setter')
    setter_witness = tmp_path / 'setter-witness.txt'
    setter_witness.write_bytes(b'original-setter-disassembly')
    recipe = dict(schema_version=1, contract=transport.CONTRACT,
                  address_space_id='guest-process', writable_regions=[],
                  artifacts=[dict(path='original.elf',
                                  sha256=hashlib.sha256(original.read_bytes()).hexdigest()),
                             dict(path='setter-witness.txt',
                                  sha256=hashlib.sha256(setter_witness.read_bytes()).hexdigest())],
                  limits=dict(max_events=8, max_actions=1, wall_seconds=5,
                              max_payload_bytes=128),
                  steps=[dict(trigger=dict(bus.pending),
                              action=dict(kind='queue_priority', priority=4),
                              expect=dict(kind='queue_consumed', queue='app-events',
                                          message_id='producer-seq-7',
                                          consumer='original-app-loop',
                                          length=len(bus.payload),
                                          payload_sha256=bus.payload_sha,
                                          priority=4))])
    path = tmp_path / 'priority.json'
    path.write_text(json.dumps(recipe))
    return path, recipe


def test_priority_action_requires_original_consumer_receipt_and_freezes(tmp_path):
    bus = PriorityBus()
    recipe_path, _ = priority_recipe_file(tmp_path, bus)
    first = transport.run(recipe_path, bus, tmp_path / 'first')
    assert first['status'] == 'QUEUE_SCHEDULING_VERIFIED'
    assert first['actions_attempted'] == 1
    assert bus.calls == [('native-setter', 'producer-seq-7', 4)]
    records = [json.loads(line) for line in (tmp_path / 'first/events.jsonl').read_text().splitlines()]
    assert [r['kind'] for r in records] == [
        'observation', 'intervention_intent', 'queue_priority_capability',
        'intervention_receipt', 'observation']
    assert records[3]['value']['receipt']['payload_sha256'] == bus.payload_sha
    frozen = tmp_path / 'priority-frozen.json'
    transport.freeze_recipe(recipe_path, tmp_path / 'first', frozen)
    second = transport.run(frozen, PriorityBus(), tmp_path / 'second')
    assert second['status'] == 'QUEUE_SCHEDULING_VERIFIED'
    assert transport.verify_attempt(frozen, tmp_path / 'second')['trace_sha256'] == first['trace_sha256']


@pytest.mark.parametrize('gate', transport.PRIORITY_GATES)
def test_priority_capability_denied_before_setter(tmp_path, gate):
    bus = PriorityBus(gate_overrides={gate: False})
    recipe_path, _ = priority_recipe_file(tmp_path, bus)
    result = transport.run(recipe_path, bus, tmp_path / gate)
    assert result['status'] == 'ADAPTER_FAILED'
    assert 'denied before native setter' in result['error']
    assert bus.calls == []


@pytest.mark.parametrize('proof_overrides', [
    {'queue': 'wrong-queue'}, {'owner': 'wrong-owner'},
    {'message_sha256': '0' * 64}, {'max_priority': 3},
    {'witness_sha256': '0' * 64}, {'priority_mask_hex': '00000000'},
])
def test_priority_rejects_unbound_or_unsupported_proof(tmp_path, proof_overrides):
    bus = PriorityBus(proof_overrides=proof_overrides)
    recipe_path, _ = priority_recipe_file(tmp_path, bus)
    result = transport.run(recipe_path, bus, tmp_path / 'bad-proof')
    assert result['status'] == 'ADAPTER_FAILED'
    assert bus.calls == []


@pytest.mark.parametrize('receipt_overrides', [
    {'native_status': -38}, {'after_priority': 1},
    {'payload_sha256_after': '0' * 64}, {'queue': 'wrong-queue'},
    {'header_after_hex': '00000100'}, {'header_after_hex': '00000401'},
    {'witness_sha256': '0' * 64},
])
def test_priority_rejects_false_native_receipt(tmp_path, receipt_overrides):
    bus = PriorityBus(receipt_overrides=receipt_overrides)
    recipe_path, _ = priority_recipe_file(tmp_path, bus)
    result = transport.run(recipe_path, bus, tmp_path / 'bad-receipt')
    assert result['status'] == 'ADAPTER_FAILED'
    assert bus.calls == [('native-setter', 'producer-seq-7', 4)]
    assert not any(json.loads(line)['kind'] == 'intervention_receipt' for line in
                   (tmp_path / 'bad-receipt/events.jsonl').read_text().splitlines())


@pytest.mark.parametrize('consume_overrides', [
    None, {'queue': 'wrong-queue'}, {'message_id': 'another-message'},
    {'consumer': 'competing-consumer'}, {'payload_sha256': '0' * 64},
    {'priority': 1},
])
def test_priority_needs_matching_independent_consumer(tmp_path, consume_overrides):
    bus = PriorityBus(consumed=consume_overrides is not None,
                      consume_overrides=consume_overrides)
    recipe_path, _ = priority_recipe_file(tmp_path, bus)
    result = transport.run(recipe_path, bus, tmp_path / 'no-consumer')
    assert result['status'] == 'BUDGET_EXHAUSTED'
    assert result['actions_attempted'] == 1
    assert result['steps_completed'] == 0


@pytest.mark.parametrize('mutation', [
    lambda step: step['trigger'].pop('message_id'),
    lambda step: step['trigger'].pop('payload_sha256'),
    lambda step: step['action'].update(priority=1),
    lambda step: step['expect'].pop('consumer'),
    lambda step: step['expect'].update(payload_sha256='0' * 64),
])
def test_priority_recipe_requires_exact_bounded_message_and_consumer(tmp_path, mutation):
    bus = PriorityBus()
    _, recipe = priority_recipe_file(tmp_path, bus)
    mutation(recipe['steps'][0])
    with pytest.raises(ValueError):
        transport.validate_recipe(recipe)


def test_priority_verifier_rejects_rechained_false_native_receipt(tmp_path):
    bus = PriorityBus()
    recipe_path, _ = priority_recipe_file(tmp_path, bus)
    assert transport.run(recipe_path, bus, tmp_path / 'attempt')['status'] == 'QUEUE_SCHEDULING_VERIFIED'
    journal_path = tmp_path / 'attempt/events.jsonl'
    records = [json.loads(line) for line in journal_path.read_text().splitlines()]
    receipt = next(record for record in records if record['kind'] == 'intervention_receipt')
    receipt['value']['receipt']['header_after_hex'] = '00000401'
    previous = transport.ZERO
    trace = []
    for record in records:
        record['previous_sha256'] = previous
        record.pop('record_sha256')
        record['record_sha256'] = transport.identity(record)
        previous = record['record_sha256']
        trace.append(dict(kind=record['kind'], value=record['value']))
    journal_path.write_text(''.join(json.dumps(record) + '\n' for record in records))
    report_path = tmp_path / 'attempt/result.json'
    report = json.loads(report_path.read_text())
    report['journal_tail_sha256'] = previous
    report['trace_sha256'] = transport.identity(trace)
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='outside priority mask'):
        transport.verify_attempt(recipe_path, tmp_path / 'attempt', require_frozen=False)


def test_priority_verifier_rejects_rechained_unbound_witness(tmp_path):
    bus = PriorityBus()
    recipe_path, _ = priority_recipe_file(tmp_path, bus)
    assert transport.run(recipe_path, bus, tmp_path / 'attempt')['status'] == 'QUEUE_SCHEDULING_VERIFIED'
    journal_path = tmp_path / 'attempt/events.jsonl'
    records = [json.loads(line) for line in journal_path.read_text().splitlines()]
    receipt = next(record for record in records if record['kind'] == 'intervention_receipt')
    receipt['value']['receipt']['witness_sha256'] = '0' * 64
    previous = transport.ZERO
    trace = []
    for record in records:
        record['previous_sha256'] = previous
        record.pop('record_sha256')
        record['record_sha256'] = transport.identity(record)
        previous = record['record_sha256']
        trace.append(dict(kind=record['kind'], value=record['value']))
    journal_path.write_text(''.join(json.dumps(record) + '\n' for record in records))
    report_path = tmp_path / 'attempt/result.json'
    report = json.loads(report_path.read_text())
    report['journal_tail_sha256'] = previous
    report['trace_sha256'] = transport.identity(trace)
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='witness is not bound'):
        transport.verify_attempt(recipe_path, tmp_path / 'attempt', require_frozen=False)


def test_copy_commit_must_match_bytes_actually_written(tmp_path):
    recipe_path, recipe = recipe_file(tmp_path)
    del recipe['steps'][1]['trigger']['payload_sha256']
    recipe_path.write_text(json.dumps(recipe))
    expected_hash = recipe['steps'][1]['expect']['payload_sha256']

    class FalseCommitBus(Bus):
        def write_memory(self, address, payload):
            super().write_memory(address, payload)
            if address == 0x2000:
                self.events[0]['payload_sha256'] = expected_hash

    result = transport.run(recipe_path, FalseCommitBus(payload=b'X' * 22), tmp_path / 'false')
    assert result['status'] == 'ADAPTER_FAILED'
    assert 'commit digest differs from copied bytes' in result['error']
    assert result['actions_attempted'] == 2


def test_frozen_replay_detects_observed_drift(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    transport.run(recipe_path, Bus(), tmp_path / 'first')
    frozen = tmp_path / 'frozen.json'
    transport.freeze_recipe(recipe_path, tmp_path / 'first', frozen)
    adapter = Bus()
    adapter.events.appendleft(dict(kind='marker', name='unrelated-real-event'))
    result = transport.run(frozen, adapter, tmp_path / 'drift')
    assert result['status'] == 'REPLAY_DIVERGED'
    with pytest.raises(ValueError, match='Frozen transport trace'):
        transport.verify_attempt(frozen, tmp_path / 'drift')


def test_verifier_rejects_journal_tampering(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    transport.run(recipe_path, Bus(), tmp_path / 'first')
    frozen = tmp_path / 'frozen.json'
    transport.freeze_recipe(recipe_path, tmp_path / 'first', frozen)
    log = tmp_path / 'first/events.jsonl'
    log.write_text(log.read_text().replace('peer.pin', 'fake.pin', 1))
    with pytest.raises(ValueError, match='journal chain'):
        transport.verify_attempt(frozen, tmp_path / 'first')


def test_verifier_rejects_contradictory_success_result(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    transport.run(recipe_path, Bus(), tmp_path / 'first')
    report_path = tmp_path / 'first/result.json'
    report = json.loads(report_path.read_text())
    report['status'] = 'OBSERVED_OR_APPLIED_UNVERIFIED'
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='status contradicts'):
        transport.verify_attempt(recipe_path, tmp_path / 'first', require_frozen=False)


def test_verifier_rejects_inconsistent_artifact_report(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    transport.run(recipe_path, Bus(), tmp_path / 'first')
    report_path = tmp_path / 'first/result.json'
    report = json.loads(report_path.read_text())
    report['artifacts_before'][0]['actual'] = '0' * 64
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='artifact identity report'):
        transport.verify_attempt(recipe_path, tmp_path / 'first', require_frozen=False)


def test_verifier_rejects_success_with_chained_failure_record(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    transport.run(recipe_path, Bus(), tmp_path / 'first')
    journal_path = tmp_path / 'first/events.jsonl'
    lines = [json.loads(line) for line in journal_path.read_text().splitlines()]
    record = dict(index=len(lines), previous_sha256=lines[-1]['record_sha256'],
                  kind='failure', value=dict(error='retained failure'))
    record['record_sha256'] = transport.identity(record)
    lines.append(record)
    journal_path.write_text(''.join(json.dumps(line) + '\n' for line in lines))
    report_path = tmp_path / 'first/result.json'
    report = json.loads(report_path.read_text())
    report['journal_tail_sha256'] = record['record_sha256']
    report['journal_records'] = len(lines)
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='failure journal record'):
        transport.verify_attempt(recipe_path, tmp_path / 'first', require_frozen=False)


def test_budget_and_identity_failures_are_retained(tmp_path):
    recipe_path, recipe = recipe_file(tmp_path)
    quiet = Bus()
    quiet.events.clear()
    budget = transport.run(recipe_path, quiet, tmp_path / 'quiet')
    assert budget['status'] == 'BUDGET_EXHAUSTED'
    assert budget['steps_completed'] == 0
    (tmp_path / 'original.elf').write_bytes(b'changed')
    identity = transport.run(recipe_path, Bus(), tmp_path / 'changed')
    assert identity['status'] == 'IDENTITY_CHANGED'
    assert identity['actions_attempted'] == 0
    assert not identity['artifacts_before'][0]['verified']


def test_failed_attempt_can_be_frozen_and_replayed_as_failure(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    quiet = Bus()
    quiet.events.clear()
    first = transport.run(recipe_path, quiet, tmp_path / 'first')
    assert first['status'] == 'BUDGET_EXHAUSTED'
    frozen = tmp_path / 'frozen.json'
    transport.freeze_recipe(recipe_path, tmp_path / 'first', frozen)
    quiet_again = Bus()
    quiet_again.events.clear()
    second = transport.run(frozen, quiet_again, tmp_path / 'second')
    assert second['status'] == 'BUDGET_EXHAUSTED'
    assert transport.verify_attempt(frozen, tmp_path / 'second')['trace_sha256'] == first['trace_sha256']


def test_freeze_and_verify_cli_runs_without_solver(tmp_path):
    recipe_path, _ = recipe_file(tmp_path)
    transport.run(recipe_path, Bus(), tmp_path / 'attempt')
    frozen = tmp_path / 'frozen.json'
    freeze = subprocess.run([sys.executable, '-m', 'devkit.adaptive.transport', 'freeze',
                             str(recipe_path), str(tmp_path / 'attempt'), '--output', str(frozen)],
                            capture_output=True, text=True, check=False)
    assert freeze.returncode == 0, freeze.stderr
    verify = subprocess.run([sys.executable, '-m', 'devkit.adaptive.transport', 'verify',
                             str(frozen), str(tmp_path / 'attempt')],
                            capture_output=True, text=True, check=False)
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)['status'] == 'TRANSPORT_VERIFIED'


@pytest.mark.parametrize('mutation', [
    lambda r: r['steps'][3].update(trigger=dict(kind='marker', name='unproven')),
    lambda r: r['steps'][1].update(trigger=dict(kind='dma_descriptor', source='0x1000')),
    lambda r: r['steps'][0].update(action=dict(kind='gpio_mirror', target='producer.pin')),
    lambda r: r['limits'].update(max_payload_bytes=10),
    lambda r: r.update(writable_regions=[]),
    lambda r: r['steps'][1].update(expect=dict(kind='marker', name='unrelated')),
    lambda r: r['steps'][2].update(expect=dict(kind='queue_commit', queue='other',
                                              length=22, payload_sha256='0' * 64)),
    lambda r: r['steps'][3].update(expect=dict(kind='marker', name='not-an-irq')),
    lambda r: r['steps'][4].update(expect=dict(kind='frame', pixels_sha256='0' * 64)),
])
def test_recipe_rejects_unwitnessed_or_unbounded_interventions(tmp_path, mutation):
    _, recipe = recipe_file(tmp_path)
    mutation(recipe)
    with pytest.raises(ValueError):
        transport.validate_recipe(recipe)
