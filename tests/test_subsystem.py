import json
from pathlib import Path
from devkit.memory_lab import MemoryDesktopSession
from devkit.subsystem_session import SubsystemSession


def make_session(tmp_path):
    project=Path(__file__).resolve().parents[1]
    profile=json.loads((project/'research/x2dii-memory/eagle2-hb722-1.2.7.16.json').read_text())
    system=tmp_path/'system.img';system.touch()
    vendor=tmp_path/'vendor.img';vendor.touch()
    return SubsystemSession(tmp_path,{},{
        'firmware_system':str(system),'firmware_vendor':str(vendor)},profile)


def test_subsystem_has_one_display_object_in_both_states(tmp_path):
    session=make_session(tmp_path)
    camera_stream=object()
    session.frames=camera_stream
    session.desktop_frames=object()
    session.desktop_active=True
    assert session.frames is camera_stream
    session.desktop_active=False
    assert session.frames is camera_stream


def test_subsystem_requests_guest_lifecycle_without_host_process_signals(tmp_path):
    session=make_session(tmp_path)
    session.switch_desktop();session.session_step()
    command=session.commands.get_nowait()
    assert "printf 'start\\n'" in command and '/dev/qe-subsystem/control' in command
    assert 'kill' not in command and 'chroot' not in command
    session.session_step()
    assert session.commands.empty()
    session.on_guest_line('QE_SUBSYSTEM_EVENT '+json.dumps({'event':'desktop-active','state':'desktop'}))
    assert session.desktop_active
    session.switch_camera();session.session_step()
    assert "printf 'stop\\n'" in session.commands.get_nowait()
    session.on_guest_line('QE_SUBSYSTEM_EVENT '+json.dumps({'event':'camera-restored','state':'camera'}))
    assert not session.desktop_active


def test_subsystem_exposes_no_host_rfb_or_network_and_readonly_firmware(tmp_path,monkeypatch):
    session=make_session(tmp_path)
    monkeypatch.setattr(MemoryDesktopSession,'build_command',lambda *args:[
        'qemu','-netdev','user,id=xfce,hostfwd=tcp:127.0.0.1:1234-10.0.2.15:5900',
        '-device','virtio-net-device,netdev=xfce',
        '-drive','if=none,id=xfceroot,file=child.ext4,format=raw,snapshot=on',
        '-device','virtio-blk-device,drive=xfceroot'])
    command=session.build_command(1,2,3)
    assert '-netdev' not in command and 'hostfwd' not in ' '.join(command)
    assert command[command.index('-nic')+1]=='none'
    drives=[command[i+1] for i,x in enumerate(command) if x=='-drive']
    assert len(drives)==3
    assert 'id=firmwarevendor' in drives[0] and 'readonly=on' in drives[0]
    assert 'id=firmwaresystem' in drives[1] and 'readonly=on' in drives[1]
    assert 'id=xfceroot' in drives[2]
