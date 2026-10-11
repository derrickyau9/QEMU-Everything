"""Automation entry point; GUI launchers remain separate."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
from .core import Task, GuestLock, data_home, read_json, write_json

def main(argv=None):
    if os.name == 'nt':
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'): stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(prog='qemu-everything', description='QEMU Everything / 通用固件开发环境')
    parser.add_argument('--home', type=Path, default=None)
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('inspect', 'import'):
        command = commands.add_parser(name); command.add_argument('file', type=Path)
    commands.add_parser('boards')
    commands.add_parser('cameras', help='Camera extraction and emulation research / 相机解包与模拟研究矩阵')
    command = commands.add_parser('unpack', help='Offline camera extraction / 离线相机解包')
    command.add_argument('file', type=Path)
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--key-file', type=Path, help='Local Nikon XOR-table JSON / 本地尼康 XOR 表 JSON')
    command = commands.add_parser('unpack-device', help='Extract an imported camera entry / 解包已导入相机条目')
    command.add_argument('device', type=Path)
    command.add_argument('--key-file', type=Path)
    command = commands.add_parser('nikon-lab', help='Nikon Z7 C3.80 assisted menu lab; Ctrl+C to stop / 尼康辅助菜单实验，Ctrl+C 停止')
    command.add_argument('device', type=Path)
    command = commands.add_parser('probe-boot', help='Bounded generic-board kernel experiment / 限时内核启动试验')
    command.add_argument('device', type=Path)
    command.add_argument('--kernel', required=True, help='Relative kernel path in this device workspace')
    from .boot import BOARDS
    command.add_argument('--board', required=True, choices=BOARDS)
    command.add_argument('--dtb', default='', help='Optional relative DTB path; vendor DTBs may not match the selected board')
    command.add_argument('--seconds', type=int, default=10)
    command = commands.add_parser('support'); command.add_argument('device', type=Path)
    command.add_argument('--export', type=Path)
    command = commands.add_parser('run'); command.add_argument('device', type=Path)
    command.add_argument('--headless', action='store_true')
    command = commands.add_parser('setup'); command.add_argument('--camera', action='store_true')
    command.add_argument('--accept-android-sdk-license', action='store_true',
        help='Accept https://developer.android.com/studio/terms for a new camera runtime download')
    args = parser.parse_args(argv)
    home = (args.home or data_home()).resolve()
    task = Task(lambda text: print(text, file=sys.stderr))
    try:
        if args.action == 'inspect':
            from .inspection import inspect_firmware
            print(json.dumps(inspect_firmware(args.file, task), ensure_ascii=False, indent=2))
        elif args.action == 'import':
            from .library import import_firmware
            print(import_firmware(args.file, home / 'library', task))
        elif args.action == 'boards':
            from .boot import BOARDS
            print(json.dumps(BOARDS, ensure_ascii=False, indent=2))
        elif args.action == 'cameras':
            from .camera_catalog import camera_catalog
            print(json.dumps(camera_catalog(), ensure_ascii=False, indent=2))
        elif args.action == 'unpack':
            from .camera_research import unpack_firmware
            local_key = args.key_file or home / 'camera-keys/nikon-tables.json'
            print(unpack_firmware(args.file, args.output, task, key_file=local_key if local_key.is_file() else None))
        elif args.action == 'unpack-device':
            from .camera_research import unpack_device
            local_key = args.key_file or home / 'camera-keys/nikon-tables.json'
            print(unpack_device(args.device, task, key_file=local_key if local_key.is_file() else None))
        elif args.action == 'probe-boot':
            from .camera_probe import probe_boot
            from .runtime import discover_qemu
            runtime = discover_qemu(home, BOARDS[args.board]['binary'])
            if not runtime:
                raise ValueError('QEMU not installed: run qemu-everything setup')
            print(probe_boot(args.device, args.kernel, args.board, runtime['qemu'], task,
                             seconds=args.seconds, dtb=args.dtb))
        elif args.action == 'nikon-lab':
            from .nikon_lab import run_lab
            run_lab(args.device, home, task, on_ready=lambda url: print(url, flush=True))
        elif args.action == 'setup':
            from .runtime import setup, setup_qemu, discover
            if args.camera and not discover(home) and not args.accept_android_sdk_license:
                raise ValueError('Read https://developer.android.com/studio/terms and pass --accept-android-sdk-license, or use the GUI license dialog.')
            print(json.dumps((setup if args.camera else setup_qemu)(home, task), indent=2))
        elif args.action == 'support':
            from .support import create_support, export_support
            from .runtime import discover_qemu
            runtime = discover_qemu(home)
            qemu = runtime['qemu'] if runtime else None
            print(export_support(args.device, args.export, qemu) if args.export else create_support(args.device, qemu))
        elif args.action == 'run':
            from .library import is_camera
            from .runtime import discover_qemu
            from .boot import build_command, validate_profile, BOARDS
            if is_camera(read_json(args.device / 'device.json')):
                raise ValueError('Use Launch.cmd / Launch.sh for the Hasselblad UI adapter')
            profile = validate_profile(read_json(args.device / 'boot.json'), args.device)
            runtime = discover_qemu(home, BOARDS[profile['board']]['binary'])
            if not runtime:
                raise ValueError('QEMU not installed: run qemu-everything setup')
            with GuestLock(args.device):
                command = build_command(profile, args.device, runtime['qemu'])
                if args.headless:
                    command += ['-display', 'none']
                write_json(args.device / 'generic-command.json', command)
                process = subprocess.Popen(command, cwd=args.device)
                try:
                    return process.wait()
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=5)
                        except subprocess.TimeoutExpired: process.kill(); process.wait()
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f'ERROR: {error}', file=sys.stderr); return 1

if __name__ == '__main__':
    raise SystemExit(main())
