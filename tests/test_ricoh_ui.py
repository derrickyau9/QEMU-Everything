"""Exact Ricoh profile and owned supervisor boundary tests; authored inputs only."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from devkit import ricoh_ui as lab
from devkit.core import Task, Cancelled, write_json


@pytest.fixture
def library(tmp_path,monkeypatch):
    device=tmp_path/'device';home=tmp_path/'home';device.mkdir();home.mkdir()
    payload=b'authored payload, not executable'
    monkeypatch.setattr(lab,'SOURCE_SHA256','1'*64)
    monkeypatch.setattr(lab,'PAYLOAD_SHA256',hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(lab,'PAYLOAD_SIZE',len(payload))
    write_json(device/'device.json',{'sha256':lab.SOURCE_SHA256})
    write_json(device/'extracted/extraction.json',{'source_sha256':lab.SOURCE_SHA256,
        'method':'ricoh-gr-literal-lz-frame-decoder',
        'artifacts':[{'path':'payload.bin','size':len(payload),'sha256':lab.PAYLOAD_SHA256}]})
    (device/'extracted/payload.bin').write_bytes(payload)
    write_json(home/'ricoh-ui-lab.json',{'distro':'Debian','manifest':'/reviewed/ricoh-ui.json'})
    return device,home


def test_exact_profile_command_uses_frozen_supervisor_protocol(library):
    device,home=library
    command,url,out,record=lab._build_command_locked(device,home,Task(lambda _:None),'probe',120)
    cfg=json.loads(command[-1])
    assert '-I' in command and cfg['seconds']==120 and type(cfg['seconds'])is int
    assert cfg['payload'].endswith('/extracted/payload.bin') and cfg['port']==8792
    assert record['assisted_startup'] and not record['natural_power_on_verified']


def test_same_size_changed_payload_never_launches(library,monkeypatch):
    device,home=library;p=device/'extracted/payload.bin';data=p.read_bytes()
    p.write_bytes(data[:-1]+bytes([data[-1]^1]))
    monkeypatch.setattr(lab.subprocess,'Popen',lambda *a,**k:pytest.fail('Changed payload launched'))
    with pytest.raises(ValueError,match='changed'):lab.run_menu_probe(device,home,Task(lambda _:None))


@pytest.mark.parametrize('fault',['source','method','artifact','duplicate','escape'])
def test_eligibility_rejects_wrong_or_escaping_extraction(library,fault):
    device,home=library;p=device/'extracted/extraction.json';r=json.loads(p.read_text())
    if fault=='source':r['source_sha256']='0'*64
    if fault=='method':r['method']='unknown'
    if fault=='artifact':r['artifacts'][0]['sha256']='0'*64
    if fault=='duplicate':r['artifacts']*=2
    if fault=='escape':r['artifacts'][0]['path']='../../outside'
    write_json(p,r)
    assert lab.lab_available(device,home) is False


def test_hardlinked_firmware_is_rejected(library,tmp_path):
    device,home=library;link=tmp_path/'hardlink'
    link.hardlink_to(device/'extracted/payload.bin')
    with pytest.raises(ValueError,match='hardlinked'):lab._inputs(device)


def test_windows_junction_attribute_rejected_before_use(tmp_path,monkeypatch):
    original=Path.lstat
    def metadata(self):
        r=original(self)
        if self==tmp_path:return SimpleNamespace(st_mode=r.st_mode,st_file_attributes=0x400,st_nlink=r.st_nlink)
        return r
    monkeypatch.setattr(Path,'lstat',metadata)
    with pytest.raises(ValueError,match='junction'):lab._path(tmp_path,'input')


@pytest.mark.parametrize('field,value',[('distro','Debian;echo'),('manifest','/tmp/../other'),('manifest','relative')])
def test_configuration_cannot_select_shell_or_traversal(library,field,value):
    device,home=library;r={'distro':'Debian','manifest':'/reviewed/manifest'};r[field]=value
    write_json(home/'ricoh-ui-lab.json',r)
    with pytest.raises(ValueError):lab._config(home)


@pytest.mark.parametrize('failure',['construct','start'])
def test_reader_start_failure_still_reaps_owned_process(library,monkeypatch,failure):
    device,home=library;seen=[]
    class Input(io.BytesIO):
        def write(self,data):seen.append(data);return super().write(data)
    class Process:
        stdin=Input();stdout=io.BytesIO();returncode=None
        def poll(self):return self.returncode
        def wait(self,**kwargs):self.returncode=0;return 0
    process=Process();monkeypatch.setattr(lab.subprocess,'Popen',lambda *a,**k:process)
    class Reader:
        def __init__(self,*a,**k):
            if failure=='construct':raise RuntimeError('authored reader failure')
        def start(self):raise RuntimeError('authored reader failure')
    monkeypatch.setattr(lab.threading,'Thread',Reader)
    with pytest.raises(RuntimeError,match='reader failure'):lab.run_menu_probe(device,home,Task(lambda _:None))
    assert b'stop\n' in seen and process.poll()==0 and process.stdout.closed
    r=json.loads((device/'ricoh-ui/session.json').read_text())
    assert r['process_reaped'] and not r['owned_guest_processes_reaped'] and r['status']=='cleanup-incomplete'


def test_saved_cleanup_failure_cannot_be_reported_as_success(library,monkeypatch,tmp_path):
    device,home=library;out=tmp_path/'output';out.mkdir()
    write_json(out/'evidence.json',{'native_input_verified':True,'original_ui_verified':True,
        'single_instance_menu_roundtrip_verified':True,'owned_guest_processes_reaped':False})
    monkeypatch.setattr(lab,'_build_command_locked',lambda *a: (['authored'],None,out,{}))
    class Process:
        stdin=io.BytesIO();stdout=io.BytesIO(b'{"event":"result","native_input_verified":true}\n{"event":"stopped"}\n');returncode=0
        def poll(self):return 0
    monkeypatch.setattr(lab.subprocess,'Popen',lambda *a,**k:Process())
    with pytest.raises(RuntimeError,match='cleanup'):lab.run_menu_probe(device,home,Task(lambda _:None))


def test_cancel_before_hash_does_not_start_transport(library,monkeypatch):
    device,home=library;task=Task(lambda _:None);task.cancelled.set()
    monkeypatch.setattr(lab.subprocess,'Popen',lambda *a,**k:pytest.fail('Cancelled transport started'))
    with pytest.raises(Cancelled):lab.run_menu_probe(device,home,task)


@pytest.mark.parametrize('seconds',[True,89,181,float('nan'),float('inf')])
def test_budget_must_be_finite_bounded(library,seconds):
    with pytest.raises(ValueError,match='90..180'):lab.run_menu_probe(*library,seconds=seconds)


@pytest.fixture
def authored_import(tmp_path,monkeypatch):
    from devkit import camera_research
    source=tmp_path/'official-input.bin';source.write_bytes(b'authored update container')
    payload=b'authored decoded payload'
    monkeypatch.setattr(lab,'SOURCE_SHA256',hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(lab,'PAYLOAD_SHA256',hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(lab,'PAYLOAD_SIZE',len(payload))
    def decode(original,target,task):
        assert original.read_bytes()==source.read_bytes()
        target.mkdir();(target/'payload.bin').write_bytes(payload)
        write_json(target/'extraction.json',dict(source_sha256=lab.SOURCE_SHA256,
            method='ricoh-gr-literal-lz-frame-decoder',artifacts=[dict(path='payload.bin',size=len(payload),sha256=lab.PAYLOAD_SHA256,format='unknown',architecture=None)]))
    monkeypatch.setattr(camera_research,'unpack_firmware',decode)
    return source,dict(sha256=lab.SOURCE_SHA256,format='ricoh-gr-update',camera={'id':'ricoh-gr','brand':'Ricoh','model':'GR IV'}),payload


def test_transactional_import_publishes_only_checked_original_and_payload(tmp_path,authored_import):
    source,inspection,payload=authored_import;out=tmp_path/'entry'
    assert lab.import_update(source,out,Task(lambda _:None),inspection=inspection)==out
    assert (out/'source/firmware.bin').read_bytes()==source.read_bytes()
    assert (out/'extracted/payload.bin').read_bytes()==payload
    assert json.loads((out/'device.json').read_text())['original_ui_verified'] is False
    assert not list(tmp_path.glob('ricoh-ui-import-*'))


def test_decode_failure_rolls_back_partial_stage(tmp_path,authored_import,monkeypatch):
    from devkit import camera_research
    source,inspection,_=authored_import
    def fail(source,target,task):target.mkdir();(target/'partial').write_bytes(b'partial');raise ValueError('authored decode failure')
    monkeypatch.setattr(camera_research,'unpack_firmware',fail)
    with pytest.raises(ValueError,match='decode failure'):lab.import_update(source,tmp_path/'entry',inspection=inspection)
    assert not (tmp_path/'entry').exists() and not list(tmp_path.glob('ricoh-ui-import-*'))


def test_stale_inspection_cannot_publish_import(tmp_path,authored_import):
    source,inspection,_=authored_import;inspection['sha256']='0'*64
    with pytest.raises(ValueError,match='after inspection'):lab.import_update(source,tmp_path/'entry',inspection=inspection)
    assert not (tmp_path/'entry').exists()
