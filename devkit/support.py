"""Local support workspace and evidence; never equate launch with guest boot."""
from pathlib import Path
import shutil
import tempfile
import zipfile
from .core import read_json, write_json
from .library import is_camera
from .boot import validate_profile

def diagnose(device, qemu=None):
    device = Path(device)
    profile = read_json(device / 'device.json')
    if not profile:
        raise ValueError('Select an imported firmware library directory')
    inspection = read_json(device / 'inspection.json')
    blockers = list(inspection.get('blockers', []))
    boot = read_json(device / 'boot.json')
    configured = False
    if not is_camera(profile):
        try:
            validate_profile(boot, device)
            configured = True
        except (OSError, ValueError, KeyError, TypeError) as error:
            blockers.append(str(error))
    if not qemu:
        blockers.append('QEMU runtime has not been checked / 尚未检查 QEMU 运行环境')
    issues = []
    for name in ('console.log', 'qemu-stderr.log', 'generic-console.log'):
        path = device / name
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            text = stream.read(65536).decode(errors='replace').lower()
        for token, hint in (
            ('kernel panic', 'Kernel panic: inspect the first failure and board drivers.'),
            ('unable to mount root', 'Root filesystem missing or wrong root= / storage driver.'),
            ('no machine specified', 'Select a QEMU machine.'),
            ('unsupported machine', 'Machine is absent from the installed QEMU build.'),
            ('could not load', 'Check firmware/kernel paths and image format.'),
        ):
            if token in text and hint not in issues:
                issues.append(hint)
    return {
        'schema_version': 1, 'name': profile['name'], 'sha256': profile['sha256'],
        'adapter': profile.get('adapter', 'hasselblad-ui'),
        'format': inspection.get('format', 'validated-cim'),
        'architecture': inspection.get('architecture') or profile.get('abi'),
        'configuration_valid': configured, 'guest_boot_verified': False,
        'camera_ui_previously_verified': bool(is_camera(profile) and profile.get('validated')),
        'qemu_directory': str(qemu) if qemu else None,
        'blockers': list(dict.fromkeys(blockers)), 'log_findings': issues,
        'next_steps': [
            'Identify SoC, CPU, memory map, boot format and original board drivers.',
            'Use a QEMU-supported board kernel or implement missing peripherals.',
            'Extract a compatible rootfs/UI through a reviewed adapter.',
            'Verify serial boot, display, input, storage and clean shutdown separately.',
        ] if not is_camera(profile) else ['Launch the preserved Hasselblad UI adapter and verify display/input.'],
    }

def create_support(device, qemu=None):
    device = Path(device)
    root = device / 'support'; root.mkdir(exist_ok=True)
    data = diagnose(device, qemu)
    write_json(root / 'diagnostics.json', data)
    inspection = read_json(device / 'inspection.json')
    lines = ['# QEMU Everything — Firmware support / 固件适配', '',
             f"Input / 输入: {data['name']}", '', f"SHA-256: `{data['sha256']}`", '',
             f"Format / 格式: {data['format']}; architecture / 架构: {data['architecture'] or 'unknown / 未确定'}", '',
             'Import and a running QEMU process do not prove this firmware boots. / 导入成功和 QEMU 进程运行不代表固件启动成功。', '',
             '## Evidence / 识别依据', '']
    lines += ['- ' + str(value) for value in inspection.get('findings', [])]
    lines += ['', '## Blockers / 启动缺口', ''] + ['- ' + value for value in data['blockers']]
    lines += ['', '## Log findings / 日志线索', ''] + ['- ' + value for value in data['log_findings']]
    lines += ['', '## Next steps / 适配步骤', ''] + ['- ' + value for value in data['next_steps']]
    lines += ['', 'Edit `../boot.json` for generic boot. All component paths are relative to the firmware library entry.',
              '编辑 `../boot.json` 配置通用启动，组件路径相对于该固件目录。', '',
              'Installed adapter API / 已安装适配器接口: Python entry point `qemu_everything.adapters`;',
              '`id`, `label`, `matches(inspection)`, `import_firmware(source, library, task) -> Path`.', '']
    (root / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    plan = root / 'adapter-plan.json'
    if not plan.exists():
        write_json(plan, {'schema_version': 1, 'platform': '', 'soc': '', 'cpu': data['architecture'],
            'memory_map': {}, 'extraction': 'not implemented', 'qemu_board': '',
            'missing_devices': [], 'checks': {'serial': False, 'display': False, 'input': False, 'storage': False}})
    return root

def export_support(device, destination, qemu=None):
    """Export allowlisted diagnostics, user plan and bounded logs; no firmware."""
    device, destination = Path(device).resolve(), Path(destination).resolve()
    if destination.is_relative_to(device):
        raise ValueError('Save the support ZIP outside the firmware workspace / 请将资料包保存到固件工作区之外')
    if destination.suffix.lower() != '.zip':
        raise ValueError('Support bundle filename must end in .zip')
    if destination.exists():
        raise FileExistsError('Choose a new ZIP filename; existing files are preserved / 请使用新的 ZIP 文件名，已有文件会保留')
    destination.parent.mkdir(parents=True, exist_ok=True)
    create_support(device, qemu)
    entries = ('device.json', 'inspection.json', 'boot.json', 'support/report.md',
               'support/diagnostics.json', 'support/adapter-plan.json', 'generic-command.json',
               'console.log', 'qemu-stderr.log', 'generic-console.log')
    with tempfile.TemporaryFile() as temporary:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in entries:
                path = device / name
                if not path.is_file() or path.is_symlink():
                    continue
                with path.open('rb') as stream:
                    if path.suffix == '.log': stream.seek(max(0, path.stat().st_size - 1024**2))
                    data = stream.read(1024**2)
                archive.writestr(name, data)
        temporary.seek(0)
        with destination.open('xb') as output: shutil.copyfileobj(temporary, output)
    return destination
