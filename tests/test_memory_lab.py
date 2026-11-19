import copy
import base64
import hashlib
import json
from pathlib import Path

import pytest
from pyfdt.pyfdt import Fdt, FdtNode
from devkit.memory_lab import MemoryDesktopSession, add_reservations, parse_probes, reg_pairs, tree_from, validate_profile


@pytest.fixture
def profile():
    path = Path(__file__).resolve().parents[1]/'research/x2dii-memory/eagle2-hb722-1.2.7.16.json'
    return json.loads(path.read_text(encoding='utf-8'))


def test_captured_memory_accounting(profile):
    info = validate_profile(profile)
    assert info['backing_bytes'] == 16*1024**3
    assert info['ram_holes'] == [(0xe8000000, 0xec000000)]
    assert info['unreserved_bytes_before_kernel'] == 1264582656
    assert profile['linux_memtotal_bytes'] < info['unreserved_bytes_before_kernel']
    assert len(profile['reserved_regions']) == 25


@pytest.mark.parametrize('mutation', ['overlap', 'outside', 'unaligned', 'heap_sum', 'negative'])
def test_reject_corrupted_capture(profile, mutation):
    bad = copy.deepcopy(profile)
    if mutation == 'overlap': bad['reserved_regions'][1]['address'] = '0x20000000'
    elif mutation == 'outside': bad['reserved_regions'][0]['address'] = '0x400000000'
    elif mutation == 'unaligned': bad['reserved_regions'][0]['size_bytes'] += 1
    elif mutation == 'heap_sum': bad['ion_reserved_bytes'] += 4096
    elif mutation == 'negative': bad['ion_heaps'][0]['free_bytes'] = -1
    with pytest.raises(ValueError): validate_profile(bad)


def test_translated_dtb_roundtrip_preserves_flags(profile):
    tree = Fdt(); tree.add_rootnode(FdtNode('/'))
    mapping = add_reservations(tree, profile)
    actual = tree_from(tree.to_dtb())
    assert len(mapping) == 26
    for entry in mapping:
        assert int(entry['guest_address'],0)-int(entry['address'],0) == 0x40000000
        props = {p.name:p for p in actual.resolve_path(entry['guest_node'])}
        assert reg_pairs(props['reg']) == [(int(entry['guest_address'],0),entry['size_bytes'])]
        assert ('no-map' in props) == entry['no_map']
    assert reg_pairs(actual.resolve_path('/reserved-memory/ram-hole@128000000/reg')) == [(0x128000000,0x4000000)]
    with pytest.raises(ValueError): add_reservations(actual,profile)


def test_snapshot_transport_rejects_serial_corruption():
    snapshot=b'QE_MEMORY_BEGIN camera\nQE_MEMORY_SECTION meminfo\nMemTotal: 886064 kB\nQE_MEMORY_END\n'
    encoded=base64.b64encode(snapshot).decode()
    digest=hashlib.sha256(snapshot).hexdigest()
    wire=f'QE_PROBE_BEGIN camera\nQE_PROBE_DATA:{encoded}\nQE_PROBE_SHA {digest}\nQE_PROBE_END\n'
    assert parse_probes(wire)['camera']['meminfo_kib']['MemTotal']==886064
    corrupted=wire.replace('QE_PROBE_DATA:'+encoded,'QE_PROBE_DATA:'+base64.b64encode(snapshot.replace(b'886064',b'986064')).decode())
    with pytest.raises(ValueError,match='checksum mismatch'): parse_probes(corrupted)


def test_probe_blocks_following_serial_commands(profile,tmp_path):
    session=MemoryDesktopSession(tmp_path,{}, {},profile)
    assert session.can_send_command()
    session.on_command_sent('sh /mnt/x2dii/camera/memory_probe.sh camera')
    assert not session.can_send_command()
    session.on_guest_line('QE_PROBE_SHA '+64*'a')
    assert not session.can_send_command()
    session.on_guest_line('QE_PROBE_END')
    assert session.can_send_command()
