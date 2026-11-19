"""P4 receipts must bind the original executable and remain honest about UI proof."""

import hashlib
import json
import struct

import pytest

from devkit.adaptive import p4


def elf_component():
    image = bytearray(0x200)
    image[:16] = b'\x7fELF\x02\x01\x01' + bytes(9)
    struct.pack_into('<HHIQQQIHHHHHH', image, 16,
                     3, 183, 1, 0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    struct.pack_into('<IIQQQQQQ', image, 64,
                     1, 5, 0x100, 0x400000, 0x400000, 0x40, 0x40, 0x1000)
    image[0x100:0x140] = bytes(range(0x40))
    return bytes(image)


def recipe_for(component):
    context = component[0x110:0x130]
    return {
        'schema_version': 1, 'contract': p4.CONTRACT,
        'component_sha256': hashlib.sha256(component).hexdigest(),
        'relocated_offset': '0x400020', 'file_offset': '0x120',
        'context_file_offset': '0x110', 'context_hex': context.hex(),
        'context_sha256': hashlib.sha256(context).hexdigest(),
        'before_hex': component[0x120:0x124].hex(),
        'after_hex': 'e24f8052', 'reason': 'bounded branch route experiment',
    }


def test_private_patch_observation_and_rollback_retains_original(tmp_path):
    source = tmp_path / 'component.so'
    original = elf_component()
    source.write_bytes(original)
    attempt = tmp_path / 'attempt'
    report = p4.apply_patch(recipe_for(original), source, attempt)
    private = attempt / source.name
    assert source.read_bytes() == original
    assert private.read_bytes()[0x120:0x124] == bytes.fromhex('e24f8052')
    assert [record['phase'] for record in report['records']] == ['intent', 'applied']
    assert report['ui_verdict'] == 'UNVERIFIED'
    assert report['records'][-1]['value']['readback_hex'] == 'e24f8052'
    artifact = attempt / 'guest-serial.txt'
    artifact.write_text('bounded guest observation; no frame yet\n', encoding='utf-8')
    observed = p4.observe(attempt / 'receipt.json', artifact)
    assert observed['records'][-1]['value']['evidence_sha256'] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert observed['ui_verdict'] == 'UNVERIFIED'
    rolled_back = p4.rollback(attempt / 'receipt.json')
    assert [record['phase'] for record in rolled_back['records']] == [
        'intent', 'applied', 'observed', 'rolled_back']
    assert not private.exists() and source.read_bytes() == original
    assert p4.verify_receipt(attempt / 'receipt.json') == rolled_back


@pytest.mark.parametrize('change,match', [
    ({'component_sha256': '0' * 64}, 'source component SHA-256 mismatch'),
    ({'relocated_offset': '0x400024'}, 'does not map'),
    ({'file_offset': '0x124'}, 'before bytes differ from declared context'),
])
def test_wrong_source_or_relocation_fails_before_private_copy(tmp_path, change, match):
    source = tmp_path / 'component.so'
    original = elf_component()
    source.write_bytes(original)
    recipe = recipe_for(original)
    recipe.update(change)
    # A wrong file offset would violate the declared context; preserve the
    # rejection regardless of whether validation or source check catches it.
    with pytest.raises(ValueError, match=match):
        p4.apply_patch(recipe, source, tmp_path / 'attempt')
    assert source.read_bytes() == original
    assert not (tmp_path / 'attempt' / source.name).exists()
    if (tmp_path / 'attempt').exists():
        report = p4.verify_receipt(tmp_path / 'attempt' / 'receipt.json')
        assert report['records'][-1]['phase'] == 'failure'


def test_matching_hash_but_changed_context_rejected(tmp_path):
    original = bytearray(elf_component())
    recipe = recipe_for(bytes(original))
    original[0x117] ^= 0xff
    source = tmp_path / 'component.so'
    source.write_bytes(original)
    recipe['component_sha256'] = hashlib.sha256(original).hexdigest()
    with pytest.raises(ValueError, match='context'):
        p4.apply_patch(recipe, source, tmp_path / 'attempt')
    report = p4.verify_receipt(tmp_path / 'attempt' / 'receipt.json')
    assert report['records'][-1]['phase'] == 'failure'
    assert not (tmp_path / 'attempt' / source.name).exists()


def test_mutated_private_copy_blocks_rollback_and_preserves_evidence(tmp_path):
    original = elf_component()
    source = tmp_path / 'component.so'
    source.write_bytes(original)
    attempt = tmp_path / 'attempt'
    p4.apply_patch(recipe_for(original), source, attempt)
    private = attempt / source.name
    edited = bytearray(private.read_bytes())
    edited[0x123] ^= 1
    private.write_bytes(edited)
    with pytest.raises(ValueError, match='private copy changed'):
        p4.rollback(attempt / 'receipt.json')
    assert private.exists() and source.read_bytes() == original
    assert p4.verify_receipt(attempt / 'receipt.json')['records'][-1]['phase'] == 'failure'


def test_receipt_chain_rejects_edit_and_recipe_cannot_claim_success(tmp_path):
    original = elf_component()
    source = tmp_path / 'component.so'
    source.write_bytes(original)
    recipe = recipe_for(original)
    with pytest.raises(ValueError, match='Unsupported P4'):
        p4.validate_recipe(dict(recipe, ui_success=True))
    attempt = tmp_path / 'attempt'
    p4.apply_patch(recipe, source, attempt)
    receipt_path = attempt / 'receipt.json'
    report = json.loads(receipt_path.read_text(encoding='utf-8'))
    report['records'][1]['value']['readback_hex'] = '00000000'
    receipt_path.write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(ValueError, match='chain diverged'):
        p4.verify_receipt(receipt_path)
