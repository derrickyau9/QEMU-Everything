"""Installed apps must find their runners independently of the working directory."""
import pytest

from devkit import board_assets


@pytest.mark.parametrize('layout', ('source', 'target', 'prefix', 'user'))
def test_board_assets_in_supported_install_layouts(tmp_path, monkeypatch, layout):
    package = tmp_path / 'site-packages'
    prefix = tmp_path / 'venv'
    user_data = tmp_path / 'user-data'
    monkeypatch.setattr(board_assets, '__file__', str(package / 'devkit/board_assets.py'))
    monkeypatch.setattr(board_assets.sys, 'prefix', str(prefix))
    monkeypatch.setattr(board_assets.sysconfig, 'get_path', lambda *args, **kwargs: str(user_data))
    locations = {
        'source': package / 'scripts/boards/sony',
        'target': package / 'share/qemu-everything/boards/sony',
        'prefix': prefix / 'share/qemu-everything/boards/sony',
        'user': user_data / 'share/qemu-everything/boards/sony',
    }
    expected = locations[layout]
    expected.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert board_assets.board_directory('sony') == expected
    with pytest.raises(FileNotFoundError, match='reinstall'):
        board_assets.board_directory('nikon-z30')
    with pytest.raises(ValueError, match='Unknown'):
        board_assets.board_directory('../sony')
