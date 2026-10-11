"""FAT12 allocation and source integrity gates using firmware-free fixtures."""
import hashlib
import struct

import pytest

from devkit import sony_components as sony
from devkit.core import Cancelled, Task, write_json


def fixture():
    image = bytearray(12 * 512)
    struct.pack_into('<HBHBH', image, 11, 512, 1, 1, 1, 16)
    struct.pack_into('<H', image, 19, 12)
    struct.pack_into('<H', image, 22, 1)
    image[510:512] = b'\x55\xaa'
    image[512:515] = b'\xf8\xff\xff'
    set_fat(image, 2, 3); set_fat(image, 3, 0xfff); set_fat(image, 4, 0xfff)
    for i, (name, cluster, content) in enumerate([
        (b'VMLINUX BIN', 2, b'K' * 600), (b'INITRD  IMG', 4, b'rootfs')]):
        pos = 1024 + i * 32
        image[pos:pos+11] = name; image[pos+11] = 0x20
        struct.pack_into('<H', image, pos+26, cluster)
        struct.pack_into('<I', image, pos+28, len(content))
        start = 1536+(cluster-2)*512
        image[start:start+len(content)] = content
    return image


def set_fat(image, cluster, value):
    pos = 512 + cluster + cluster//2
    old = struct.unpack_from('<H', image, pos)[0]
    new = ((old & 0x000f) | (value << 4)) if cluster & 1 else ((old & 0xf000) | value)
    struct.pack_into('<H', image, pos, new)


def test_named_files_follow_fat_chains_without_mutation():
    image = fixture(); original = bytes(image)
    assert sony.fat12_inputs(image) == {'VMLINUX.BIN': b'K'*600, 'INITRD.IMG': b'rootfs'}
    assert bytes(image) == original


@pytest.mark.parametrize('cluster,value', [(3,2),(2,4),(3,0),(3,0xff7),(3,30)])
def test_loop_overlap_bad_cluster_and_escape_rejected(cluster, value):
    image = fixture(); set_fat(image, cluster, value)
    with pytest.raises(ValueError, match='cluster|allocation'): sony.fat12_inputs(image)


def test_duplicate_root_entry_and_truncated_allocation_rejected():
    image = fixture(); image[1056:1088] = image[1024:1056]
    with pytest.raises(ValueError, match='Duplicate'): sony.fat12_inputs(image)
    image = fixture(); struct.pack_into('<I', image, 1024+28, 1200)
    with pytest.raises(ValueError, match='allocation'): sony.fat12_inputs(image)


@pytest.mark.parametrize('offset,value', [(11,0),(13,3),(16,0),(19,65535)])
def test_geometry_cannot_escape_input(offset, value):
    image = fixture()
    if offset in (13,16): image[offset] = value
    else: struct.pack_into('<H', image, offset, value)
    with pytest.raises(ValueError): sony.fat12_inputs(image)


def test_cancelled_parser_stops_before_allocating_outputs():
    task = Task(); task.cancelled.set()
    with pytest.raises(Cancelled): sony.fat12_inputs(fixture(), task)


def test_component_cache_rehashed_and_original_preserved(tmp_path, monkeypatch):
    image = bytes(fixture()); sha = lambda value: hashlib.sha256(value).hexdigest()
    source = b'original firmware source'
    profile = dict(sony.PROFILES['a7iv-2.00'], source_sha256=sha(source), source_size=len(source),
                   partition=('part.bin', len(image), sha(image)), inputs={
        'kernel': ('VMLINUX.BIN','kernel.bin',600,sha(b'K'*600)),
        'rootfs': ('INITRD.IMG','initrd.img',6,sha(b'rootfs')),
    })
    monkeypatch.setattr(sony, 'PROFILES', {'a7iv-2.00':profile})
    write_json(tmp_path/'device.json', {'sha256':sha(source)})
    (tmp_path/'source').mkdir(); (tmp_path/'source/firmware.bin').write_bytes(source)
    write_json(tmp_path/'extracted/extraction.json', {'source_sha256':sha(source),
        'artifacts':[{'path':'part.bin','size':len(image),'sha256':sha(image)}]})
    source = tmp_path/'extracted/part.bin'; source.write_bytes(image)
    paths = sony.prepare_components(tmp_path)
    assert paths['kernel'].read_bytes() == b'K'*600
    assert source.read_bytes() == image
    paths['kernel'].write_bytes(b'X'*600)
    with pytest.raises(ValueError, match='SHA-256'): sony.prepare_components(tmp_path)
    assert source.read_bytes() == image


@pytest.mark.parametrize('key', ['a7iv-2.00', 'a7rv-4.00'])
def test_profile_selection_requires_exact_source_version(tmp_path, key):
    profile = sony.PROFILES[key]
    write_json(tmp_path/'device.json', {'sha256':profile['source_sha256']})
    assert sony.profile_for_device(tmp_path) == profile
    write_json(tmp_path/'device.json', {'sha256':'0'*64, 'camera':{'model':profile['model']}})
    assert sony.profile_for_device(tmp_path) is None


def test_profile_component_sets_are_not_interchangeable():
    first, second = sony.PROFILES.values()
    assert first['partition'] != second['partition']
    assert first['inputs']['kernel'][2:] != second['inputs']['kernel'][2:]
    assert first['inputs']['rootfs'][2:] != second['inputs']['rootfs'][2:]


def test_source_metadata_cannot_substitute_unverified_bytes(tmp_path, monkeypatch):
    sha = hashlib.sha256(b'expected').hexdigest()
    profile = dict(sony.PROFILES['a7iv-2.00'], source_sha256=sha, source_size=8)
    monkeypatch.setattr(sony, 'PROFILES', {'a7iv-2.00':profile})
    write_json(tmp_path/'device.json', {'sha256':sha})
    (tmp_path/'source').mkdir(); (tmp_path/'source/firmware.bin').write_bytes(b'modified')
    with pytest.raises(ValueError, match='source SHA-256'): sony.prepare_components(tmp_path)
    assert not (tmp_path/'sony-lab-inputs').exists()


def test_internal_symlink_is_not_hidden_by_resolve(tmp_path):
    target = tmp_path/'actual'; target.mkdir()
    try: (tmp_path/'link').symlink_to(target, target_is_directory=True)
    except OSError: pytest.skip('Host does not allow symlink creation')
    with pytest.raises(ValueError, match='link or junction'): sony._path(tmp_path, 'link/file')
