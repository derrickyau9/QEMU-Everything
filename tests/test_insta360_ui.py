import hashlib
import importlib.util
import json
from pathlib import Path
import struct
from unittest.mock import Mock

import pytest

from devkit import insta360_ui as lab
from devkit.core import Task, read_json, write_json


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location('x3_gui_runner', lab.RUNNER)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def guest(tmp_path, monkeypatch):
    device, home = tmp_path/'device', tmp_path/'home'
    blob = b'owned original RTOS test fixture'
    sha = hashlib.sha256(blob).hexdigest()
    monkeypatch.setattr(lab,'RTOS_SIZE',len(blob)); monkeypatch.setattr(lab,'RTOS_SHA256',sha)
    write_json(device/'device.json',dict(sha256=lab.SOURCE_SHA256))
    write_json(device/'extracted/extraction.json',dict(source_sha256=lab.SOURCE_SHA256,
        artifacts=[dict(path='rtos.bin',size=len(blob),sha256=sha)]))
    (device/'extracted/rtos.bin').write_bytes(blob)
    write_json(home/'insta360-ui-lab.json',dict(distro='Debian',manifest='/private/build/insta360-ui-build.json'))
    return device, home


def test_cheap_eligibility_does_not_hash_or_launch(guest,monkeypatch):
    monkeypatch.setattr(lab,'_hash',Mock(side_effect=AssertionError()))
    monkeypatch.setattr(lab.subprocess,'Popen',Mock(side_effect=AssertionError()))
    assert lab.lab_available(*guest)
    (guest[0]/'extracted/rtos.bin').write_bytes(b'truncated')
    assert not lab.lab_available(*guest)


@pytest.mark.parametrize('change',['escape','duplicate','wrong_source'])
def test_manifest_cannot_redirect_or_misidentify_guest(guest,change):
    path = guest[0]/'extracted/extraction.json'; value=read_json(path)
    if change=='escape': value['artifacts'][0]['path']='../../outside.bin'
    if change=='duplicate': value['artifacts'].append(value['artifacts'][0])
    if change=='wrong_source': value['source_sha256']='0'*64
    write_json(path,value)
    assert not lab.lab_available(*guest)


def test_launch_hashes_same_size_modified_original(guest):
    path=guest[0]/'extracted/rtos.bin'; blob=path.read_bytes(); path.write_bytes(b'x'*len(blob))
    with pytest.raises(ValueError,match='changed'):
        lab._build_command_locked(*guest,Task(),'probe',20)
    assert not (guest[0]/'insta360-ui').exists()


@pytest.mark.parametrize('manifest',['../build.json','/safe/../unsafe.json','/safe/a\n.json'])
def test_invalid_build_path_rejected(guest,manifest):
    write_json(guest[1]/'insta360-ui-lab.json',dict(distro='Debian',manifest=manifest))
    assert not lab.lab_available(*guest)


def test_pinned_ram_hook_extent_enforced(runner):
    header=bytearray(52); header[:7]=b'\x7fELF\x01\x01\x01'
    struct.pack_into('<H',header,18,40); struct.pack_into('<I',header,28,52)
    count=len(runner.REGIONS)+1
    struct.pack_into('<HH',header,42,32,count)
    data=bytearray(); ph=bytearray()
    for address,size in [*runner.REGIONS.items(),(0x1ffc0000,4)]:
        ph.extend(struct.pack('<8I',1,52+count*32+len(data),address,address,size,size,5,4)); data.extend(bytes(size))
    elf=header+ph+data
    bootstrap,patches=runner.platform_parts(elf)
    assert len(bootstrap)==4 and sum(map(len,patches.values()))==68
    struct.pack_into('<I',elf,52+12,0x20000)
    with pytest.raises(ValueError): runner.platform_parts(elf)


def test_frame_dimensions_and_lossless_png(runner):
    pixels=bytes(range(256))*(runner.WIDTH*runner.HEIGHT*3//256)+bytes(range((runner.WIDTH*runner.HEIGHT*3)%256))
    assert runner.decode_ppm(b'P6\n352 476\n255\n'+pixels)==pixels
    encoded=runner.rgb_png(pixels)
    import zlib
    offset=8; raw=b''
    while offset<len(encoded):
        length=struct.unpack_from('>I',encoded,offset)[0]
        if encoded[offset+4:offset+8]==b'IDAT': raw+=encoded[offset+8:offset+8+length]
        offset+=length+12
    decoded=zlib.decompress(raw)
    assert b''.join(decoded[y*(352*3+1)+1:(y+1)*(352*3+1)] for y in range(476))==pixels
    with pytest.raises(ValueError): runner.decode_ppm(b'P6\n1 1\n255\nabc')


@pytest.mark.parametrize('x,y,pressed',[(352,1,True),(-1,0,True),(1,476,True),(True,1,True),(1,1,1)])
def test_bad_touch_rejected_before_qmp(runner,x,y,pressed):
    qmp=Mock()
    with pytest.raises(ValueError): runner.touch(qmp,x,y,pressed)
    qmp.call.assert_not_called()


def test_touch_uses_qmp_absolute_input(runner):
    qmp=Mock(); runner.touch(qmp,351,475,True)
    args=qmp.call.call_args
    assert args.args[0]=='input-send-event'
    assert args.args[1]['events'][0]['data']['value']==32767
    assert args.args[1]['events'][2]['data']=={'button':'left','down':True}


def test_failed_preflight_does_not_write_existing_destination(runner,tmp_path,monkeypatch):
    out=tmp_path/'existing'; out.mkdir(); evidence=out/'evidence.json'; evidence.write_text('keep')
    monkeypatch.setattr(runner,'validate',Mock(side_effect=ValueError('invalid source')))
    assert runner.main(dict(destination=str(out)))==1
    assert evidence.read_text()=='keep'


@pytest.mark.skipif(__import__('os').name=='nt',reason='POSIX immutable snapshot')
def test_snapshot_rejects_wrong_bytes(runner,tmp_path):
    source=tmp_path/'source'; source.write_bytes(b'wrong')
    with pytest.raises(ValueError,match='changed'):
        runner.snapshot(source,tmp_path/'copy',5,hashlib.sha256(b'right').hexdigest())
