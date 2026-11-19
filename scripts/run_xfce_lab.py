"""Open the workbench's real original-GUI / XFCE userspace lab."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from devkit.application import Window
from devkit.core import data_home, read_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path)
    parser.add_argument('--device',type=Path,help='open an isolated lab device without modifying the imported library')
    parser.add_argument('--open-xfce',action='store_true',help='show the original GUI first, then open XFCE automatically')
    args=parser.parse_args()
    app=QApplication(sys.argv[:1]);app.setApplicationName('QEMU Everything')
    window=Window((args.home or data_home()).resolve())
    cameras=[p.parent for p in window.devices if read_json(p).get('model')=='x2dii']
    if args.device:
        selected=args.device.resolve()
        profile=read_json(selected/'device.json')
        if profile.get('model') != 'x2dii': raise ValueError('Expected an isolated X2D II device')
        if selected/'device.json' not in window.devices:
            window.devices.append(selected/'device.json')
            window.library.addItem(f"{profile['name']} — CPU/RAM lab\n{profile['version']}")
        window.library.setCurrentRow(window.devices.index(selected/'device.json'))
        window.library_count.setText(f'{len(window.devices):02d}')
        window.empty_library.hide();window.library.show();window.details.show()
        if (selected/'memory-profile.json').exists():
            window.setWindowTitle('QEMU Everything — X2D II CPU / RAM model')
        if (selected/'subsystem-runtime.json').exists():
            window.setWindowTitle('QEMU Everything — X2D II in-camera subsystem')
    else:
        if not cameras: raise RuntimeError('Import an X2D II firmware into the workbench first')
        window.refresh_library(select=cameras[0])
    window.show()
    def begin():
        window.launch_desktop()
        if args.open_xfce and window.session:
            opened=[False]
            def phase(value):
                if value=='running' and not opened[0]:
                    opened[0]=True
                    QTimer.singleShot(5000,window.session.switch_desktop)
            window.session.phase.connect(phase)
    QTimer.singleShot(500,begin)
    return app.exec()


if __name__=='__main__':raise SystemExit(main())
