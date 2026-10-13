"""Reviewed, offline camera container formats.

These readers do not execute firmware or infer successful hardware emulation.
Third-party research is credited in the camera catalogue and license notices.
"""
from importlib import import_module
from pathlib import Path

from ..core import Task

MODULES = ('ricoh', 'sony', 'insta360', 'canon', 'nikon', 'sigma')


def readers():
    for name in MODULES:
        yield import_module(f'{__name__}.{name}')


def identify_camera(source: Path, task: Task | None = None) -> dict | None:
    task = task or Task()
    for reader in readers():
        task.check()
        result = reader.identify(Path(source), task)
        if result is not None:
            return result
    return None


def reader_for(identity: dict):
    for reader in readers():
        if identity['id'] in reader.FORMAT_IDS:
            return reader
    raise ValueError('No reviewed extractor for this camera format')
