"""Provenance for one bounded, offline P4 executable-code patch.

This only changes an exclusive private component copy. A readback and attached
runtime log are evidence of the intervention, not a UI-success oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

from .contracts import digest, identity, save_json


CONTRACT = 'molten-p4-branch-v1'
MAX_COMPONENT_BYTES = 512 * 1024 * 1024
HASH = re.compile(r'^[0-9a-f]{64}$')
HEX = re.compile(r'^0x(?:0|[1-9a-f][0-9a-f]*)$')
FIELDS = {'schema_version', 'contract', 'component_sha256',
          'relocated_offset', 'file_offset', 'context_file_offset',
          'context_hex', 'context_sha256', 'before_hex', 'after_hex',
          'reason'}


def _offset(value, name):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError(f'{name} must be canonical lowercase hexadecimal')
    number = int(value, 16)
    if number > (1 << 64) - 1:
        raise ValueError(f'{name} exceeds uint64')
    return number


def _bytes(value, name, limit):
    if not isinstance(value, str) or len(value) % 2 or len(value) > limit * 2:
        raise ValueError(f'{name} must be bounded lowercase hex bytes')
    if not re.fullmatch(r'[0-9a-f]*', value):
        raise ValueError(f'{name} must be bounded lowercase hex bytes')
    return bytes.fromhex(value)


def validate_recipe(recipe):
    if (not isinstance(recipe, dict) or set(recipe) != FIELDS or
            recipe.get('schema_version') != 1 or recipe.get('contract') != CONTRACT):
        raise ValueError('Unsupported P4 patch recipe')
    for key in ('component_sha256', 'context_sha256'):
        if not isinstance(recipe[key], str) or not HASH.fullmatch(recipe[key]):
            raise ValueError(f'{key} must be a lowercase SHA-256 digest')
    relocated = _offset(recipe['relocated_offset'], 'relocated_offset')
    file_offset = _offset(recipe['file_offset'], 'file_offset')
    context_offset = _offset(recipe['context_file_offset'], 'context_file_offset')
    before = _bytes(recipe['before_hex'], 'before_hex', 16)
    after = _bytes(recipe['after_hex'], 'after_hex', 16)
    context = _bytes(recipe['context_hex'], 'context_hex', 256)
    if not before or len(before) != len(after) or before == after:
        raise ValueError('P4 patch must change 1..16 bytes without changing length')
    if len(context) < len(before) + 8 or not context_offset + 4 <= file_offset:
        raise ValueError('P4 patch requires at least four context bytes on each side')
    if file_offset + len(before) + 4 > context_offset + len(context):
        raise ValueError('P4 patch is outside its bounded context')
    if context[file_offset - context_offset:file_offset - context_offset + len(before)] != before:
        raise ValueError('P4 before bytes differ from declared context')
    if hashlib.sha256(context).hexdigest() != recipe['context_sha256']:
        raise ValueError('P4 context fingerprint differs from declared bytes')
    if not isinstance(recipe['reason'], str) or not 1 <= len(recipe['reason']) <= 512:
        raise ValueError('P4 intervention requires a short reason')
    if any(ord(char) < 32 and char not in '\t' for char in recipe['reason']):
        raise ValueError('P4 reason contains control characters')
    if relocated + len(before) > 1 << 64:
        raise ValueError('P4 relocated range wraps uint64')
    return recipe


def _executable_file_offset(component, relocated, length):
    if component[:4] != b'\x7fELF' or component[4] not in (1, 2) or component[5] not in (1, 2):
        raise ValueError('P4 component must be ELF32 or ELF64')
    endian = '<' if component[5] == 1 else '>'
    if component[4] == 2:
        if len(component) < 64:
            raise ValueError('Truncated ELF header')
        phoff, phentsize, phnum = struct.unpack_from(endian + 'Q14xHH', component, 32)
        entry_format, minimum = endian + 'IIQQQQQQ', 56
    else:
        if len(component) < 52:
            raise ValueError('Truncated ELF header')
        phoff = struct.unpack_from(endian + 'I', component, 28)[0]
        phentsize, phnum = struct.unpack_from(endian + 'HH', component, 42)
        entry_format, minimum = endian + 'IIIIIIII', 32
    if phnum == 0 or phnum > 4096 or phentsize < minimum or phoff + phentsize * phnum > len(component):
        raise ValueError('Invalid ELF program-header table')
    matches = []
    for index in range(phnum):
        values = struct.unpack_from(entry_format, component, phoff + index * phentsize)
        if component[4] == 2:
            p_type, flags, p_offset, p_vaddr, _, filesz, _, _ = values
        else:
            p_type, p_offset, p_vaddr, _, filesz, _, flags, _ = values
        if p_type == 1 and flags & 1 and p_vaddr <= relocated and relocated + length <= p_vaddr + filesz:
            file_offset = p_offset + relocated - p_vaddr
            if file_offset + length <= len(component):
                matches.append(file_offset)
    if len(matches) != 1:
        raise ValueError('P4 relocated range must map uniquely into executable ELF bytes')
    return matches[0]


def _append(report, phase, value):
    prior = report['records'][-1]['sha256'] if report['records'] else '0' * 64
    record = {'phase': phase, 'value': value, 'prior_sha256': prior}
    record['sha256'] = identity(record)
    report['records'].append(record)
    save_json(report['receipt_path'], report)
    return report


def verify_receipt(receipt_path):
    receipt_path = Path(receipt_path).resolve()
    report = json.loads(receipt_path.read_text(encoding='utf-8'))
    if (set(report) != {'contract', 'schema_version', 'receipt_path', 'source_path',
                        'private_path', 'recipe_sha256', 'records', 'ui_verdict',
                        'intervention_prefix'} or
            report['contract'] != CONTRACT or report['schema_version'] != 1 or
            report['receipt_path'] != str(receipt_path) or report['ui_verdict'] != 'UNVERIFIED' or
            report['intervention_prefix'] != 'P4'):
        raise ValueError('Invalid P4 receipt envelope')
    if not isinstance(report['recipe_sha256'], str) or not HASH.fullmatch(report['recipe_sha256']):
        raise ValueError('Invalid P4 recipe digest')
    if not isinstance(report['source_path'], str) or not isinstance(report['private_path'], str):
        raise ValueError('Invalid P4 receipt paths')
    source = Path(report['source_path']).resolve()
    private = Path(report['private_path']).resolve()
    if (private.parent != receipt_path.parent or private.name != source.name or
            private == source):
        raise ValueError('P4 receipt private path is outside its attempt directory')
    recipe_path = receipt_path.parent / 'recipe.json'
    recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
    validate_recipe(recipe)
    if identity(recipe) != report['recipe_sha256']:
        raise ValueError('P4 saved recipe differs from receipt')
    prior = '0' * 64
    if not isinstance(report['records'], list) or not report['records']:
        raise ValueError('P4 receipt has no records')
    for record in report['records']:
        if (not isinstance(record, dict) or set(record) != {'phase', 'value', 'prior_sha256', 'sha256'} or
                record['prior_sha256'] != prior or not HASH.fullmatch(record['sha256']) or
                identity({key: record[key] for key in ('phase', 'value', 'prior_sha256')}) != record['sha256']):
            raise ValueError('P4 receipt chain diverged')
        prior = record['sha256']
    if (report['records'][0]['phase'] != 'intent' or
            report['records'][0]['value'] != {
                'component_sha256': recipe['component_sha256'],
                'relocated_offset': recipe['relocated_offset'],
                'file_offset': recipe['file_offset'],
                'before_hex': recipe['before_hex'],
                'after_hex': recipe['after_hex'],
                'source_path': str(source), 'private_path': str(private),
                'receipt_path': str(receipt_path),
                'recipe_sha256': report['recipe_sha256'],
            }):
        raise ValueError('P4 receipt intent differs from recipe or paths')
    phases = [record['phase'] for record in report['records']]
    if phases not in (['intent'], ['intent', 'failure']):
        if phases[:2] != ['intent', 'applied']:
            raise ValueError('P4 receipt phases are invalid')
        tail = phases[2:]
        if tail and tail[-1] in ('rolled_back', 'failure'):
            tail = tail[:-1]
        if any(phase != 'observed' for phase in tail):
            raise ValueError('P4 receipt phases are invalid')
    return report


def apply_patch(recipe, component_path, attempt_dir):
    """Create a new private patched ELF and retain intent, readback, or failure."""
    if isinstance(recipe, (str, Path)):
        recipe = json.loads(Path(recipe).read_text(encoding='utf-8'))
    validate_recipe(recipe)
    source = Path(component_path).resolve(strict=True)
    attempt_dir = Path(attempt_dir).resolve()
    if attempt_dir.exists() or attempt_dir == source or source in attempt_dir.parents:
        raise ValueError('P4 attempt directory must be new and separate from source')
    attempt_dir.mkdir(parents=True, exist_ok=False)
    private = attempt_dir / source.name
    receipt_path = attempt_dir / 'receipt.json'
    report = {'schema_version': 1, 'contract': CONTRACT,
              'receipt_path': str(receipt_path), 'source_path': str(source),
              'private_path': str(private), 'recipe_sha256': identity(recipe),
              'records': [], 'ui_verdict': 'UNVERIFIED',
              'intervention_prefix': 'P4'}
    save_json(attempt_dir / 'recipe.json', recipe)
    _append(report, 'intent', {'component_sha256': recipe['component_sha256'],
                               'relocated_offset': recipe['relocated_offset'],
                               'file_offset': recipe['file_offset'],
                               'before_hex': recipe['before_hex'],
                               'after_hex': recipe['after_hex'],
                               'source_path': str(source), 'private_path': str(private),
                               'receipt_path': str(receipt_path),
                               'recipe_sha256': identity(recipe)})
    try:
        if source.stat().st_size > MAX_COMPONENT_BYTES:
            raise ValueError('P4 component exceeds bounded copy size')
        component = source.read_bytes()
        if len(component) > MAX_COMPONENT_BYTES:
            raise ValueError('P4 component exceeds bounded copy size')
        actual = hashlib.sha256(component).hexdigest()
        if actual != recipe['component_sha256']:
            raise ValueError('P4 source component SHA-256 mismatch')
        relocated = _offset(recipe['relocated_offset'], 'relocated_offset')
        file_offset = _offset(recipe['file_offset'], 'file_offset')
        before = bytes.fromhex(recipe['before_hex'])
        after = bytes.fromhex(recipe['after_hex'])
        if _executable_file_offset(component, relocated, len(before)) != file_offset:
            raise ValueError('P4 relocated offset does not map to declared file offset')
        context_offset = _offset(recipe['context_file_offset'], 'context_file_offset')
        context = bytes.fromhex(recipe['context_hex'])
        if component[context_offset:context_offset + len(context)] != context or component[
                file_offset:file_offset + len(before)] != before:
            raise ValueError('P4 original code context or before bytes differ')
        patched = bytearray(component)
        patched[file_offset:file_offset + len(after)] = after
        with private.open('xb') as output:
            output.write(patched)
        patched_sha = digest(private)
        if (patched_sha != hashlib.sha256(patched).hexdigest() or
                private.read_bytes()[file_offset:file_offset + len(after)] != after or
                digest(source) != actual):
            raise RuntimeError('P4 patched clone readback or original component changed')
        _append(report, 'applied', {'private_sha256': patched_sha,
                                    'source_sha256_after': actual,
                                    'readback_hex': after.hex(),
                                    'private_bytes': len(patched)})
    except Exception as error:
        _append(report, 'failure', {'error_type': type(error).__name__, 'message': str(error)})
        raise
    return report


def observe(receipt_path, evidence_path):
    """Attach a raw runtime artifact; interpretation belongs to an external oracle."""
    report = verify_receipt(receipt_path)
    if report['records'][-1]['phase'] not in ('applied', 'observed'):
        raise ValueError('P4 observation requires an applied private copy')
    applied = next(record['value'] for record in report['records'] if record['phase'] == 'applied')
    if digest(report['private_path']) != applied['private_sha256']:
        raise ValueError('P4 private copy changed before observation')
    evidence = Path(evidence_path).resolve(strict=True)
    if evidence in (Path(report['source_path']), Path(report['private_path']), Path(report['receipt_path'])):
        raise ValueError('P4 observation requires a separate runtime artifact')
    _append(report, 'observed', {'evidence_path': str(evidence),
                                'evidence_sha256': digest(evidence),
                                'meaning': 'raw artifact only; original code/frame not verified'})
    return report


def rollback(receipt_path):
    """Discard only an unchanged private copy; preserve every receipt record."""
    report = verify_receipt(receipt_path)
    if report['records'][-1]['phase'] not in ('applied', 'observed'):
        raise ValueError('P4 rollback requires an applied private copy')
    applied = next(record['value'] for record in report['records'] if record['phase'] == 'applied')
    try:
        if digest(report['private_path']) != applied['private_sha256']:
            raise ValueError('P4 private copy changed; retain it for investigation')
        expected_source = report['records'][0]['value']['component_sha256']
        if digest(report['source_path']) != expected_source:
            raise ValueError('P4 original component changed; retain private copy')
        Path(report['private_path']).unlink()
        _append(report, 'rolled_back', {'private_copy_removed': True,
                                        'source_sha256_after': digest(report['source_path'])})
    except Exception as error:
        _append(report, 'failure', {'error_type': type(error).__name__, 'message': str(error)})
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    apply_cmd = commands.add_parser('apply')
    apply_cmd.add_argument('recipe')
    apply_cmd.add_argument('component')
    apply_cmd.add_argument('attempt_dir')
    observe_cmd = commands.add_parser('observe')
    observe_cmd.add_argument('receipt')
    observe_cmd.add_argument('runtime_artifact')
    rollback_cmd = commands.add_parser('rollback')
    rollback_cmd.add_argument('receipt')
    verify_cmd = commands.add_parser('verify')
    verify_cmd.add_argument('receipt')
    args = parser.parse_args(argv)
    if args.command == 'apply':
        result = apply_patch(args.recipe, args.component, args.attempt_dir)
    elif args.command == 'observe':
        result = observe(args.receipt, args.runtime_artifact)
    elif args.command == 'rollback':
        result = rollback(args.receipt)
    else:
        result = verify_receipt(args.receipt)
    print(json.dumps({'receipt': result['receipt_path'],
                      'phase': result['records'][-1]['phase'],
                      'ui_verdict': result['ui_verdict']}))


if __name__ == '__main__':
    main()
