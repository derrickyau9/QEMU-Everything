import hashlib
import importlib.util
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import zlib

import pytest

BOARD = Path(__file__).resolve().parents[1]/'scripts/boards/gopro'


@pytest.fixture
def platform(monkeypatch):
    # Runtime is Linux/WSL; these tests exercise ordinary immutable files on
    # Windows too, where POSIX open flags do not exist.
    if not hasattr(os,'O_NOFOLLOW'):monkeypatch.setattr(os,'O_NOFOLLOW',0,raising=False)
    if not hasattr(os,'O_NONBLOCK'):monkeypatch.setattr(os,'O_NONBLOCK',0,raising=False)
    monkeypatch.syspath_prepend(str(BOARD))
    for name in ('component','run_ui','component_viewer'):
        monkeypatch.delitem(sys.modules,name,raising=False)
    spec=importlib.util.spec_from_file_location('component',BOARD/'component.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules,'component',module)
    return module


def test_snapshot_copies_exact_checked_bytes(platform,tmp_path):
    source=tmp_path/'input';source.write_bytes(b'original firmware')
    target=tmp_path/'copy'
    platform.freeze(source,target,hashlib.sha256(source.read_bytes()).hexdigest(),17)
    assert target.read_bytes()==source.read_bytes()


@pytest.mark.parametrize('kind',('hash','size','bound'))
def test_snapshot_rejects_wrong_fingerprint_or_bounds(platform,tmp_path,kind):
    source=tmp_path/'input';source.write_bytes(b'1234')
    params=dict(expected=hashlib.sha256(b'1234').hexdigest(),size=4,bound=4)
    params[{'hash':'expected','size':'size','bound':'bound'}[kind]]={'hash':'0'*64,'size':3,'bound':3}[kind]
    with pytest.raises(ValueError):platform.freeze(source,tmp_path/'copy',**params)


def test_snapshot_rejects_hardlink(platform,tmp_path):
    source=tmp_path/'input';source.write_bytes(b'data')
    link=tmp_path/'linked'
    try:link.hardlink_to(source)
    except OSError:pytest.skip('Host filesystem cannot make hardlinks')
    with pytest.raises(ValueError,match='non-hardlinked'):platform.regular(source)


def test_snapshot_rejects_parent_symlink(platform,tmp_path):
    actual=tmp_path/'actual';actual.mkdir();(actual/'input').write_bytes(b'data')
    link=tmp_path/'linked'
    try:link.symlink_to(actual,target_is_directory=True)
    except OSError:pytest.skip('Host lacks symlink privilege')
    with pytest.raises(ValueError,match='symlink'):platform.regular(link/'input')


def test_png_keeps_exact_native_rgba_with_valid_crcs(platform):
    raw=bytes((10,20,30,255))*(platform.WIDTH*platform.HEIGHT)
    encoded=platform.rgba_png(raw);assert encoded[:8]==b'\x89PNG\r\n\x1a\n'
    offset=8;compressed=b''
    while offset<len(encoded):
        length=struct.unpack_from('>I',encoded,offset)[0];kind=encoded[offset+4:offset+8]
        data=encoded[offset+8:offset+8+length]
        assert zlib.crc32(kind+data)&0xffffffff==struct.unpack_from('>I',encoded,offset+8+length)[0]
        if kind==b'IDAT':compressed+=data
        offset+=12+length
    scan=zlib.decompress(compressed);stride=platform.WIDTH*4
    assert b''.join(scan[y*(stride+1)+1:(y+1)*(stride+1)] for y in range(platform.HEIGHT))==raw


def test_png_rejects_partial_native_frame(platform):
    with pytest.raises(ValueError):platform.rgba_png(b'\x00'*100)


def test_hook_directory_has_no_overlap_and_real_targets(platform):
    ranges=[]
    for address,original,name,semantic in platform.HOOKS:
        assert address%4==0 and len(bytes.fromhex(original))==8 and name.startswith('lab_') and semantic
        assert 0<=address-0xa0000000<platform.RTOS_SIZE-8
        ranges.append((address,address+8))
    ordered=sorted(ranges)
    assert len(ordered)==20 and all(a[1]<=b[0] for a,b in zip(ordered,ordered[1:]))


def test_owned_snapshot_changes_only_reviewed_veneers(platform,tmp_path,monkeypatch):
    size=max(address-0xa0000000 for address,*_ in platform.HOOKS)+8
    data=bytearray(size)
    for address,original,*_ in platform.HOOKS:data[address-0xa0000000:address-0xa0000000+8]=bytes.fromhex(original)
    monkeypatch.setattr(platform,'RTOS_SIZE',len(data));monkeypatch.setattr(platform,'RTOS_SHA256',hashlib.sha256(data).hexdigest())
    source=tmp_path/'source';source.write_bytes(data)
    symbols={name:hex(0xa9000100+i*4) for i,(_,_,name,_) in enumerate(platform.HOOKS)}
    output=tmp_path/'snapshot';patches=platform.patched_snapshot(source,output,symbols)
    rebuilt=bytearray(output.read_bytes())
    for item in patches:
        offset=int(item['address'],16)-0xa0000000
        assert rebuilt[offset:offset+8]==bytes.fromhex(item['replacement_bytes'])
        rebuilt[offset:offset+8]=bytes.fromhex(item['original_bytes'])
    assert rebuilt==data and source.read_bytes()==data


def test_snapshot_rejects_veneer_outside_platform(platform,tmp_path,monkeypatch):
    data=bytes(16);monkeypatch.setattr(platform,'RTOS_SIZE',16);monkeypatch.setattr(platform,'RTOS_SHA256',hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(platform,'HOOKS',((0xa0000000,'00'*8,'target','test'),))
    source=tmp_path/'source';source.write_bytes(data)
    with pytest.raises(ValueError,match='bridge target'):platform.patched_snapshot(source,tmp_path/'copy',{'target':'0xa9300000'})


def test_qmp_constructor_closes_owned_socket_on_connect_failure(platform,monkeypatch):
    import run_ui
    closed=[]
    class Broken:
        def settimeout(self,_):pass
        def connect(self,_):raise OSError('test failed connection')
        def close(self):closed.append(True)
    # AF_UNIX is absent in some Windows Python builds; this is a socket mock.
    monkeypatch.setattr(socket,'AF_UNIX',1,raising=False)
    monkeypatch.setattr(socket,'socket',lambda *_:Broken())
    with pytest.raises(OSError):run_ui.QMP('/owned/test/socket')
    assert closed==[True]


@pytest.mark.parametrize('args',((True,1,1),(1,True,1),(1,1,True),(0,1,1),(5,1,1),(1,-1,1),(1,1058,1),(1,1,540)))
def test_native_pointer_rejects_invalid_before_any_qmp(platform,args):
    import run_ui
    class NoQMP:
        def call(self,*_):raise AssertionError('Invalid event touched guest')
    guest=run_ui.Guest(NoQMP(),None,None,None,threading.Event())
    with pytest.raises(ValueError):guest.submit(*args)


def test_runner_gate_does_not_report_success_after_manifest_failure(platform,tmp_path,capsys):
    import run_ui
    assert run_ui.main({'destination':str(tmp_path/'out'),'manifest':str(tmp_path/'missing')})==1
    assert not (tmp_path/'out').exists()
    output=capsys.readouterr().out
    assert '"event": "error"' in output and '"event": "stopped"' in output
