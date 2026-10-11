"""Regression coverage for repairing a partial Windows QEMU installation."""
import os
from pathlib import Path

import pytest

from devkit.core import Task, read_json
from devkit.runtime import QEMU_BINARIES, discover_qemu, setup_qemu


@pytest.mark.skipif(os.name != 'nt', reason='Windows uses the portable pinned QEMU bundle')
def test_setup_repairs_a_marked_complete_but_wrong_architecture_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    extractor = runtime / '7zip'; extractor.mkdir(parents=True)
    (extractor / '7z.exe').touch(); (extractor / '.complete').touch()
    partial = runtime / 'qemu'; partial.mkdir()
    (partial / 'qemu-system-aarch64.exe').touch()
    (partial / '.complete').touch()
    (partial / 'keep.txt').write_text('previous installation', encoding='utf-8')
    monkeypatch.setattr('devkit.runtime.shutil.which', lambda _: None)
    monkeypatch.setattr('devkit.runtime.download', lambda *args: tmp_path / 'qemu-installer.exe')
    extracted = []

    class ExtractTask(Task):
        def run(self, args, **kwargs):
            assert Path(args[0]) == extractor / '7z.exe'
            output = Path(next(str(arg)[2:] for arg in args if str(arg).startswith('-o')))
            extracted.append(output)
            for binary in QEMU_BINARIES:
                (output / (binary + '.exe')).touch()
            return ''

    config = setup_qemu(tmp_path, ExtractTask(lambda _: None), required='qemu-system-x86_64')
    assert len(extracted) == 1
    assert config['qemu'] == str(partial.resolve())
    assert (partial / 'qemu-system-x86_64.exe').is_file()
    assert discover_qemu(tmp_path, QEMU_BINARIES) == config
    assert read_json(tmp_path / 'qemu.json') == config
    backups = list(runtime.glob('qemu-incomplete-*'))
    assert len(backups) == 1
    assert (backups[0] / 'keep.txt').read_text(encoding='utf-8') == 'previous installation'
