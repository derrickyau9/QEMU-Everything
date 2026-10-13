"""Select explicitly configured original camera UI laboratories by exact firmware.

Nikon labs execute original menus through native GDB calls. X3 executes the
original emWin GUI component with an explicitly disclosed platform shim.
Canon 70D boots a checked ROM set in its custom hardware emulator.
GoPro HERO8 executes the original Tara UI with a disclosed platform shim.
Ricoh GR IV executes its original menu controller with a software canvas backend.
The selector does not advertise photography or complete hardware support.
"""
from pathlib import Path
from . import nikon_menus


def available_lab(device, home):
    selected = nikon_menus.available_lab(device, home)
    if selected:
        return selected
    # Import the optional profile only when its explicit local config exists.
    # This keeps ordinary inspection independent of a configured WSL build.
    if (Path(home) / 'insta360-ui-lab.json').is_file():
        from . import insta360_ui
        if insta360_ui.lab_available(device, home):
            return {'id':'insta360-x3', 'label':'Insta360 X3 v1.1.4.1 emWin UI component',
                    'port':insta360_ui.PORT, 'status_path':'/status', 'frame_path':'/frame.png',
                    'assisted_startup':True}
    if (Path(home) / 'canon-ui-lab.json').is_file():
        from . import canon_ui
        if canon_ui.lab_available(device, home):
            return {'id':'canon-70d', 'label':'Canon EOS 70D 1.1.2 original ROM menu',
                    'port':canon_ui.PORT, 'status_path':'/status', 'frame_path':'/frame.png',
                    'assisted_startup':False}
    if (Path(home) / 'gopro-ui-lab.json').is_file():
        from . import gopro_ui
        if gopro_ui.lab_available(device, home):
            return {'id':'gopro-hero8', 'label':'GoPro HERO8 Black 2.51 original Tara UI component',
                    'port':gopro_ui.PORT, 'status_path':'/status', 'frame_path':'/frame.png',
                    'assisted_startup':True}
    if (Path(home) / 'ricoh-ui-lab.json').is_file():
        from . import ricoh_ui
        if ricoh_ui.lab_available(device, home):
            return {'id':'ricoh-griv', 'label':'Ricoh GR IV 1.11 original TopMenu UI component',
                    'port':ricoh_ui.PORT, 'status_path':'/status', 'frame_path':'/frame.png',
                    'assisted_startup':True}
    return None


def run_lab(device, home, task=None, *, on_ready=None):
    selected = available_lab(device, home)
    if not selected:
        raise ValueError('No configured original camera UI lab matches this firmware')
    if selected['id'] == 'insta360-x3':
        from .insta360_ui import run_lab as launch
    elif selected['id'] == 'canon-70d':
        from .canon_ui import run_lab as launch
    elif selected['id'] == 'gopro-hero8':
        from .gopro_ui import run_lab as launch
    elif selected['id'] == 'ricoh-griv':
        from .ricoh_ui import run_lab as launch
    else:
        launch = nikon_menus.run_lab
    return launch(device, home, task, on_ready=on_ready)
