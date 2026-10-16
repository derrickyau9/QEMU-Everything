"""Owned snapshot, strict profile and terminal cleanup gates for Ricoh UI."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

BOARD=Path(__file__).resolve().parents[1]/'scripts/boards/ricoh'


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(BOARD))
    for name in ('build_ui','resource_boot','rtos_probe'):
        monkeypatch.delitem(sys.modules,name,raising=False)
    if not hasattr(os,'O_NOFOLLOW'):monkeypatch.setattr(os,'O_NOFOLLOW',0,raising=False)
    spec=importlib.util.spec_from_file_location('ricoh_runner_test',BOARD/'run_ui.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_snapshot_is_independent_and_hash_checked(runner,tmp_path):
    source=tmp_path/'source';source.write_bytes(b'authored firmware')
    copy=tmp_path/'copy';runner.freeze(source,copy,hashlib.sha256(source.read_bytes()).hexdigest(),100)
    source.write_bytes(b'changed')
    assert copy.read_bytes()==b'authored firmware'


@pytest.mark.parametrize('failure',['hash','limit','hardlink'])
def test_snapshot_rejects_changed_or_shared_inputs(runner,tmp_path,failure):
    source=tmp_path/'source';source.write_bytes(b'1234')
    if failure=='hardlink':(tmp_path/'other').hardlink_to(source)
    expected='0'*64 if failure=='hash' else hashlib.sha256(b'1234').hexdigest()
    with pytest.raises(ValueError):runner.freeze(source,tmp_path/'copy',expected,3 if failure=='limit' else 4)


@pytest.mark.parametrize('field,value',[('mode','unknown'),('seconds',True),('seconds',89),('seconds',181),('port',8794),('session_seconds',0)])
def test_invalid_controls_rejected_before_manifest(runner,field,value):
    cfg={'mode':'probe','seconds':120,'port':8792};cfg[field]=value
    with pytest.raises(ValueError):runner.validate(cfg)


def test_native_transition_gate_rejects_same_page(runner,tmp_path,monkeypatch):
    monkeypatch.delitem(sys.modules,'component_control',raising=False)
    import component_control
    class Control:
        output=tmp_path;frame=b'authored placeholder'
        def capture(self):return {'raw_sha256':'initial','native_current_id':'same'}
        def key(self,key):return {'raw_sha256':'down' if key=='down' else 'initial','native_current_id':'same'},{'key':key}
    result=component_control.roundtrip(Control())
    assert result['down_changes_pixels'] and result['up_restores_initial_pixels']
    assert not result['confirm_changes_page'] and not result['verified']


def test_closed_guest_export_preserves_exact_bytes_and_empty_logs(runner,tmp_path):
    source=tmp_path/'native';source.mkdir();(source/'frame.png').write_bytes(b'authored pixels')
    (source/'stderr.log').write_bytes(b'');(source/'nested').mkdir()
    (source/'nested'/'trace.log').write_bytes(b'closed trace')
    value=runner.export_guest(source,tmp_path/'exported',{'process_reaped':True})
    assert value['status']=='exported' and value['files']==3
    for artifact in value['artifacts']:
        copied=tmp_path/'exported'/artifact['path']
        assert copied.read_bytes()==(source/artifact['path']).read_bytes()
        assert hashlib.sha256(copied.read_bytes()).hexdigest()==artifact['sha256']


def test_active_guest_export_is_withheld(runner,tmp_path):
    source=tmp_path/'native';source.mkdir();(source/'serial.log').write_bytes(b'active')
    destination=tmp_path/'exported'
    assert runner.export_guest(source,destination,{'process_reaped':False})['status']=='withheld'
    assert not destination.exists()


def test_exported_frame_references_remain_usable_after_native_workspace_is_deleted(runner,tmp_path):
    source=tmp_path/'native';source.mkdir();pixels=b'authored snapshot'
    (source/'roundtrip-confirm.png').write_bytes(pixels)
    runtime=str(source/'live-component/original-canvas.png')
    record=dict(process_reaped=True,original_native_ui_roundtrip=dict(confirm=dict(
                frame_path=runtime,png_sha256=hashlib.sha256(pixels).hexdigest())))
    (tmp_path/'out').mkdir()
    record['guest_evidence_export']=runner.export_guest(source,tmp_path/'out'/'guest',record)
    runner.expose_exported_frames(record)
    (source/'roundtrip-confirm.png').unlink();source.rmdir()
    frame=record['original_native_ui_roundtrip']['confirm']
    assert frame['runtime_frame_path']==runtime
    assert (tmp_path/'out'/frame['frame_path']).read_bytes()==pixels
    assert record['exported_frames']['confirm']['png_sha256']==hashlib.sha256(pixels).hexdigest()


def test_exported_frame_rejects_capture_hash_mismatch(runner):
    record=dict(guest_evidence_export=dict(status='exported',artifacts=[dict(path='roundtrip-confirm.png',sha256='actual',size=10)]),
                original_native_ui_roundtrip=dict(confirm=dict(png_sha256='expected')))
    with pytest.raises(ValueError,match='frame hash'):runner.expose_exported_frames(record)


def test_guest_export_rejects_hardlink_before_any_destination(runner,tmp_path):
    source=tmp_path/'native';source.mkdir();(source/'serial.log').write_bytes(b'closed')
    (tmp_path/'other').hardlink_to(source/'serial.log')
    with pytest.raises(ValueError,match='unlinked regular'):
        runner.export_guest(source,tmp_path/'exported',{'process_reaped':True})
    assert not (tmp_path/'exported').exists()


@pytest.mark.parametrize('error',[None,'guest failed before UI'])
def test_main_uses_native_temporary_guest_and_exports_after_reap(runner,tmp_path,monkeypatch,error):
    monkeypatch.setattr(runner,'validate',lambda cfg:dict(qemu_build=dict(binary='authored',binary_sha256='x'),
                          bridge='x',bridge_sha256='x',backend='x',backend_sha256='x',platform_metadata={}))
    def freeze(source,target,*a,**k):target.write_bytes(b'authored');return target
    original_freeze=runner.freeze
    def inputs_only(source,target,*a,**k):
        if str(source)=='authored' or str(source)=='x':return freeze(source,target,*a,**k)
        return original_freeze(source,target,*a,**k)
    monkeypatch.setattr(runner,'freeze',inputs_only)
    monkeypatch.setattr(runner.signal,'signal',lambda *a:None)
    class NoThread:
        def __init__(self,*a,**k):pass
        def start(self):pass
    monkeypatch.setattr(runner.threading,'Thread',NoThread)
    observed={}
    def probe(payload,manifest,guest,*a,**k):
        assert guest.parent==payload.parent and guest.parent!=tmp_path/'out'
        guest.mkdir();(guest/'serial.log').write_bytes(b'exact closed native trace')
        observed['guest']=guest;record=dict(process_reaped=True,original_ui_verified=not error,
                       original_native_ui_roundtrip=dict(verified=not error))
        if error:record['error']=error
        return record
    monkeypatch.setattr(runner,'probe',probe)
    assert runner.main(dict(destination=str(tmp_path/'out'),mode='probe',payload='x',seconds=120))==(1 if error else 0)
    evidence=json.loads((tmp_path/'out'/'evidence.json').read_text())
    assert evidence['owned_guest_processes_reaped'] is True
    assert evidence['guest_evidence_export']['status']=='exported'
    assert (tmp_path/'out'/'guest'/'serial.log').read_bytes()==b'exact closed native trace'
    assert not observed['guest'].exists()
    if error:assert 'runner_error_traceback' in evidence


def test_open_stdin_owner_does_not_crash_interpreter_at_successful_exit(tmp_path):
    # The first real public probe reached the menu and reaped QEMU, then
    # Python aborted because a daemon held BufferedReader's stdin lock.
    # Keep stdin open while the child exits, precisely that regression.
    code='''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('ricoh_runner',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.validate=lambda cfg:dict(qemu_build=dict(binary='authored',binary_sha256='x'),bridge='x',bridge_sha256='x',backend='x',backend_sha256='x',platform_metadata={})
def freeze(source,target,*a,**k):target.write_bytes(b'authored');return target
m.freeze=freeze
m.probe=lambda *a,**k:dict(process_reaped=True,original_ui_verified=True,original_native_ui_roundtrip=dict(verified=True))
raise SystemExit(m.main(dict(destination=sys.argv[2],mode='probe',payload='x',seconds=120)))
'''
    process=subprocess.Popen([sys.executable,'-I','-c',code,str(BOARD/'run_ui.py'),str(tmp_path/'out')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert process.wait(timeout=8)==0,process.stderr.read().decode(errors='replace')
        events=[json.loads(r) for r in process.stdout.read().splitlines()]
        assert events[-1]=={'event':'stopped','owned_guest_processes_reaped':True}
        assert events[0]['event']=='result'
    finally:
        if process.poll() is None:process.kill();process.wait()
        for stream in (process.stdin,process.stdout,process.stderr):stream.close()


@pytest.mark.parametrize('key', ['display', 'adj', 'native_adj', 'native_half_press',
                               'native_half_release', 'mode_0', 'native_fn'])
def test_native_shortcuts_reject_wrong_page_before_guest_access(runner, monkeypatch, key):
    monkeypatch.delitem(sys.modules, 'component_control', raising=False)
    import component_control
    control = object.__new__(component_control.ComponentControl)
    control.last = {'native_current_id': '0x400000f'}
    with pytest.raises(ValueError):
        control.key(key)
