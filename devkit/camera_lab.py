"""Select only fingerprinted, separately configured original Linux labs."""
from pathlib import Path

from . import sony_lab, ricoh_lab, insta360_lab

LABS = (
    ('sony', 'Sony A7 IV / A7R V Linux', sony_lab, ('shell', 'init')),
    ('ricoh', 'Ricoh GR Linux', ricoh_lab, ('shell', 'services', 'init')),
    ('insta360', 'Insta360 X3 Linux', insta360_lab, ('shell',)),
)


def available_lab(device: Path, home: Path):
    """Metadata eligibility only; runners verify actual bytes before launch."""
    for key, label, module, modes in LABS:
        if module.lab_available(device, home):
            if key == 'sony':
                from .sony_components import profile_for_device
                profile = profile_for_device(device)
                if profile:
                    name = {'ILCE-7M4': 'A7 IV', 'ILCE-7RM5': 'A7R V'}.get(profile['model'], profile['model'])
                    label = f"Sony {name} {profile['version']}"
            return {'id': key, 'label': label, 'modes': modes}
    return None


def run_lab(device: Path, home: Path, task=None, *, seconds=30, mode='shell'):
    lab = available_lab(device, home)
    if not lab:
        raise ValueError('No configured camera Linux lab matches this firmware / 无匹配的相机 Linux 实验配置')
    if mode not in lab['modes']:
        raise ValueError(f"{lab['label']} supports modes: {', '.join(lab['modes'])}")
    module = next(row[2] for row in LABS if row[0] == lab['id'])
    options = {'seconds': seconds}
    if lab['id'] != 'insta360': options['mode'] = mode
    return module.run_lab(device, home, task, **options)
