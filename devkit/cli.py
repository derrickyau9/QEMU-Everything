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
    command = commands.add_parser('canon-lab', help='EOS 50D ROM/decryption/updater lab / 佳能 ROM、解密与升级程序实验')
    command.add_argument('arguments', nargs=argparse.REMAINDER)
    command = commands.add_parser('unpack', help='Offline camera extraction / 离线相机解包')
    command.add_argument('file', type=Path)
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--key-file', type=Path, help='Local Nikon XOR-table JSON / 本地尼康 XOR 表 JSON')
    command = commands.add_parser('unpack-device', help='Extract an imported camera entry / 解包已导入相机条目')
    command.add_argument('device', type=Path)
    command.add_argument('--key-file', type=Path)
    command = commands.add_parser('nikon-lab', help='Nikon Z7/Z30/Z6/Z50 assisted menu lab; Ctrl+C to stop / 尼康辅助菜单实验，Ctrl+C 停止')
    command.add_argument('device', type=Path)
    command = commands.add_parser('nikon-z30-check', help='Z30 original menu round-trip verification / Z30 原厂菜单往返验证')
    command.add_argument('device', type=Path)
    command.add_argument('--seconds', type=int, default=60)
    command = commands.add_parser('nikon-z6-check', help='Z6 original menu round-trip verification / Z6 原厂菜单往返验证')
    command.add_argument('device', type=Path)
    command.add_argument('--seconds', type=int, default=60)
    command = commands.add_parser('nikon-z50-check', help='Z50 original menu round-trip verification / Z50 原厂菜单往返验证')
    command.add_argument('device', type=Path)
    command.add_argument('--seconds', type=int, default=60)
    command = commands.add_parser('insta360-ui', help='X3 original emWin UI component lab / X3 原厂 emWin 界面组件实验')
    command.add_argument('device', type=Path)
    command = commands.add_parser('insta360-ui-check', help='Bounded X3 original UI and touch check / 限时 X3 原厂界面与触摸验证')
    command.add_argument('device', type=Path)
    command.add_argument('--seconds', type=int, default=20)
    command = commands.add_parser('canon-ui', help='EOS 70D 1.1.2 original ROM menu; Ctrl+C to stop / 佳能原厂 ROM 菜单，Ctrl+C 停止')
    command.add_argument('device', type=Path)
    command = commands.add_parser('canon-ui-import', help='Import a complete EOS 70D 1.1.2 ROM set / 导入完整佳能 ROM 组合')
    command.add_argument('--rom0', type=Path, required=True)
    command.add_argument('--rom1', type=Path, required=True)
    command.add_argument('--sfdata', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True, help='New library entry directory / 新固件库条目目录')
    command = commands.add_parser('canon-ui-check', help='Bounded EOS 70D original menu check / 限时佳能原厂菜单验证')
    command.add_argument('device', type=Path)
    command.add_argument('--seconds', type=int, default=40)
    command = commands.add_parser('gopro-ui', help='HERO8 2.51 original Tara UI component; Ctrl+C to stop / GoPro 原厂 UI 组件，Ctrl+C 停止')
    command.add_argument('device', type=Path)
    command = commands.add_parser('gopro-ui-import', help='Import official HERO8 2.51 UPDATE.zip or DATA.bin / 导入 GoPro 官方升级包')
    command.add_argument('file', type=Path)
    command.add_argument('--output', type=Path, required=True)
    command = commands.add_parser('gopro-ui-check', help='Bounded original GoPro menu and Back check / 限时 GoPro 原厂菜单与返回验证')
    command.add_argument('device', type=Path)
    command.add_argument('--seconds', type=int, default=60)
    command = commands.add_parser('ricoh-ui', help='GR IV 1.11 original TopMenu UI component; Ctrl+C to stop / 理光原厂菜单组件，Ctrl+C 停止')
    command.add_argument('device', type=Path)
    command = commands.add_parser('ricoh-ui-check', help='Bounded GR IV original menu, Confirm and Back check / 限时理光原厂菜单、确认与返回验证')
    command.add_argument('device', type=Path)
    command.add_argument('--seconds', type=int, default=120)
    command = commands.add_parser('ricoh-ui-import', help='Import and extract official GR IV 1.11 firmware / 导入并解包理光 GR IV 1.11 官方固件')
    command.add_argument('file', type=Path)
    command.add_argument('--output', type=Path, required=True)
    command = commands.add_parser('camera-lab', help='Bounded original camera Linux boot / 限时启动原厂相机 Linux')
    command.add_argument('device', type=Path)
    command.add_argument('--seconds', type=int, default=30)
    command.add_argument('--mode', choices=('shell', 'init', 'services'), default='shell')
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
        elif args.action == 'canon-lab':
            from .canon_lab import main as canon_main
            return canon_main(args.arguments) or 0
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
            from .nikon_menus import run_lab
            run_lab(args.device, home, task, on_ready=lambda url: print(url, flush=True))
        elif args.action == 'nikon-z30-check':
            from .nikon_z30_lab import run_menu_probe
            print(json.dumps(run_menu_probe(args.device, home, task, seconds=args.seconds), ensure_ascii=False, indent=2))
        elif args.action == 'nikon-z6-check':
            from .nikon_z6_lab import run_menu_probe
            print(json.dumps(run_menu_probe(args.device, home, task, seconds=args.seconds), ensure_ascii=False, indent=2))
        elif args.action == 'nikon-z50-check':
            from .nikon_z50_lab import run_menu_probe
            print(json.dumps(run_menu_probe(args.device, home, task, seconds=args.seconds), ensure_ascii=False, indent=2))
        elif args.action == 'insta360-ui':
            from .insta360_ui import run_lab
            run_lab(args.device, home, task, on_ready=lambda url: print(url, flush=True))
        elif args.action == 'insta360-ui-check':
            from .insta360_ui import run_menu_probe
            print(json.dumps(run_menu_probe(args.device, home, task, seconds=args.seconds), ensure_ascii=False, indent=2))
        elif args.action == 'canon-ui':
            from .canon_ui import run_lab
            run_lab(args.device, home, task, on_ready=lambda url: print(url, flush=True))
        elif args.action == 'canon-ui-import':
            from .canon_ui import import_roms
            print(import_roms(args.rom0, args.rom1, args.sfdata, args.output, task))
        elif args.action == 'canon-ui-check':
            from .canon_ui import run_menu_probe
            print(json.dumps(run_menu_probe(args.device, home, task, seconds=args.seconds), ensure_ascii=False, indent=2))
        elif args.action == 'gopro-ui':
            from .gopro_ui import run_lab
            run_lab(args.device, home, task, on_ready=lambda url: print(url, flush=True))
        elif args.action == 'gopro-ui-import':
            from .gopro_firmware import import_update
            print(import_update(args.file, args.output, task))
        elif args.action == 'gopro-ui-check':
            from .gopro_ui import run_menu_probe
            print(json.dumps(run_menu_probe(args.device, home, task, seconds=args.seconds), ensure_ascii=False, indent=2))
        elif args.action == 'ricoh-ui':
            from .ricoh_ui import run_lab
            run_lab(args.device, home, task, on_ready=lambda url: print(url, flush=True))
        elif args.action == 'ricoh-ui-check':
            from .ricoh_ui import run_menu_probe
            print(json.dumps(run_menu_probe(args.device, home, task, seconds=args.seconds), ensure_ascii=False, indent=2))
        elif args.action == 'ricoh-ui-import':
            from .ricoh_ui import import_update
            print(import_update(args.file, args.output, task))
        elif args.action == 'camera-lab':
            from .camera_lab import run_lab
            result = run_lab(args.device, home, task, seconds=args.seconds, mode=args.mode)
            print(json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result))
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
