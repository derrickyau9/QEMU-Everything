"""Locate public board sources in a checkout, portable build or wheel install."""
from pathlib import Path
import sys
import sysconfig

FAMILIES = frozenset(('canon', 'insta360', 'nikon-z30', 'nikon-z6', 'ricoh', 'sony'))


def board_directory(family: str) -> Path:
    if family not in FAMILIES:
        raise ValueError('Unknown board family')
    package_root = Path(__file__).resolve().parents[1]
    bases = (
        package_root / 'scripts/boards',
        package_root / 'share/qemu-everything/boards',
        Path(sys.prefix) / 'share/qemu-everything/boards',
        Path(sysconfig.get_path('data', scheme=sysconfig.get_preferred_scheme('user'))) / 'share/qemu-everything/boards',
    )
    for base in bases:
        directory = base / family
        if directory.is_dir():
            return directory
    raise FileNotFoundError(f'Board assets are missing for {family}; reinstall QEMU Everything')
