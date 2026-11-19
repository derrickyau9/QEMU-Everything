"""Bounded, causal transport interventions for an external QEMU adapter.

This module contains no board addresses or peer responses.  An adapter must
observe real target events and implement concrete GPIO, IRQ and memory access.
The finite recipe is data; no solver, LLM, agent or Python callback is loaded
from it.  A transport verdict is deliberately separate from a UI verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from .contracts import digest, identity, save_json


CONTRACT = 'molten-transport-v1'
ZERO = '0' * 64
HASH = re.compile(r'^[0-9a-f]{64}$')
HEX = re.compile(r'^0x(?:0|[1-9a-f][0-9a-f]*)$')
LIMITS = dict(max_events=100000, max_actions=256, wall_seconds=3600,
              max_payload_bytes=4 * 1024 * 1024)
EVENT_FIELDS = {
    # These observations describe CPU transactions and counters only.  A
    # register write is not a GPIO edge; a RAM write is not a peer commit.
    'cpu_access': {'region', 'address', 'operation', 'width_bits', 'value', 'pc', 'vcpu'},
    'irq_count_snapshot': {'irq', 'count'},
    'gpio_edge': {'endpoint', 'level'},
    'irq_assert': {'irq'}, 'irq_delivered': {'irq'}, 'irq_ack': {'irq'},
    'dma_descriptor': {'source', 'target', 'length'},
    'dma_commit': {'target', 'length', 'payload_sha256'},
    'queue_descriptor': {'queue', 'source', 'target', 'length'},
    'queue_commit': {'queue', 'length', 'payload_sha256'},
    'queue_consumed': {'queue', 'length', 'payload_sha256'},
    # A producer-owned message before its original queue send.  message_id is
    # an opaque adapter identity, never a board address interpreted by core.
    'queue_message_pending': {'queue', 'message_id', 'owner', 'length',
                              'payload_sha256', 'priority'},
    'input_request': {'code'}, 'input_delivered': {'code'},
    'frame': {'pixels_sha256'}, 'marker': {'name'},
}
EVENT_OPTIONAL = {'virtual_time', 'witness_sha256'}
ACTION_FIELDS = {
    'gpio_mirror': {'target'}, 'irq_route': {'irq'},
    'dma_copy': set(), 'queue_copy': set(),
    'queue_priority': {'priority'},
    'deliver_input': {'target', 'code'},
}
COPY_GATES = {
    'dma_copy': ('link_connected', 'doorbell_observed', 'translation_verified'),
    'queue_copy': ('queue_owned', 'peer_ready', 'translation_verified'),
}
PRIORITY_GATES = ('producer_owns_message', 'pre_send_live',
                  'queue_binding_verified', 'native_setter_verified',
                  'priority_field_mask_verified', 'payload_immutable')
STATUSES = {'TRANSPORT_VERIFIED', 'QUEUE_SCHEDULING_VERIFIED',
            'OBSERVED_OR_APPLIED_UNVERIFIED',
            'BUDGET_EXHAUSTED', 'IDENTITY_MISMATCH', 'IDENTITY_CHANGED',
            'ADAPTER_FAILED', 'INVALID_OBSERVATION', 'REPLAY_DIVERGED'}


def _sha(value, label):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError(f'{label} must be a lowercase SHA-256 digest')
    return value


def _name(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or '\n' in value:
        raise ValueError(f'{label} must be a short name')
    return value


def _address(value, label):
    if not isinstance(value, str) or not HEX.fullmatch(value) or int(value, 16) > 0xffffffffffffffff:
        raise ValueError(f'{label} must be a canonical uint64 hex address')
    return int(value, 16)


def _uint(value, label, upper):
    if type(value) is not int or not 0 <= value <= upper:
        raise ValueError(f'{label} must be an integer in 0..{upper}')
    return value


def validate_event(event, max_payload_bytes):
    if not isinstance(event, dict) or event.get('kind') not in EVENT_FIELDS:
        raise ValueError('Unsupported transport observation')
    kind = event['kind']
    required = EVENT_FIELDS[kind]
    extra = ({'payload_sha256'} if kind in ('dma_descriptor', 'queue_descriptor') else
             {'message_id', 'consumer', 'priority'} if kind == 'queue_consumed' else set())
    if not required <= event.keys() or set(event) - required - EVENT_OPTIONAL - {'kind'} - extra:
        raise ValueError(f'Invalid fields for {kind} observation')
    for key in ('endpoint', 'queue', 'name', 'region', 'message_id', 'owner', 'consumer'):
        if key in event:
            _name(event[key], key)
    for key in ('source', 'target', 'address', 'pc', 'value'):
        if key in event:
            _address(event[key], key)
    if 'operation' in event and event['operation'] not in ('read', 'write'):
        raise ValueError('CPU access operation must be read or write')
    if ('width_bits' in event and
            (type(event['width_bits']) is not int or event['width_bits'] not in (8, 16, 32, 64, 128))):
        raise ValueError('CPU access width must be 8, 16, 32, 64 or 128 bits')
    if 'vcpu' in event:
        _uint(event['vcpu'], 'vcpu', 255)
    if 'count' in event:
        _uint(event['count'], 'IRQ count', (1 << 63) - 1)
    if 'level' in event:
        _uint(event['level'], 'level', 1)
    if 'irq' in event:
        _uint(event['irq'], 'irq', 1023)
    if 'code' in event:
        _uint(event['code'], 'code', 0xffffffff)
    if 'priority' in event:
        _uint(event['priority'], 'priority', 255)
    if 'length' in event:
        _uint(event['length'], 'length', max_payload_bytes)
        if not event['length']:
            raise ValueError('Empty transport payload is not a causal commit')
        for key in ('source', 'target'):
            if key in event and _address(event[key], key) + event['length'] > 1 << 64:
                raise ValueError('Transport address wraps uint64')
    for key in ('payload_sha256', 'pixels_sha256', 'witness_sha256'):
        if key in event:
            _sha(event[key], key)
    if 'virtual_time' in event:
        _uint(event['virtual_time'], 'virtual_time', (1 << 63) - 1)
    if len(json.dumps(event, separators=(',', ':'))) > 4096:
        raise ValueError('Transport observation exceeds 4 KiB')
    return event


def _selector(value, label, max_payload_bytes):
    if not isinstance(value, dict) or value.get('kind') not in EVENT_FIELDS:
        raise ValueError(f'{label} must select a typed event')
    # A selector can compare any subset of event fields, but must contain kind.
    extra = ({'payload_sha256'} if value['kind'] in ('dma_descriptor', 'queue_descriptor') else
             {'message_id', 'consumer', 'priority'} if value['kind'] == 'queue_consumed' else set())
    allowed = EVENT_FIELDS[value['kind']] | EVENT_OPTIONAL | extra
    if set(value) - allowed - {'kind'}:
        raise ValueError(f'{label} contains unsupported event fields')
    candidate = dict(value)
    for field in EVENT_FIELDS[value['kind']] - candidate.keys():
        candidate[field] = {'endpoint': '_', 'queue': '_', 'message_id': '_',
                            'owner': '_', 'consumer': '_', 'name': '_',
                            'region': '_', 'address': '0x0', 'operation': 'read',
                            'width_bits': 32, 'value': '0x0', 'pc': '0x0',
                            'vcpu': 0, 'count': 0,
                            'source': '0x0', 'target': '0x0', 'level': 0,
                            'irq': 0, 'code': 0, 'priority': 0, 'length': 1,
                            'payload_sha256': ZERO, 'pixels_sha256': ZERO}[field]
    validate_event(candidate, max_payload_bytes)
    return value


def _match(selector, event):
    return all(event.get(key) == value for key, value in selector.items())


def _required_postcondition(trigger, action):
    kind = action['kind']
    if kind == 'gpio_mirror':
        return dict(kind='gpio_edge', endpoint=action['target'], level=trigger['level'])
    if kind == 'dma_copy':
        return dict(kind='dma_commit', target=trigger['target'], length=trigger['length'])
    if kind == 'queue_copy':
        return dict(kind='queue_commit', queue=trigger['queue'], length=trigger['length'])
    if kind == 'queue_priority':
        return dict(kind='queue_consumed', queue=trigger['queue'],
                    length=trigger['length'], payload_sha256=trigger['payload_sha256'],
                    message_id=trigger['message_id'], priority=action['priority'])
    if kind == 'irq_route':
        return dict(kind='irq_delivered', irq=action['irq'])
    if kind == 'deliver_input':
        return dict(kind='input_delivered', code=action['code'])
    raise ValueError('Unsupported intervention')


def _check_postcondition(trigger, action, expect):
    required = _required_postcondition(trigger, action)
    if expect is None or not _match(required, expect):
        raise ValueError(f"{action['kind']} requires a matching concrete postcondition")
    if action['kind'] in ('dma_copy', 'queue_copy'):
        expected_hash = _sha(expect.get('payload_sha256'), 'copy postcondition payload')
        if 'payload_sha256' in trigger and trigger['payload_sha256'] != expected_hash:
            raise ValueError('Copy postcondition digest differs from descriptor digest')
    if action['kind'] == 'queue_priority':
        _name(expect.get('consumer'), 'queue consumer')


def _check_postcondition_receipt(action, receipt, observed):
    if action['kind'] in ('dma_copy', 'queue_copy') and (
            observed['payload_sha256'] != receipt['payload_sha256']):
        raise RuntimeError('Observed copy commit digest differs from copied bytes')
    if action['kind'] == 'queue_priority' and (
            observed['queue'] != receipt['queue'] or
            observed['message_id'] != receipt['message_id'] or
            observed['length'] != receipt['length'] or
            observed['priority'] != receipt['after_priority'] or
            observed['payload_sha256'] != receipt['payload_sha256']):
        raise RuntimeError('Observed queue consumption differs from scheduled message')


def validate_recipe(recipe):
    if not isinstance(recipe, dict) or set(recipe) - {
            'schema_version', 'contract', 'address_space_id', 'writable_regions',
            'artifacts', 'limits', 'steps',
            'expected_trace_sha256', 'expected_status'} or recipe.get('schema_version') != 1 or recipe.get('contract') != CONTRACT:
        raise ValueError('Unsupported transport recipe contract')
    _name(recipe.get('address_space_id'), 'address space')
    regions = recipe.get('writable_regions')
    if not isinstance(regions, list) or len(regions) > 32:
        raise ValueError('Recipe requires explicit writable memory regions')
    intervals = []
    for region in regions:
        if not isinstance(region, dict) or set(region) != {'start', 'end', 'owner'}:
            raise ValueError('Writable region requires start, end and owner')
        start, end = _address(region['start'], 'region start'), _address(region['end'], 'region end')
        _name(region['owner'], 'region owner')
        if end <= start or any(start < other_end and other_start < end for other_start, other_end in intervals):
            raise ValueError('Writable regions must be nonempty and disjoint')
        intervals.append((start, end))
    if not isinstance(recipe.get('artifacts'), list) or not recipe['artifacts']:
        raise ValueError('Recipe requires immutable source artifacts')
    seen_paths = set()
    for artifact in recipe['artifacts']:
        if not isinstance(artifact, dict) or set(artifact) != {'path', 'sha256'}:
            raise ValueError('Artifact requires path and SHA-256')
        path = artifact['path']
        if not isinstance(path, str) or not 1 <= len(path) <= 4096 or '\n' in path or path in seen_paths:
            raise ValueError('Invalid or duplicate artifact path')
        seen_paths.add(path)
        _sha(artifact['sha256'], 'artifact sha256')
    limits = recipe.get('limits')
    if not isinstance(limits, dict) or set(limits) != set(LIMITS):
        raise ValueError('Recipe must state all transport limits')
    for key, upper in LIMITS.items():
        if type(limits[key]) is not int or not 1 <= limits[key] <= upper:
            raise ValueError(f'Invalid {key} limit')
    steps = recipe.get('steps')
    if not isinstance(steps, list) or not steps or len(steps) > limits['max_events']:
        raise ValueError('Recipe requires bounded nonempty steps')
    actions = 0
    for step in steps:
        if not isinstance(step, dict) or set(step) - {'trigger', 'action', 'expect'} or 'trigger' not in step:
            raise ValueError('Invalid transport step')
        trigger = _selector(step['trigger'], 'trigger', limits['max_payload_bytes'])
        if 'expect' in step:
            _selector(step['expect'], 'expect', limits['max_payload_bytes'])
        action = step.get('action')
        if action is None:
            if 'action' in step:
                raise ValueError('Action must be a supported intervention AST')
            continue
        actions += 1
        if not isinstance(action, dict) or action.get('kind') not in ACTION_FIELDS or set(action) != ACTION_FIELDS[action['kind']] | {'kind'}:
            raise ValueError('Unsupported intervention AST')
        kind = action['kind']
        if kind == 'gpio_mirror':
            if trigger['kind'] != 'gpio_edge' or 'endpoint' not in trigger or 'level' not in trigger:
                raise ValueError('GPIO mirror requires a concrete observed source edge')
            if _name(action['target'], 'GPIO target') == trigger['endpoint']:
                raise ValueError('GPIO source and target must differ')
        elif kind == 'irq_route':
            if trigger['kind'] not in ('dma_commit', 'queue_commit') or 'payload_sha256' not in trigger:
                raise ValueError('IRQ route requires a witnessed nonempty payload commit')
            _uint(action['irq'], 'irq', 1023)
        elif kind in ('dma_copy', 'queue_copy'):
            expected = 'dma_descriptor' if kind == 'dma_copy' else 'queue_descriptor'
            if trigger['kind'] != expected or not {'source', 'target', 'length'} <= trigger.keys():
                raise ValueError('Copy requires a concrete observed descriptor')
            if trigger['source'] == trigger['target']:
                raise ValueError('Source and destination must differ')
            target = int(trigger['target'], 16)
            if not any(start <= target and target + trigger['length'] <= end for start, end in intervals):
                raise ValueError('Copy destination is outside declared writable regions')
        elif kind == 'queue_priority':
            if trigger['kind'] != 'queue_message_pending' or not EVENT_FIELDS[
                    'queue_message_pending'] <= trigger.keys():
                raise ValueError('Queue priority requires a concrete live producer message')
            _uint(action['priority'], 'requested priority', 255)
            if action['priority'] <= trigger['priority']:
                raise ValueError('Queue priority action must raise the observed priority')
        elif kind == 'deliver_input':
            if trigger['kind'] != 'input_request' or 'code' not in trigger or action['code'] != trigger['code']:
                raise ValueError('Input delivery must follow the observed request code')
            _name(action['target'], 'input target')
            _uint(action['code'], 'input code', 0xffffffff)
        _check_postcondition(trigger, action, step.get('expect'))
    if actions > limits['max_actions']:
        raise ValueError('Recipe exceeds action budget')
    if 'expected_trace_sha256' in recipe:
        _sha(recipe['expected_trace_sha256'], 'expected trace')
    if ('expected_status' in recipe) != ('expected_trace_sha256' in recipe):
        raise ValueError('Frozen status and trace must be specified together')
    if ('expected_status' in recipe and
            (not isinstance(recipe['expected_status'], str) or
             recipe['expected_status'] not in STATUSES - {'REPLAY_DIVERGED'})):
        raise ValueError('Unsupported frozen transport status')
    return recipe


def _artifact_hashes(recipe, base):
    hashes = []
    for item in recipe['artifacts']:
        path = Path(item['path'])
        if not path.is_absolute():
            path = base / path
        try:
            actual = digest(path)
        except OSError:
            actual = None
        hashes.append(dict(path=str(path.resolve()), expected=item['sha256'], actual=actual,
                           verified=actual == item['sha256']))
    return hashes


def _base_recipe(recipe):
    return {key: value for key, value in recipe.items()
            if key not in ('expected_trace_sha256', 'expected_status')}


def _semantic_event(event):
    return {key: value for key, value in event.items() if key not in EVENT_OPTIONAL}


class _Journal:
    def __init__(self, path):
        self.path = path
        self.stream = path.open('w', encoding='utf-8')
        self.previous = ZERO
        self.count = 0
        self.trace = []

    def append(self, kind, value, semantic=None):
        record = dict(index=self.count, previous_sha256=self.previous, kind=kind, value=value)
        record['record_sha256'] = identity(record)
        self.stream.write(json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n')
        self.stream.flush()
        self.previous = record['record_sha256']
        self.count += 1
        if semantic is not None:
            self.trace.append(dict(kind=kind, value=semantic))

    def close(self):
        self.stream.close()


def _validate_copy_capability(kind, event, proof):
    required = {'kind', 'descriptor_sha256', 'witness_sha256'} | set(COPY_GATES[kind])
    if type(proof) is not dict or set(proof) != required or proof['kind'] != kind:
        raise ValueError('Copy capability has invalid fields')
    if proof['descriptor_sha256'] != identity(_semantic_event(event)):
        raise ValueError('Copy capability is not bound to the observed descriptor')
    _sha(proof['witness_sha256'], 'copy capability witness')
    for gate in COPY_GATES[kind]:
        if type(proof[gate]) is not bool:
            raise ValueError(f'Copy capability {gate} must be a boolean')
    return all(proof[gate] for gate in COPY_GATES[kind])


def _int32(value, label):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        raise ValueError(f'{label} must be a signed 32-bit integer')
    return value


def _header_bytes(value, label):
    if (not isinstance(value, str) or not 2 <= len(value) <= 512 or
            len(value) % 2 or not re.fullmatch(r'[0-9a-f]+', value)):
        raise ValueError(f'{label} must be 1..256 bytes of lowercase hex')
    return bytes.fromhex(value)


def _validate_priority_capability(event, action, proof, artifact_hashes):
    required = {'kind', 'message_sha256', 'witness_sha256', 'queue', 'owner',
                'message_id', 'requested_priority', 'max_priority',
                'success_status', 'priority_mask_hex'} | set(PRIORITY_GATES)
    if type(proof) is not dict or set(proof) != required or proof['kind'] != 'queue_priority':
        raise ValueError('Queue priority capability has invalid fields')
    if proof['message_sha256'] != identity(_semantic_event(event)):
        raise ValueError('Queue priority capability is not bound to the observed message')
    for field in ('queue', 'owner', 'message_id'):
        if proof[field] != event[field]:
            raise ValueError(f'Queue priority capability {field} differs from message')
    _sha(proof['witness_sha256'], 'queue priority capability witness')
    if proof['witness_sha256'] not in artifact_hashes:
        raise ValueError('Queue priority capability witness is not a pinned artifact')
    _uint(proof['requested_priority'], 'proof requested priority', 255)
    _uint(proof['max_priority'], 'proof maximum priority', 255)
    _int32(proof['success_status'], 'setter success status')
    if not any(_header_bytes(proof['priority_mask_hex'], 'native priority mask')):
        raise ValueError('Native priority mask must change at least one bit')
    if proof['requested_priority'] != action['priority'] or action['priority'] > proof['max_priority']:
        raise ValueError('Queue priority exceeds the bound native setter range')
    for gate in PRIORITY_GATES:
        if type(proof[gate]) is not bool:
            raise ValueError(f'Queue priority capability {gate} must be a boolean')
    return all(proof[gate] for gate in PRIORITY_GATES)


def _validate_priority_receipt(event, action, proof, raw):
    required = {'queue', 'owner', 'message_id', 'length', 'before_priority',
                'after_priority', 'native_status', 'payload_sha256_before',
                'payload_sha256_after', 'header_before_hex',
                'header_after_hex', 'witness_sha256'}
    if type(raw) is not dict or set(raw) != required:
        raise RuntimeError('Native queue priority receipt has invalid fields')
    for field in ('queue', 'owner', 'message_id', 'length'):
        if raw[field] != event[field]:
            raise RuntimeError(f'Native queue priority receipt {field} differs from message')
    try:
        _uint(raw['before_priority'], 'previous priority', 255)
        _uint(raw['after_priority'], 'applied priority', 255)
        _int32(raw['native_status'], 'native setter status')
        for field in ('payload_sha256_before', 'payload_sha256_after', 'witness_sha256'):
            _sha(raw[field], field)
        before = _header_bytes(raw['header_before_hex'], 'header before')
        after = _header_bytes(raw['header_after_hex'], 'header after')
        mask = _header_bytes(proof['priority_mask_hex'], 'native priority mask')
    except ValueError as failure:
        raise RuntimeError(f'Invalid native queue priority receipt: {failure}') from failure
    if len(before) != len(after) or len(before) != len(mask):
        raise RuntimeError('Native queue priority header and mask lengths differ')
    changed = bytes(old ^ new for old, new in zip(before, after))
    if not any(changed) or any(change & ~allowed for change, allowed in zip(changed, mask)):
        raise RuntimeError('Native queue priority changed bits outside priority mask')
    if (raw['before_priority'] != event['priority'] or
            raw['after_priority'] != action['priority'] or
            raw['native_status'] != proof['success_status'] or
            raw['payload_sha256_before'] != event['payload_sha256'] or
            raw['payload_sha256_after'] != event['payload_sha256']):
        raise RuntimeError('Native queue priority setter or payload readback differs')
    # The receipt digest must identify the exact native-status/header/payload
    # fields retained in the journal; a syntactically valid unattached hash is
    # not evidence of this setter result. Raw target logs remain adapter-owned.
    if raw['witness_sha256'] != identity({key: value for key, value in raw.items()
                                           if key != 'witness_sha256'}):
        raise RuntimeError('Native queue priority witness is not bound to the receipt')
    return dict(kind='queue_priority', queue=event['queue'], owner=event['owner'],
                message_id=event['message_id'], length=event['length'],
                before_priority=raw['before_priority'], after_priority=raw['after_priority'],
                native_status=raw['native_status'], payload_sha256=event['payload_sha256'],
                payload_sha256_before=raw['payload_sha256_before'],
                payload_sha256_after=raw['payload_sha256_after'],
                header_before_hex=raw['header_before_hex'],
                header_after_hex=raw['header_after_hex'],
                priority_mask_hex=proof['priority_mask_hex'],
                witness_sha256=raw['witness_sha256'],
                capability_sha256=identity(proof))


def _execute(action, event, adapter, max_payload, journal, step_index, artifact_hashes):
    kind = action['kind']
    if kind == 'gpio_mirror':
        adapter.drive_gpio(action['target'], event['level'])
        observed = adapter.read_gpio(action['target'])
        if observed != event['level']:
            raise RuntimeError('GPIO readback differs from mirrored source level')
        return dict(kind=kind, source=event['endpoint'], target=action['target'], level=observed)
    if kind == 'irq_route':
        adapter.assert_irq(action['irq'])
        return dict(kind=kind, irq=action['irq'], causal_payload_sha256=event['payload_sha256'])
    if kind in ('dma_copy', 'queue_copy'):
        # The adapter must inspect the live link/queue state and address map at
        # action time. A descriptor can exist before a doorbell or peer commit.
        proof = adapter.authorize_copy(kind, dict(event))
        try:
            granted = _validate_copy_capability(kind, event, proof)
        except ValueError as failure:
            raise RuntimeError(f'Invalid adapter copy capability: {failure}') from failure
        journal.append('copy_capability', dict(step=step_index, proof=proof),
                       dict(step=step_index, proof=proof))
        if not granted:
            raise RuntimeError(f'{kind} capability denied before memory access')
        length = event['length']
        if not 1 <= length <= max_payload:
            raise ValueError('Descriptor length exceeds payload budget')
        source, target = int(event['source'], 16), int(event['target'], 16)
        payload = adapter.read_memory(source, length)
        if type(payload) is not bytes or len(payload) != length:
            raise RuntimeError('Source memory read was short or nonbytes')
        payload_hash = hashlib.sha256(payload).hexdigest()
        if event.get('payload_sha256') and payload_hash != event['payload_sha256']:
            raise RuntimeError('Source bytes differ from observed descriptor digest')
        adapter.write_memory(target, payload)
        readback = adapter.read_memory(target, length)
        if readback != payload:
            raise RuntimeError('Copied bytes differ at destination readback')
        return dict(kind=kind, source=event['source'], target=event['target'],
                    length=length, payload_sha256=payload_hash,
                    destination_sha256=hashlib.sha256(readback).hexdigest(),
                    capability_sha256=identity(proof))
    if kind == 'queue_priority':
        proof = adapter.authorize_queue_priority(dict(event), action['priority'])
        try:
            granted = _validate_priority_capability(event, action, proof, artifact_hashes)
        except ValueError as failure:
            raise RuntimeError(f'Invalid adapter queue priority capability: {failure}') from failure
        journal.append('queue_priority_capability', dict(step=step_index, proof=proof),
                       dict(step=step_index, proof=proof))
        if not granted:
            raise RuntimeError('Queue priority capability denied before native setter')
        raw = adapter.set_queue_priority(dict(event), action['priority'])
        return _validate_priority_receipt(event, action, proof, raw)
    if kind == 'deliver_input':
        adapter.deliver_input(action['target'], action['code'])
        return dict(kind=kind, target=action['target'], code=action['code'])
    raise ValueError('Unsupported intervention')


def run(recipe_path, adapter, output):
    """Execute a finite recipe against a target adapter and retain failure evidence.

    Adapter methods are next_event(timeout_seconds), drive_gpio, read_gpio,
    authorize_copy, assert_irq, read_memory, write_memory, deliver_input,
    authorize_queue_priority and set_queue_priority.
    Only methods used by the recipe are required.  The adapter must honor the
    supplied timeout and retain raw copy-capability witnesses.
    """
    recipe_path = Path(recipe_path).resolve()
    recipe = validate_recipe(json.loads(recipe_path.read_text(encoding='utf-8')))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'recipe.json', recipe)
    start = time.monotonic()
    before = _artifact_hashes(recipe, recipe_path.parent)
    journal = _Journal(output / 'events.jsonl')
    status = 'BUDGET_EXHAUSTED'
    error = None
    events_seen = actions_attempted = steps_completed = 0
    expected = recipe.get('expected_trace_sha256')
    expected_status = recipe.get('expected_status')
    try:
        if not all(item['verified'] for item in before):
            status = 'IDENTITY_MISMATCH'
        else:
            for index, step in enumerate(recipe['steps']):
                receipt = None
                for phase in ('trigger', 'expect'):
                    if phase == 'expect' and 'expect' not in step:
                        break
                    selector = step[phase]
                    matched = None
                    while events_seen < recipe['limits']['max_events']:
                        remaining = recipe['limits']['wall_seconds'] - (time.monotonic() - start)
                        if remaining <= 0:
                            break
                        event = adapter.next_event(remaining)
                        if time.monotonic() - start > recipe['limits']['wall_seconds']:
                            break
                        if event is None:
                            break
                        event = validate_event(event, recipe['limits']['max_payload_bytes'])
                        events_seen += 1
                        journal.append('observation', dict(step=index, phase=phase, event=event),
                                       dict(step=index, phase=phase, event=_semantic_event(event)))
                        if _match(selector, event):
                            matched = event
                            break
                    if matched is None:
                        error = f'step {index} {phase} observation not reached within budget'
                        break
                    if phase == 'expect' and receipt is not None:
                        _check_postcondition_receipt(step['action'], receipt, matched)
                    if phase == 'trigger' and 'action' in step:
                        actions_attempted += 1
                        action = step['action']
                        journal.append('intervention_intent', dict(step=index, action=action,
                                    trigger_event_sha256=identity(_semantic_event(matched))),
                                       dict(step=index, action=action,
                                            trigger_event_sha256=identity(_semantic_event(matched))))
                        receipt = _execute(action, matched, adapter,
                                           recipe['limits']['max_payload_bytes'], journal, index,
                                           {item['sha256'] for item in recipe['artifacts']})
                        journal.append('intervention_receipt', dict(step=index, receipt=receipt),
                                       dict(step=index, receipt=receipt))
                        if time.monotonic() - start > recipe['limits']['wall_seconds']:
                            error = f'step {index} action exceeded wall budget'
                            break
                if error:
                    break
                steps_completed += 1
            else:
                if all('expect' in step for step in recipe['steps']) and any(
                        'action' in step for step in recipe['steps']):
                    status = ('QUEUE_SCHEDULING_VERIFIED' if any(
                        step.get('action', {}).get('kind') == 'queue_priority'
                        for step in recipe['steps']) else 'TRANSPORT_VERIFIED')
                else:
                    status = 'OBSERVED_OR_APPLIED_UNVERIFIED'
    except BaseException as failure:
        status = 'ADAPTER_FAILED' if not isinstance(failure, ValueError) else 'INVALID_OBSERVATION'
        error = f'{type(failure).__name__}: {failure}'
        journal.append('failure', dict(error=error))
    finally:
        after = _artifact_hashes(recipe, recipe_path.parent)
        if not all(item['verified'] for item in after):
            status = 'IDENTITY_CHANGED'
        trace_sha = identity(journal.trace)
        observed_status = status
        if expected and (trace_sha != expected or observed_status != expected_status):
            status = 'REPLAY_DIVERGED'
            error = 'Observed transport status or semantic trace differs from frozen recipe'
        journal.close()
        result = dict(schema_version=1, contract=CONTRACT, status=status,
                      observed_status=observed_status, error=error,
                      address_space_id=recipe['address_space_id'],
                      recipe_base_sha256=identity(_base_recipe(recipe)),
                      expected_trace_sha256=expected, trace_sha256=trace_sha,
                      journal_tail_sha256=journal.previous, journal_records=journal.count,
                      events_seen=events_seen, actions_attempted=actions_attempted,
                      steps_completed=steps_completed, wall_seconds=time.monotonic() - start,
                      artifacts_before=before, artifacts_after=after,
                      output=str(output.resolve()))
        save_json(output / 'result.json', result)
    return result


def freeze_recipe(recipe_path, attempt_dir, output):
    recipe_path = Path(recipe_path).resolve()
    output = Path(output).resolve()
    if output.parent != recipe_path.parent:
        raise ValueError('Frozen recipe must stay beside the source recipe and relative artifacts')
    if output.exists():
        raise FileExistsError('Frozen recipe output already exists')
    recipe = validate_recipe(json.loads(recipe_path.read_text(encoding='utf-8')))
    result = verify_attempt(recipe_path, attempt_dir, require_frozen=False)
    frozen = dict(_base_recipe(recipe), expected_trace_sha256=result['trace_sha256'],
                  expected_status=result['observed_status'])
    save_json(output, frozen)
    return frozen


def verify_attempt(recipe_path, attempt_dir, *, require_frozen=True):
    recipe = validate_recipe(json.loads(Path(recipe_path).read_text(encoding='utf-8')))
    attempt_dir = Path(attempt_dir)
    result = json.loads((attempt_dir / 'result.json').read_text(encoding='utf-8'))
    if result['recipe_base_sha256'] != identity(_base_recipe(recipe)):
        raise ValueError('Attempt recipe identity differs')
    if result['address_space_id'] != recipe['address_space_id']:
        raise ValueError('Attempt address space differs from recipe')
    base = Path(recipe_path).resolve().parent
    expected_artifacts = []
    for artifact in recipe['artifacts']:
        path = Path(artifact['path'])
        expected_artifacts.append((str((path if path.is_absolute() else base / path).resolve()),
                                   artifact['sha256']))
    for phase in ('artifacts_before', 'artifacts_after'):
        reported = result[phase]
        if not isinstance(reported, list) or len(reported) != len(expected_artifacts):
            raise ValueError('Attempt artifact list differs from recipe')
        for item, (path, sha256) in zip(reported, expected_artifacts):
            if (type(item) is not dict or set(item) != {'path', 'expected', 'actual', 'verified'} or
                    item['path'] != path or item['expected'] != sha256 or
                    item['verified'] is not (item['actual'] == sha256)):
                raise ValueError('Attempt artifact identity report is inconsistent')
    prior, trace, count = ZERO, [], 0
    failure_records = 0
    for line in (attempt_dir / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        record = json.loads(line)
        record_hash = record.pop('record_sha256')
        if record.get('index') != count or record.get('previous_sha256') != prior or identity(record) != record_hash:
            raise ValueError('Transport journal chain differs')
        prior, count = record_hash, count + 1
        value = record['value']
        if record['kind'] == 'observation':
            event = validate_event(value['event'], recipe['limits']['max_payload_bytes'])
            trace.append(dict(kind='observation', value=dict(step=value['step'], phase=value['phase'],
                                                            event=_semantic_event(event))))
        elif record['kind'] in ('intervention_intent', 'intervention_receipt',
                                'copy_capability', 'queue_priority_capability'):
            trace.append(dict(kind=record['kind'], value=value))
        elif record['kind'] == 'failure':
            failure_records += 1
        else:
            raise ValueError('Unknown transport journal record')
    if prior != result['journal_tail_sha256'] or count != result['journal_records'] or identity(trace) != result['trace_sha256']:
        raise ValueError('Transport trace/report differs')
    if (sum(item['kind'] == 'observation' for item in trace) != result['events_seen'] or
            sum(item['kind'] == 'intervention_intent' for item in trace) != result['actions_attempted']):
        raise ValueError('Transport counters differ from journal')
    if result['observed_status'] not in STATUSES - {'REPLAY_DIVERGED'}:
        raise ValueError('Invalid observed transport status')
    expected_status = result['observed_status']
    if 'expected_trace_sha256' in recipe and (
            result['trace_sha256'] != recipe['expected_trace_sha256'] or
            result['observed_status'] != recipe['expected_status']):
        expected_status = 'REPLAY_DIVERGED'
    if result['status'] != expected_status:
        raise ValueError('Reported transport status contradicts observed status and recipe')
    if result['observed_status'] in ('TRANSPORT_VERIFIED', 'QUEUE_SCHEDULING_VERIFIED'):
        if not all('expect' in step for step in recipe['steps']) or not any(
                'action' in step for step in recipe['steps']):
            raise ValueError('Verified outcome requires postconditions and an intervention')
        scheduled = any(step.get('action', {}).get('kind') == 'queue_priority'
                        for step in recipe['steps'])
        if (result['observed_status'] == 'QUEUE_SCHEDULING_VERIFIED') is not scheduled:
            raise ValueError('Queue scheduling verdict differs from recipe actions')
        if failure_records:
            raise ValueError('Transport success contains a failure journal record')
        if result['steps_completed'] != len(recipe['steps']) or result['actions_attempted'] != sum(
                'action' in step for step in recipe['steps']):
            raise ValueError('Transport success counters differ from recipe')
        offset = 0
        for index, step in enumerate(recipe['steps']):
            applied = None
            for phase in ('trigger', 'expect'):
                if phase == 'expect' and 'expect' not in step:
                    break
                matched = False
                while offset < len(trace) and trace[offset]['kind'] == 'observation':
                    item = trace[offset]['value']
                    if item['step'] != index or item['phase'] != phase:
                        raise ValueError('Transport observation order differs')
                    offset += 1
                    if _match(step[phase], item['event']):
                        matched = True
                        break
                if not matched:
                    raise ValueError('Transport success lacks a matched observation')
                if phase == 'expect' and applied is not None:
                    try:
                        _check_postcondition_receipt(step['action'], applied, item['event'])
                    except RuntimeError as failure:
                        raise ValueError(str(failure)) from failure
                if phase == 'trigger' and 'action' in step:
                    action = step['action']
                    sequence = (['intervention_intent', 'copy_capability', 'intervention_receipt']
                                if action['kind'] in COPY_GATES else
                                ['intervention_intent', 'queue_priority_capability',
                                 'intervention_receipt'] if action['kind'] == 'queue_priority' else
                                ['intervention_intent', 'intervention_receipt'])
                    if [entry['kind'] for entry in trace[offset:offset + len(sequence)]] != sequence:
                        raise ValueError('Transport success lacks an intervention receipt')
                    intent = trace[offset]['value']
                    receipt = trace[offset + len(sequence) - 1]['value']
                    if (intent['step'] != index or intent['action'] != step['action'] or
                            intent['trigger_event_sha256'] != identity(item['event']) or
                            receipt['step'] != index):
                        raise ValueError('Intervention intent/receipt differs from recipe')
                    applied = receipt['receipt']
                    if applied.get('kind') != action['kind']:
                        raise ValueError('Intervention receipt kind differs')
                    if action['kind'] == 'gpio_mirror':
                        expected_receipt = dict(kind='gpio_mirror', source=item['event']['endpoint'],
                                                target=action['target'], level=item['event']['level'])
                        if applied != expected_receipt:
                            raise ValueError('GPIO receipt differs from observed source')
                    elif action['kind'] in ('dma_copy', 'queue_copy'):
                        source_event = item['event']
                        capability = trace[offset + 1]['value']
                        if capability['step'] != index or not _validate_copy_capability(
                                action['kind'], source_event, capability['proof']):
                            raise ValueError('Copy capability is denied or differs from descriptor')
                        if (applied.get('source') != source_event['source'] or
                                applied.get('target') != source_event['target'] or
                                applied.get('length') != source_event['length'] or
                                applied.get('capability_sha256') != identity(capability['proof']) or
                                applied.get('destination_sha256') != applied.get('payload_sha256') or
                                ('payload_sha256' in source_event and
                                 applied.get('payload_sha256') != source_event['payload_sha256'])):
                            raise ValueError('Copy receipt differs from observed descriptor')
                        _sha(applied.get('payload_sha256'), 'copy receipt')
                    elif action['kind'] == 'queue_priority':
                        capability = trace[offset + 1]['value']
                        if capability['step'] != index or not _validate_priority_capability(
                                item['event'], action, capability['proof'],
                                {artifact['sha256'] for artifact in recipe['artifacts']}):
                            raise ValueError('Queue priority capability is denied or differs from message')
                        proof = capability['proof']
                        raw = dict(queue=applied.get('queue'), owner=applied.get('owner'),
                                   message_id=applied.get('message_id'),
                                   length=applied.get('length'),
                                   before_priority=applied.get('before_priority'),
                                   after_priority=applied.get('after_priority'),
                                   native_status=applied.get('native_status'),
                                   payload_sha256_before=applied.get('payload_sha256_before'),
                                   payload_sha256_after=applied.get('payload_sha256_after'),
                                   header_before_hex=applied.get('header_before_hex'),
                                   header_after_hex=applied.get('header_after_hex'),
                                   witness_sha256=applied.get('witness_sha256'))
                        try:
                            checked = _validate_priority_receipt(item['event'], action, proof, raw)
                        except RuntimeError as failure:
                            raise ValueError(str(failure)) from failure
                        if applied != checked:
                            raise ValueError('Queue priority receipt differs from native setter evidence')
                    elif action['kind'] == 'irq_route':
                        expected_receipt = dict(kind='irq_route', irq=action['irq'],
                                                causal_payload_sha256=item['event']['payload_sha256'])
                        if applied != expected_receipt:
                            raise ValueError('IRQ receipt differs from observed commit')
                    elif action['kind'] == 'deliver_input':
                        expected_receipt = dict(kind='deliver_input', target=action['target'],
                                                code=item['event']['code'])
                        if applied != expected_receipt:
                            raise ValueError('Input receipt differs from observed request')
                    offset += len(sequence)
        if offset != len(trace):
            raise ValueError('Transport success includes unexplained trace records')
    if require_frozen and (recipe.get('expected_trace_sha256') is None or
                           result['trace_sha256'] != recipe['expected_trace_sha256'] or
                           result['observed_status'] != recipe['expected_status']):
        raise ValueError('Frozen transport trace differs')
    if not all(item['verified'] for item in result['artifacts_before'] + result['artifacts_after']):
        raise ValueError('Source artifact identity changed')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    verify = sub.add_parser('verify', help='Check a frozen recipe and an attempt journal')
    verify.add_argument('recipe', type=Path)
    verify.add_argument('attempt', type=Path)
    freeze = sub.add_parser('freeze', help='Bind an initial observed trace to a recipe')
    freeze.add_argument('recipe', type=Path)
    freeze.add_argument('attempt', type=Path)
    freeze.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = (verify_attempt(args.recipe, args.attempt) if args.command == 'verify'
                  else freeze_recipe(args.recipe, args.attempt, args.output))
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as failure:
        print(str(failure), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
