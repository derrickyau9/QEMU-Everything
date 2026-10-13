"""Dispatch exact Z7/Z30/Z6/Z50 assisted menus without cross-model substitution."""
from . import nikon_lab, nikon_z30_lab, nikon_z6_lab, nikon_z50_lab

LABS = (
    ('z7', 'Nikon Z7 C3.80', nikon_lab, '/api/status', '/api/frame'),
    ('z30', 'Nikon Z30 C1.20', nikon_z30_lab, '/status', '/frame.png'),
    ('z6', 'Nikon Z6 C3.80', nikon_z6_lab, '/status', '/frame.png'),
    ('z50', 'Nikon Z50 C2.60', nikon_z50_lab, '/status', '/frame.png'),
)


def available_lab(device, home):
    for key, label, module, status_path, frame_path in LABS:
        if module.lab_available(device, home):
            return {'id':key, 'label':label, 'port':module.PORT,
                    'status_path':status_path, 'frame_path':frame_path}
    return None


def run_lab(device, home, task=None, *, on_ready=None):
    lab = available_lab(device, home)
    if not lab: raise ValueError('No configured Nikon menu lab matches this firmware')
    module = next(row[2] for row in LABS if row[0] == lab['id'])
    return module.run_lab(device, home, task, on_ready=on_ready)
