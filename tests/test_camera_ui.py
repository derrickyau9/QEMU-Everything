"""Exact original UI routing and Windows package source completeness."""
from pathlib import Path
import pytest
from devkit import camera_ui,insta360_ui,canon_ui,ricoh_ui
from devkit.cli import main


def test_unknown_camera_does_not_launch_other_model(tmp_path,monkeypatch):
    monkeypatch.setattr(camera_ui.nikon_menus,'available_lab',lambda *a:None)
    monkeypatch.setattr(insta360_ui,'run_lab',lambda *a,**k:pytest.fail('Unknown camera must not run'))
    assert camera_ui.available_lab(tmp_path/'unknown',tmp_path) is None
    with pytest.raises(ValueError,match='matches'):camera_ui.run_lab(tmp_path/'unknown',tmp_path)


def test_nikon_selector_keeps_existing_route_and_urls(tmp_path,monkeypatch):
    selected={'id':'z50','label':'Nikon Z50 C2.60','port':8790,'status_path':'/status','frame_path':'/frame.png'}
    monkeypatch.setattr(camera_ui.nikon_menus,'available_lab',lambda *a:selected)
    monkeypatch.setattr(insta360_ui,'lab_available',lambda *a:pytest.fail('Selected Nikon must not fall through'))
    calls=[];marker=object()
    monkeypatch.setattr(camera_ui.nikon_menus,'run_lab',lambda *a,**k:calls.append((a,k)) or marker)
    assert camera_ui.available_lab(tmp_path,tmp_path)==selected
    callback=lambda *a:None
    assert camera_ui.run_lab(tmp_path,tmp_path,on_ready=callback) is marker
    assert calls[0][1]=={'on_ready':callback}


def test_x3_route_requires_explicit_config_and_exact_profile_eligibility(tmp_path,monkeypatch):
    monkeypatch.setattr(camera_ui.nikon_menus,'available_lab',lambda *a:None)
    (tmp_path/'insta360-ui-lab.json').write_text('{}')
    monkeypatch.setattr(insta360_ui,'lab_available',lambda device,home:device.name=='known')
    assert camera_ui.available_lab(tmp_path/'unknown',tmp_path) is None
    selected=camera_ui.available_lab(tmp_path/'known',tmp_path)
    assert selected['id']=='insta360-x3' and selected['port']==8791
    assert 'component' in selected['label']
    calls=[];marker=object()
    monkeypatch.setattr(insta360_ui,'run_lab',lambda *a,**k:calls.append((a,k)) or marker)
    assert camera_ui.run_lab(tmp_path/'known',tmp_path) is marker
    assert calls[0][0][:2]==(tmp_path/'known',tmp_path)


def test_canon_rom_route_requires_config_and_does_not_accept_fir(tmp_path,monkeypatch):
    monkeypatch.setattr(camera_ui.nikon_menus,'available_lab',lambda *a:None)
    monkeypatch.setattr(canon_ui,'lab_available',lambda device,home:device.name=='rom-set')
    assert camera_ui.available_lab(tmp_path/'rom-set',tmp_path) is None
    (tmp_path/'canon-ui-lab.json').write_text('{}')
    assert camera_ui.available_lab(tmp_path/'official-fir',tmp_path) is None
    selected=camera_ui.available_lab(tmp_path/'rom-set',tmp_path)
    assert selected['id']=='canon-70d' and selected['port']==8793
    assert selected['assisted_startup'] is False
    calls=[];marker=object();callback=lambda *a:None
    monkeypatch.setattr(canon_ui,'run_lab',lambda *a,**k:calls.append((a,k)) or marker)
    assert camera_ui.run_lab(tmp_path/'rom-set',tmp_path,on_ready=callback) is marker
    assert calls[0][0][:2]==(tmp_path/'rom-set',tmp_path)
    assert calls[0][1]=={'on_ready':callback}


def test_optional_x3_config_does_not_hide_canon_rom_route(tmp_path,monkeypatch):
    monkeypatch.setattr(camera_ui.nikon_menus,'available_lab',lambda *a:None)
    (tmp_path/'insta360-ui-lab.json').write_text('{}')
    (tmp_path/'canon-ui-lab.json').write_text('{}')
    monkeypatch.setattr(insta360_ui,'lab_available',lambda *a:False)
    monkeypatch.setattr(canon_ui,'lab_available',lambda *a:True)
    assert camera_ui.available_lab(tmp_path,tmp_path)['id']=='canon-70d'


def test_ricoh_route_requires_exact_configured_profile(tmp_path,monkeypatch):
    monkeypatch.setattr(camera_ui.nikon_menus,'available_lab',lambda *a:None)
    monkeypatch.setattr(ricoh_ui,'lab_available',lambda device,home:device.name=='griv-111')
    assert camera_ui.available_lab(tmp_path/'griv-111',tmp_path) is None
    (tmp_path/'ricoh-ui-lab.json').write_text('{}')
    assert camera_ui.available_lab(tmp_path/'gr-unknown',tmp_path) is None
    selected=camera_ui.available_lab(tmp_path/'griv-111',tmp_path)
    assert selected['id']=='ricoh-griv' and selected['port']==8792
    assert selected['assisted_startup'] is True
    calls=[];marker=object();callback=lambda *a:None
    monkeypatch.setattr(ricoh_ui,'run_lab',lambda *a,**k:calls.append((a,k)) or marker)
    assert camera_ui.run_lab(tmp_path/'griv-111',tmp_path,on_ready=callback) is marker
    assert calls[0][0][:2]==(tmp_path/'griv-111',tmp_path)
    assert calls[0][1]=={'on_ready':callback}


@pytest.mark.parametrize('profile_id,assisted,label',[
    ('insta360-x3',True,'Insta360 X3 emWin UI component'),
    ('canon-70d',False,'Canon EOS 70D 1.1.2 original ROM menu'),
    ('gopro-hero8',True,'GoPro HERO8 Black 2.51 original Tara UI component'),
    ('ricoh-griv',True,'Ricoh GR IV 1.11 original TopMenu UI component'),
])
def test_gui_start_routes_original_ui_without_generic_boot(tmp_path,monkeypatch,profile_id,assisted,label):
    from PySide6.QtWidgets import QApplication
    from devkit.application import Window
    from devkit.library import import_firmware
    app=QApplication.instance() or QApplication([])
    source=tmp_path/'firmware.dat';source.write_bytes(b'authored unknown format')
    device=import_firmware(source,tmp_path/'home/library')
    selected={'id':profile_id,'label':label,'assisted_startup':assisted,'port':8791,'status_path':'/status','frame_path':'/frame.png'}
    monkeypatch.setattr(camera_ui,'available_lab',lambda *a:selected)
    window=Window(tmp_path/'home');started=[];window.launch_nikon_lab=lambda:started.append(device)
    try:
        assert window.start.isEnabled() and window.nikon_lab_button.isEnabled()
        assert 'original UI' in window.start.text() or '原厂 UI' in window.start.text()
        assert label in window.details.text()
        if not assisted:
            assert 'ROM' in window.details.text()
            assert 'assisted' not in window.screen.caption.lower()
            assert '辅助' not in window.screen.caption
        assert 'Nikon' not in window.screen.caption
        window.launch();assert started==[device] and window.session is None
    finally:window.timer.stop();window.close();app.processEvents()


@pytest.mark.parametrize('action,model',[('insta360-ui-check','x3'),('nikon-z50-check','z50'),('canon-ui-check','70d'),('ricoh-ui-check','griv')])
def test_cli_bounded_ui_probe_routes_requested_model(tmp_path,monkeypatch,capsys,action,model):
    from devkit import nikon_z50_lab
    calls=[];module={'x3':insta360_ui,'z50':nikon_z50_lab,'70d':canon_ui,'griv':ricoh_ui}[model]
    monkeypatch.setattr(module,'run_menu_probe',lambda *a,**k:calls.append((a,k)) or {'authored':True})
    assert main(['--home',str(tmp_path),action,str(tmp_path/'known'),'--seconds','25'])==0
    assert calls[0][1]=={'seconds':25} and 'authored' in capsys.readouterr().out


def test_cli_canon_import_keeps_complete_rom_set_and_destination(tmp_path,monkeypatch,capsys):
    calls=[]
    monkeypatch.setattr(canon_ui,'import_roms',lambda *a:calls.append(a) or tmp_path/'entry')
    assert main(['canon-ui-import','--rom0',str(tmp_path/'ROM0.BIN'),'--rom1',str(tmp_path/'ROM1.BIN'),
                 '--sfdata',str(tmp_path/'SFDATA.BIN'),'--output',str(tmp_path/'entry')])==0
    assert calls[0][:4]==(tmp_path/'ROM0.BIN',tmp_path/'ROM1.BIN',tmp_path/'SFDATA.BIN',tmp_path/'entry')
    assert str(tmp_path/'entry') in capsys.readouterr().out


def test_frozen_and_wheel_include_board_headers_metadata_and_x3_assembly():
    import ast,tomllib
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse((root/'scripts/build.py').read_text())
    suffixes={v.value for n in ast.walk(tree) if isinstance(n,ast.Tuple) for v in n.elts if isinstance(v,ast.Constant) and isinstance(v.value,str)}
    assert {'.h','.json','.S','.ld'}<=suffixes
    data=tomllib.loads((root/'pyproject.toml').read_text())['tool']['setuptools']['data-files']
    x3=data['share/qemu-everything/boards/insta360']
    assert 'scripts/boards/insta360/*.S' in x3 and 'scripts/boards/insta360/*.ld' in x3
    assert 'share/qemu-everything/boards/nikon-z50/source/qemu' in data
    assert 'share/qemu-everything/boards/nikon-z50/source/tools' in data
    assert 'scripts/boards/canon/*.py' in data['share/qemu-everything/boards/canon']
    assert 'scripts/boards/canon/*.json' in data['share/qemu-everything/boards/canon']
    assert 'scripts/boards/gopro/*.S' in data['share/qemu-everything/boards/gopro']
    assert 'scripts/boards/gopro/*.json' in data['share/qemu-everything/boards/gopro']
