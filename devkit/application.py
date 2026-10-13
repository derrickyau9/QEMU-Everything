"""Native Qt firmware workbench with a retro instrument-panel interface."""
from pathlib import Path
import argparse
import json
import locale
import os
import platform
import shutil
import struct
import sys
import time
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRectF, QUrl
from PySide6.QtGui import QPainter, QColor, QImage, QDesktopServices, QFont, QPixmap, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QListWidget, QStackedWidget, QFileDialog,
    QPlainTextEdit, QComboBox, QMessageBox, QProgressBar, QLineEdit, QDialog, QCheckBox,
    QDialogButtonBox, QSplitter, QScrollArea)
from .core import ASSETS, Task, Cancelled, data_home, read_json, write_json
from .library import import_firmware, is_camera
from .runtime import discover, setup, discover_qemu, setup_qemu
from .development import workspace, build, find_ndk
from .session import Session
from .generic_session import GenericSession
from .boot import BOARDS, validate_profile, build_command
from .support import create_support, export_support
from .guest.prepare import network_supported
from .models import MODELS, model_id, available_models
from .theme import apply_theme, current_colors
from .retro_widgets import paint_idle_screen

THEME_MODES = ('retro', 'system', 'light', 'dark')

class Job(QThread):
    message = Signal(str)
    url_ready = Signal(str)
    result = Signal(object)
    failed = Signal(str)
    def __init__(self, operation):
        super().__init__()
        self.operation, self.task = operation, Task(self.message.emit)
    def run(self):
        try:
            self.result.emit(self.operation(self.task))
        except Cancelled:
            self.message.emit('Cancelled / 已取消')
        except Exception as error:
            self.failed.emit(str(error))

class Screen(QWidget):
    touch = Signal(str, int, int)
    def __init__(self):
        super().__init__()
        self.frame = None
        self.message = 'QEMU Everything'
        self.caption = 'Import official firmware to begin'
        self.last_move = 0
        self.dragging = False
        self.setMinimumSize(400, 240)
        self.setSizePolicy(self.sizePolicy().Policy.Expanding, self.sizePolicy().Policy.Expanding)
    def bounds(self):
        fw, fh = (self.frame.width(),self.frame.height()) if self.frame is not None else (1024,768)
        scale = min(self.width()/fw, self.height()/fh)
        w, h = fw*scale, fh*scale
        return QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor('#0f0e13'))
        if self.frame is not None:
            painter.drawImage(self.bounds(), self.frame)
        else:
            paint_idle_screen(painter, self.rect(), self.message, self.caption, current_colors())
        painter.end()
    def send(self, operation, event):
        r = self.bounds(); p = event.position()
        if operation == 'down' and not r.contains(p):
            return
        fw, fh = (self.frame.width(),self.frame.height()) if self.frame is not None else (1024,768)
        x = max(0, min(fw-1, int((p.x()-r.x())*fw/r.width())))
        y = max(0, min(fh-1, int((p.y()-r.y())*fh/r.height())))
        self.touch.emit(operation, x, y)
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.bounds().contains(event.position()):
            self.dragging = True
            self.send('down', event)
    def mouseMoveEvent(self, event):
        if self.dragging and time.monotonic()-self.last_move > .016:
            self.last_move = time.monotonic(); self.send('move', event)
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragging:
            self.send('up', event); self.dragging = False
class DropArea(QFrame):
    dropped = Signal(str)
    clicked = Signal()
    def __init__(self, title, caption):
        super().__init__()
        self.setAcceptDrops(True); self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self); layout.setContentsMargins(20, 16, 20, 16); layout.setSpacing(18)
        symbol = QLabel('+'); symbol.setObjectName('importSymbol'); symbol.setAlignment(Qt.AlignCenter); symbol.setFixedSize(46, 46)
        layout.addWidget(symbol)
        text = QVBoxLayout(); text.setSpacing(5); layout.addLayout(text, 1)
        self.title = QLabel(title); self.title.setObjectName('subtitle'); self.title.setWordWrap(True)
        self.caption = QLabel(caption); self.caption.setWordWrap(True); self.caption.setObjectName('muted')
        text.addWidget(self.title); text.addWidget(self.caption)
        self.action = QLabel(); self.action.setObjectName('importAction'); layout.addWidget(self.action)
    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).is_file():
            event.acceptProposedAction()
    def dropEvent(self, event):
        self.dropped.emit(event.mimeData().urls()[0].toLocalFile())
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton: self.clicked.emit()
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Space): self.clicked.emit()

class Window(QMainWindow):
    def __init__(self, home):
        super().__init__()
        self.home = home
        home.mkdir(parents=True, exist_ok=True)
        self.preferences = read_json(home / 'preferences.json')
        if self.preferences.get('appearance_version') != 1:
            self.preferences.update(theme='retro', appearance_version=1)
        self.lang = self.preferences.get('language', 'zh' if (locale.getlocale()[0] or '').lower().startswith('zh') else 'en')
        self.bindings = []
        self.job = self.session = None
        self.runtime = discover(home)
        self.qemu_runtime = discover_qemu(home)
        self.devices = []; self.device = None
        self.last_frame = None
        self.closing = False
        self.setWindowTitle('QEMU Everything')
        cat_logo = QPixmap(str(ASSETS / 'assets' / 'cat-logo.jpg'))
        if not cat_logo.isNull():
            self.setWindowIcon(QIcon(cat_logo))
        self.resize(1440, 960); self.setMinimumSize(1120, 800)
        self.setAcceptDrops(True)
        app = QApplication.instance()
        apply_theme(app, self.preferences.get('theme', 'retro'), self.preferences.get('accent', 'violet'))
        app.styleHints().colorSchemeChanged.connect(lambda *_: self.appearance())
        body = QWidget(); self.setCentralWidget(body)
        row = QHBoxLayout(body); row.setContentsMargins(16, 16, 16, 8); row.setSpacing(22)
        sidebar = QFrame(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(214)
        side = QVBoxLayout(sidebar); side.setContentsMargins(18, 20, 18, 18); side.setSpacing(10)
        side.addWidget(self.label('QE / SYSTEMS LAB', 'QE / 系统实验室', 'brandMeta'))
        brand = QLabel()
        brand.setAccessibleName('Cat logo')
        brand.setObjectName('catPortrait')
        brand.setAlignment(Qt.AlignCenter)
        brand.setPixmap(cat_logo.scaled(156, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        side.addWidget(brand)
        side.addWidget(self.label('QEMU\nEVERYTHING', 'QEMU\nEVERYTHING', 'brandTitle'))
        side.addWidget(self.label('FIRMWARE EXPLORER', '固件探索工作台', 'brandMeta'))
        side.addSpacing(22)
        side.addWidget(self.label('WORKSPACE', '工作空间', 'railHeading'))
        self.pages = QStackedWidget(); self.nav = []
        for i, (en, zh) in enumerate([('Firmware library', '固件库'), ('Development', '开发工作区'), ('Platform support', '平台适配'), ('Console and logs', '控制台与日志'), ('Settings', '设置')]):
            b = QPushButton(); b.setObjectName('nav'); b.setCheckable(True)
            b.setSizePolicy(b.sizePolicy().Policy.Expanding, b.sizePolicy().Policy.Preferred)
            self.bind(b.setText, f'{i + 1:02d}  {en}', f'{i + 1:02d}  {zh}'); b.clicked.connect(lambda _=False, n=i: self.navigate(n))
            side.addWidget(b); self.nav.append(b)
        side.addStretch()
        side.addWidget(self.label('HOST / LOCAL', '主机 / 本地', 'railHeading'))
        host_label = 'macOS ARM64' if sys.platform == 'darwin' and platform.machine() == 'arm64' else ('macOS' if sys.platform == 'darwin' else 'Windows x64' if os.name == 'nt' else platform.system())
        side.addWidget(self.label(f'{host_label}\nARM / x86 / RISC-V\nv0.3.0', f'{host_label}\nARM / x86 / RISC-V\nv0.3.0', 'brandMeta'))
        self.official = self.button('Hasselblad downloads ↗', '哈苏官方固件 ↗', lambda: QDesktopServices.openUrl(QUrl('https://www.hasselblad.com/x-system/firmware/')))
        self.official.setStyleSheet('padding-left: 6px; padding-right: 6px; font-size: 12px;')
        side.addWidget(self.official)
        row.addWidget(sidebar); row.addWidget(self.pages, 1)
        self.make_library(); self.make_development(); self.make_support(); self.make_console(); self.make_settings()
        self.navigate(0)
        self.progress = QProgressBar(); self.progress.setMaximumWidth(170); self.progress.setRange(0, 0); self.progress.hide()
        self.cancel = self.button('Cancel', '取消', self.cancel_job); self.cancel.hide()
        self.statusBar().addPermanentWidget(self.progress); self.statusBar().addPermanentWidget(self.cancel)
        self.statusBar().setSizeGripEnabled(False)
        self.refresh_library(); self.update_controls()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(16)

    def tr(self, en, zh): return zh if self.lang == 'zh' else en
    def bind(self, setter, en, zh):
        self.bindings.append((setter, en, zh)); setter(self.tr(en, zh))
    def label(self, en, zh, style=None):
        label = QLabel(); label.setWordWrap(True)
        if style: label.setObjectName(style)
        self.bind(label.setText, en, zh); return label
    def button(self, en, zh, action, primary=False):
        b = QPushButton(); self.bind(b.setText, en, zh); b.clicked.connect(action)
        if primary: b.setObjectName('primary')
        return b
    def card(self, layout):
        card = QFrame(); card.setObjectName('card'); inner = QVBoxLayout(card); inner.setContentsMargins(22, 20, 22, 20); inner.setSpacing(12)
        layout.addWidget(card); return inner
    def page(self, en, zh, sub_en, sub_zh, scroll=True):
        p = QWidget(); layout = QVBoxLayout(p); layout.setContentsMargins(0, 6, 0, 0); layout.setSpacing(14)
        top = QHBoxLayout()
        eyebrow = self.label('QEMU EVERYTHING / WORKBENCH', 'QEMU EVERYTHING / 开发工作台', 'eyebrow'); eyebrow.setWordWrap(False)
        top.addWidget(eyebrow); top.addStretch()
        edition = self.label('LOCAL EDITION', '本地版本', 'brandMeta'); edition.setWordWrap(False)
        top.addWidget(edition); layout.addLayout(top)
        layout.addWidget(self.label(en, zh, 'workspaceTitle')); layout.addWidget(self.label(sub_en, sub_zh, 'workspaceSubtitle'))
        if scroll:
            area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(p); self.pages.addWidget(area)
        else: self.pages.addWidget(p)
        return layout

    def make_library(self):
        layout = self.page('QEMU Everything', 'QEMU Everything',
            'A home for every firmware. A workspace for the next discovery.', '每一份固件，都值得探索。下一次发现，从这里开始。')
        self.drop = DropArea('', '')
        self.bind(self.drop.title.setText, 'Load your next discovery', '载入下一次发现')
        self.bind(self.drop.caption.setText, 'Drop a firmware, kernel or disk image here to inspect it.', '将固件、内核或磁盘镜像拖到这里，开始分析。')
        self.bind(self.drop.action.setText, 'BROWSE FILES  ↗', '选择文件  ↗')
        self.drop.clicked.connect(self.browse); self.drop.dropped.connect(self.import_file); layout.addWidget(self.drop)
        content = QHBoxLayout(); content.setSpacing(16); layout.addLayout(content, 1)
        list_card = QFrame(); list_card.setObjectName('firmwarePanel'); list_card.setFixedWidth(230)
        left = QVBoxLayout(list_card); left.setContentsMargins(16, 16, 16, 16); left.setSpacing(12)
        list_header = QHBoxLayout(); list_header.addWidget(self.label('01 / LIBRARY', '01 / 固件库', 'moduleHeader'))
        self.library_count = QLabel('00'); self.library_count.setObjectName('miniLabel'); list_header.addStretch(); list_header.addWidget(self.library_count); left.addLayout(list_header)
        self.library = QListWidget(); self.library.currentRowChanged.connect(self.select_device); left.addWidget(self.library, 1)
        self.library.setTextElideMode(Qt.ElideRight); self.library.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.empty_library = self.label('NO FIRMWARE YET\n\nDrop a file above to create\nyour first workspace.', '尚未载入固件\n\n拖入文件，建立你的\n第一个开发工作区。', 'emptyLibrary')
        self.empty_library.setAlignment(Qt.AlignCenter); left.insertWidget(1, self.empty_library, 1)
        self.details = QLabel(); self.details.setWordWrap(True); self.details.setObjectName('muted'); left.addWidget(self.details)
        self.model_choice = QComboBox(); self.model_choice.currentIndexChanged.connect(self.select_model); left.addWidget(self.model_choice)
        self.folder = self.button('Open files', '打开文件目录', self.open_device); left.addWidget(self.folder)
        left.addWidget(self.button('Inspect & configure', '分析与配置', lambda: self.navigate(2)))
        content.addWidget(list_card)
        preview = QFrame(); preview.setObjectName('displayPanel'); right = QVBoxLayout(preview); right.setContentsMargins(16, 14, 16, 16); right.setSpacing(12)
        header = QHBoxLayout(); header.addWidget(self.label('02 / DISPLAY OUTPUT', '02 / 显示输出', 'moduleHeader')); header.addStretch()
        self.badge = QLabel(); self.badge.setObjectName('badge'); header.addWidget(self.badge); right.addLayout(header)
        self.screen = Screen(); self.screen.touch.connect(self.send_touch); right.addWidget(self.screen, 1)
        self.preview_hint = QLabel(); self.preview_hint.setObjectName('muted'); self.preview_hint.setWordWrap(True); right.addWidget(self.preview_hint)
        key_row = QHBoxLayout(); self.camera_keys = []
        for i in range(1,6):
            key = QPushButton(f'F{i}')
            key.pressed.connect(lambda i=i:self.send_touch('key',58+i,1))
            key.released.connect(lambda i=i:self.send_touch('key',58+i,0))
            self.camera_keys.append(key); key_row.addWidget(key)
        key_row.addStretch(); right.addLayout(key_row)
        buttons = QHBoxLayout()
        self.start = self.button('Start original UI', '启动原厂 UI', self.launch, True)
        self.stop = self.button('Stop', '停止', self.stop_session)
        self.shot = self.button('Screenshot', '截图', self.screenshot)
        buttons.addWidget(self.start); buttons.addWidget(self.stop); buttons.addStretch(); buttons.addWidget(self.shot); right.addLayout(buttons)
        content.addWidget(preview, 1)
        readouts = QFrame(); readouts.setObjectName('topbar'); metrics = QHBoxLayout(readouts); metrics.setContentsMargins(18, 12, 18, 12); metrics.setSpacing(24)
        self.readouts = []
        for en, zh in [('IMAGES IN LIBRARY', '固件库镜像'), ('LOCAL RUNTIME', '本地运行环境'), ('SESSION', '当前会话')]:
            column = QVBoxLayout(); column.setSpacing(4); column.addWidget(self.label(en, zh, 'miniLabel'))
            value = QLabel(); value.setObjectName('retroReadout'); column.addWidget(value); self.readouts.append(value); metrics.addLayout(column, 1)
        layout.addWidget(readouts)
        terminal = QFrame(); terminal.setObjectName('terminalBar'); line = QHBoxLayout(terminal); line.setContentsMargins(14, 8, 8, 8)
        prompt = QLabel('>_'); prompt.setObjectName('eyebrow'); line.addWidget(prompt)
        self.last_event_message = ''
        self.last_event = QLabel(); self.last_event.setObjectName('footerStatus')
        self.last_event.setMinimumWidth(0)
        self.last_event.setSizePolicy(self.last_event.sizePolicy().Policy.Ignored, self.last_event.sizePolicy().Policy.Preferred)
        line.addWidget(self.last_event, 1)
        line.addWidget(self.button('Open log ↗', '查看日志 ↗', lambda: self.navigate(3))); layout.addWidget(terminal)

    def make_development(self):
        layout = self.page('Development', '开发工作区',
            'Camera app tools use the Hasselblad adapter. Generic platforms have a support workspace.', '相机应用工具使用哈苏适配器；通用平台提供独立的适配工作区。')
        card = self.card(layout)
        card.addWidget(self.label('01  Create your workspace', '01  创建开发工作区', 'subtitle'))
        card.addWidget(self.label('Create hello.c and a guide for the selected firmware. Existing edits are preserved.', '为选中的固件创建 hello.c 和开发说明，保留已有修改。', 'muted'))
        card.addWidget(self.button('Open development workspace', '打开开发工作区', self.open_workspace))
        card = self.card(layout)
        card.addWidget(self.label('02  Build and run', '02  编译并运行', 'subtitle'))
        card.addWidget(self.label('Use NDK r27 clang. The build selects Android ARM32, ARM64, or Linux ARMhf automatically.', '使用 NDK r27 的 clang，自动选择 Android ARM32、ARM64 或 Linux ARMhf；仅运行 UI 不需要 NDK。', 'muted'))
        ndk_row = QHBoxLayout(); self.ndk = QLineEdit(str(find_ndk() or self.preferences.get('ndk', '')))
        self.ndk.setPlaceholderText('Android NDK folder'); ndk_row.addWidget(self.ndk, 1)
        ndk_row.addWidget(self.button('Browse…', '选择…', self.choose_ndk)); card.addLayout(ndk_row)
        buttons = QHBoxLayout(); self.build_button = self.button('Build app', '编译应用', self.build_app, True)
        self.app_button = self.button('Run app', '运行应用', lambda: self.launch(mode='app'))
        buttons.addWidget(self.build_button); buttons.addWidget(self.app_button); buttons.addStretch(); card.addLayout(buttons)
        card.addWidget(self.button('Download Android NDK ↗', '下载 Android NDK ↗', lambda: QDesktopServices.openUrl(QUrl('https://developer.android.com/ndk/downloads'))))
        card = self.card(layout)
        card.addWidget(self.label('03  Test a third-party guest app', '03  测试第三方虚拟机应用', 'subtitle'))
        card.addWidget(self.label('Import a validated .xdevapp package for this firmware. Apps run only in the local QEMU guest; original UI and apps use separate sessions.', '导入适用于当前固件的 .xdevapp 包。应用只在本地 QEMU 虚拟机运行，与原厂 UI 使用独立会话。', 'muted'))
        self.app_choice = QComboBox(); self.app_choice.currentIndexChanged.connect(lambda _: self.update_controls()); card.addWidget(self.app_choice)
        app_actions = QHBoxLayout()
        self.import_app_button = self.button('Import app package', '导入应用包', self.import_app_package)
        self.remove_app_button = self.button('Remove selected package', '移除所选应用包', self.remove_app_package)
        app_actions.addWidget(self.import_app_button); app_actions.addWidget(self.remove_app_button); app_actions.addStretch(); card.addLayout(app_actions)
        self.app_network = QCheckBox()
        self.bind(self.app_network.setText, 'Enable QEMU network for this app session', '为此次应用会话启用 QEMU 网络')
        self.app_network.setChecked(False)
        card.addWidget(self.app_network)
        card.addWidget(self.label('Off by default. Verified only for X2D II ARM64 apps in QEMU; the original camera UI stays offline.', '默认关闭。仅 X2D II ARM64 虚拟机应用已验证；原厂相机 UI 保持离线。', 'muted'))
        card = self.card(layout)
        card.addWidget(self.label('What this environment provides', '开发环境能力', 'subtitle'))
        card.addWidget(self.label('Original Qt UI + a minimal Wayland compositor + mocked camera services. The included C app uses shared memory. Custom Qt apps need a toolchain matching the selected firmware’s ABI.', '原厂 Qt UI、精简 Wayland 合成器和模拟相机服务。附带的 C 示例使用共享内存绘制；自定义 Qt 应用另需与所选固件 ABI 匹配的工具链。', 'muted'))
        layout.addStretch()

    def make_support(self):
        layout = self.page('Build platform support.', '建立平台支持。',
            'Detection provides evidence. Booting requires a compatible kernel, board and drivers.', '格式识别提供线索，实际启动还需要兼容的内核、板型与驱动。')
        split = QSplitter(Qt.Horizontal); layout.addWidget(split, 1)
        self.support_report = QPlainTextEdit(); self.support_report.setReadOnly(True); split.addWidget(self.support_report)
        config = QWidget(); box = QVBoxLayout(config); split.addWidget(config)
        box.addWidget(self.label('Generic boot configuration', '通用启动配置', 'subtitle'))
        self.board_choice = QComboBox(); self.board_choice.addItem('Choose board / 选择板型', None)
        for key, board in BOARDS.items(): self.board_choice.addItem(board['label'], key)
        self.board_choice.activated.connect(self.use_board); box.addWidget(self.board_choice)
        self.boot_editor = QPlainTextEdit(); self.boot_editor.setFont(QFont('Cascadia Mono', 10)); box.addWidget(self.boot_editor, 1)
        row = QHBoxLayout()
        self.component_buttons = []
        for kind in ('kernel', 'initrd', 'disk'):
            button = self.button(f'Add {kind}', {'kernel':'添加内核','initrd':'添加 initrd','disk':'添加磁盘'}[kind], lambda _=False, kind=kind:self.add_component(kind))
            self.component_buttons.append(button); row.addWidget(button)
        box.addLayout(row)
        self.save_boot_button = self.button('Validate & save', '验证并保存配置', self.save_boot, True); box.addWidget(self.save_boot_button)
        box.addWidget(self.label('Generic guests open a QEMU window. Serial I/O is in Console and logs. Disks use temporary snapshots; networking is disabled.', '通用虚拟机在独立 QEMU 窗口显示，串口输入输出在控制台中。磁盘使用临时快照，网络关闭。', 'muted'))
        row = QHBoxLayout()
        self.report_button = self.button('Refresh diagnosis', '刷新诊断', self.refresh_support)
        self.export_button = self.button('Export support bundle', '导出适配资料', self.export_bundle)
        self.unpack_button = self.button('Extract camera components', '解包相机组件', self.unpack_camera)
        row.addWidget(self.unpack_button); row.addWidget(self.report_button); row.addWidget(self.export_button)
        row.addStretch(); layout.addLayout(row)
        row = QHBoxLayout()
        self.nikon_lab_button = self.button('Original UI lab', '原厂 UI 实验', self.launch_nikon_lab)
        row.addWidget(self.nikon_lab_button)
        self.camera_lab_button = self.button('Camera Linux lab', '相机 Linux 实验', self.launch_camera_lab)
        row.addWidget(self.camera_lab_button)
        row.addWidget(self.button('Camera research', '相机品牌研究', self.show_camera_research))
        row.addStretch(); layout.addLayout(row)

    def unpack_camera(self):
        if not self.device or self.busy(): return
        from .camera_research import unpack_device
        device = self.device
        identity = read_json(device / 'inspection.json').get('camera', {})
        key_file = None
        if identity.get('key_required'):
            key_file = self.home / 'camera-keys/nikon-tables.json'
            if not key_file.is_file():
                name, _ = QFileDialog.getOpenFileName(self, self.tr('Select locally recovered Nikon XOR tables', '选择本地恢复的尼康 XOR 表'), '', 'JSON (*.json)')
                if not name: return
                key_file = Path(name)
        def done(result):
            self.refresh_support(); self.update_controls()
            self.log(self.tr('Checked camera components saved in extracted/. Review them before configuring boot.', '相机组件已校验并保存至 extracted/，请查看组件后再配置启动。'))
        self.run_job(lambda task: unpack_device(device, task, key_file=key_file), done)

    def launch_nikon_lab(self, *_):
        if not self.device or self.busy(): return
        from .camera_ui import run_lab
        device = self.device
        self.run_job(lambda task: run_lab(device, self.home, task, on_ready=self.job.url_ready.emit),
                     lambda _: self.log(self.tr('Original UI lab stopped.', '原厂 UI 实验已停止。')))

    def launch_camera_lab(self, *_):
        if not self.device or self.busy(): return
        from .camera_lab import run_lab
        device = self.device
        self.navigate(3)
        def done(result):
            self.log(json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result))
            run = Path(result['run_directory']) if isinstance(result, dict) else Path(result).parent
            serial = run / 'serial.log'
            if serial.is_file():
                with serial.open('rb') as stream:
                    stream.seek(max(0, serial.stat().st_size - 16000))
                    self.log(stream.read(16000).decode('utf-8', errors='replace'))
            self.log(self.tr('Linux lab finished. Original camera UI remains unverified.', 'Linux 实验结束；原厂相机 UI 尚未验证。'))
        self.run_job(lambda task: run_lab(device, self.home, task, seconds=30), done)

    def show_camera_research(self):
        from .camera_catalog import catalogue_markdown
        dialog = QDialog(self); dialog.setWindowTitle(self.tr('Camera firmware research', '相机固件研究'))
        dialog.resize(950, 650)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(); view.setReadOnly(True); view.setPlainText(catalogue_markdown(self.lang)); layout.addWidget(view)
        close = QDialogButtonBox(QDialogButtonBox.Close); close.rejected.connect(dialog.reject); layout.addWidget(close)
        dialog.exec()

    def refresh_support(self):
        if not self.device: return
        try:
            root = create_support(self.device, self.qemu_runtime['qemu'] if self.qemu_runtime else None)
            self.support_report.setPlainText((root / 'report.md').read_text(encoding='utf-8'))
        except (OSError, ValueError, KeyError) as error: self.error(str(error))

    def use_board(self, index):
        key = self.board_choice.itemData(index)
        if not key or not self.device: return
        try:
            data = json.loads(self.boot_editor.toPlainText() or '{}')
            if not isinstance(data, dict): raise ValueError('Boot configuration must be a JSON object / 启动配置必须是 JSON 对象')
            data.update(board=key, cpu=BOARDS[key]['default_cpu'], status='draft')
            data['cmdline'] = f'console={BOARDS[key]["console"]},115200' if data.get('kernel') else ''
            self.boot_editor.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
        except (ValueError, TypeError) as error: self.error(str(error))

    def save_boot(self):
        if not self.device or self.busy(): return False
        try:
            data = validate_profile(json.loads(self.boot_editor.toPlainText()), self.device)
            write_json(self.device / 'boot.json', data)
            self.boot_editor.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
            self.refresh_support(); self.update_controls()
            self.log(self.tr('Configuration valid; guest boot still needs verification.', '配置验证通过，固件实际启动仍需验证。'))
            return True
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.error(str(error)); return False

    def add_component(self, kind):
        if not self.device or self.busy(): return
        path, _ = QFileDialog.getOpenFileName(self, self.tr(f'Add {kind}', f'添加 {kind}'), '', 'All files (*)')
        if not path: return
        try:
            data = json.loads(self.boot_editor.toPlainText())
            if not isinstance(data, dict): raise ValueError('Boot configuration must be a JSON object / 启动配置必须是 JSON 对象')
        except ValueError as error: return self.error(str(error))
        device = self.device
        def copy(task):
            directory = device / 'components'; directory.mkdir(exist_ok=True)
            target = directory / (kind + '.bin'); temporary = target.with_suffix('.part')
            try:
                with Path(path).open('rb') as source, temporary.open('wb') as dest:
                    while block := source.read(1024**2): task.check(); dest.write(block)
                task.check(); temporary.replace(target)
            finally: temporary.unlink(missing_ok=True)
            return target.relative_to(device).as_posix()
        def done(relative):
            data[kind] = relative
            if kind == 'disk': data['disk_format'] = 'qcow2' if Path(path).suffix.lower() == '.qcow2' else 'raw'
            self.boot_editor.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
            self.log(self.tr('Component copied. Validate and save before boot.', '组件已复制，请验证并保存后启动。'))
        self.run_job(copy, done)

    def export_bundle(self):
        if not self.device or self.busy(): return
        path, _ = QFileDialog.getSaveFileName(self, self.tr('Export support bundle', '导出适配资料'), 'qemu-everything-support.zip', 'ZIP (*.zip)')
        if path:
            try:
                export_support(self.device, path, self.qemu_runtime['qemu'] if self.qemu_runtime else None)
                self.log(self.tr('Exported reports and bounded logs; firmware is excluded.', '已导出报告和限量日志，资料包不包含固件。'))
            except (OSError, ValueError) as error: self.error(str(error))

    def make_console(self):
        layout = self.page('Console & logs', '控制台与日志', 'Commands run only inside the local development guest.', '命令只在本地开发虚拟机中执行。')
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumBlockCount(2500); self.logs.setFont(QFont('Cascadia Mono', 10)); layout.addWidget(self.logs, 1)
        row = QHBoxLayout(); self.command = QLineEdit(); self.bind(self.command.setPlaceholderText, 'Guest shell command, e.g. uname -a', '虚拟机 Shell 命令，例如 uname -a')
        self.command.returnPressed.connect(self.send_command); row.addWidget(self.command, 1)
        self.send_button = self.button('Send to guest', '发送到虚拟机', self.send_command); row.addWidget(self.send_button); layout.addLayout(row)
        row = QHBoxLayout(); row.addWidget(self.button('Open logs folder', '打开日志目录', self.open_device)); row.addWidget(self.button('Clear view', '清空显示', self.logs.clear)); row.addStretch(); layout.addLayout(row)

    def make_settings(self):
        layout = self.page('Settings', '设置', 'Native Qt on your desktop. Additional camera board labs use separately configured WSL builds.', '桌面原生 Qt；新增相机板级实验使用单独配置的 WSL 构建。')
        card = self.card(layout); card.addWidget(self.label('Appearance', '外观', 'subtitle'))
        row = QHBoxLayout(); self.language = QComboBox(); self.language.addItems(['English', '简体中文']); self.language.setCurrentIndex(int(self.lang == 'zh'))
        self.language.currentIndexChanged.connect(self.change_language); row.addWidget(self.language)
        self.theme = QComboBox(); self.theme.addItems(['Retro / CRT 实验台', 'System / 跟随系统', 'Light / 浅色', 'Dark / 深色'])
        theme = self.preferences.get('theme', 'retro')
        self.theme.setCurrentIndex(THEME_MODES.index(theme) if theme in THEME_MODES else 0)
        self.theme.currentIndexChanged.connect(self.appearance); row.addWidget(self.theme)
        self.accent = QComboBox(); self.accent.addItems(['Iris / 鸢尾紫', 'Blue / 蓝色', 'Leaf / 叶绿']); self.accent.setCurrentIndex(['violet','blue','green'].index(self.preferences.get('accent','violet')))
        self.accent.currentIndexChanged.connect(self.appearance); row.addWidget(self.accent); card.addLayout(row)
        self.accent.setEnabled(self.theme.currentIndex() != 0)
        card = self.card(layout); card.addWidget(self.label('Runtime', '运行环境', 'subtitle'))
        self.runtime_label = QLabel(); self.runtime_label.setWordWrap(True); self.runtime_label.setObjectName('muted'); card.addWidget(self.runtime_label)
        self.setup_button = self.button('Set up runtime', '安装运行环境', self.install_runtime, True); card.addWidget(self.setup_button)
        self.setup_qemu_button = self.button('Set up QEMU only', '仅安装 QEMU', self.install_qemu); card.addWidget(self.setup_qemu_button)
        card.addWidget(self.label('Base setup downloads about 615 MB; X1D adds about 78 MB of Linux libraries. Reserve 8 GB, or 12 GB for X1D, plus space for each firmware. CPU emulation and Qt software rendering are used; GPU acceleration is unavailable in this ARM64 guest.', '基础环境下载约 615 MB，X1D 另需约 78 MB Linux 运行库。请预留 8 GB，X1D 建议 12 GB，多份固件需额外空间。当前使用 CPU 模拟与 Qt 软件渲染，此 ARM64 环境尚未提供 GPU 加速。', 'muted'))
        card.addWidget(self.button('Open DevKit data folder', '打开 DevKit 数据目录', lambda: self.open_path(self.home)))
        card = self.card(layout); card.addWidget(self.label('Credits & scope', '致谢与范围', 'subtitle'))
        card.addWidget(self.label('Retro workbench for QEMU Everything. Classic themes adapted from Derrick Yao’s tsla-infotainment-lab (MIT). Powered by QEMU, Android, Qt for Python and 7-Zip. Firmware is supplied by you and remains local. Unaffiliated with Hasselblad.', 'QEMU Everything 复古工作台；经典主题改编自 Derrick Yao 的 tsla-infotainment-lab（MIT）。基于 QEMU、Android、Qt for Python 和 7-Zip。固件由你提供并保留在本地。本项目与哈苏无隶属关系。', 'muted'))
        card.addWidget(self.button('Radium Wang · Hasselblad firmware research ↗', 'Radium Wang · 哈苏固件研究与致谢 ↗', lambda: QDesktopServices.openUrl(QUrl('https://github.com/radium-wang/Hasselblad-X-System-CIM-Firmware-Research-Feature-Extensions'))))
        card.addWidget(self.button('View source & documentation ↗', '查看源码与文档 ↗', lambda: QDesktopServices.openUrl(QUrl('https://github.com/derrickyau9/QEMU-Everything'))))
        layout.addStretch()

    def navigate(self, n):
        self.pages.setCurrentIndex(n)
        for i, b in enumerate(self.nav): b.setChecked(i == n)
    def change_language(self, index):
        self.lang = 'zh' if index else 'en'; self.preferences['language'] = self.lang
        for setter, en, zh in self.bindings: setter(self.tr(en, zh))
        self.save_preferences(); self.update_controls()
    def appearance(self, *_):
        if not hasattr(self, 'accent'): return
        self.preferences.update(theme=THEME_MODES[self.theme.currentIndex()], accent=['violet','blue','green'][self.accent.currentIndex()])
        self.accent.setEnabled(self.preferences['theme'] != 'retro')
        apply_theme(QApplication.instance(), self.preferences['theme'], self.preferences['accent']); self.save_preferences()
    def save_preferences(self): write_json(self.home / 'preferences.json', self.preferences)
    def open_path(self, path): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def open_device(self):
        if self.device: self.open_path(self.device)
    def open_workspace(self):
        if self.device: self.open_path(workspace(self.device) if is_camera(read_json(self.device / 'device.json')) else create_support(self.device))
        else: self.error(self.tr('Import and select a firmware first.', '请先导入并选择固件。'))
    def choose_ndk(self):
        path = QFileDialog.getExistingDirectory(self, 'Android NDK', self.ndk.text())
        if path:
            self.ndk.setText(path); self.preferences['ndk'] = path; self.save_preferences()
    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr('Import firmware', '导入固件'), '', 'Firmware and images (*.cim *.dat *.bin *.img *.qcow2 *.elf *.zip *.gz *.xz);;All files (*)')
        if path: self.import_file(path)
    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).is_file(): event.acceptProposedAction()
    def dropEvent(self, event): self.import_file(event.mimeData().urls()[0].toLocalFile())
    def busy(self): return bool((self.job and self.job.isRunning()) or (self.session and self.session.isRunning()))
    def import_file(self, path):
        if self.busy(): return self.error(self.tr('Stop the current operation before importing.', '请先停止当前任务再导入。'))
        def done(device):
            self.refresh_library(device)
            self.log(self.tr('Imported. Review support status before launching.', '已导入，请查看适配状态后启动。'))
        self.run_job(lambda task: import_firmware(path, self.home / 'library', task), done)
    def run_job(self, operation, done):
        if self.busy(): return
        self.job = Job(operation); self.job.message.connect(self.log); self.job.failed.connect(self.error); self.job.result.connect(done)
        self.job.url_ready.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.job.finished.connect(self.update_controls); self.job.start(); self.update_controls()
    def cancel_job(self):
        if self.job: self.job.task.cancelled.set()
    def install_runtime(self, *_ , then=None):
        if self.busy(): return
        dialog = QDialog(self); dialog.setWindowTitle(self.tr('Runtime setup', '安装运行环境')); box = QVBoxLayout(dialog)
        box.addWidget(self.label('Download QEMU, 7-Zip and the Android SDK system image from their official sources. They stay in your DevKit data folder.', '从官方来源下载 QEMU、7-Zip 与 Android SDK 系统镜像，保存在 DevKit 数据目录。'))
        license_link = QLabel('<a href="https://developer.android.com/studio/terms">Android SDK License Agreement / Android SDK 许可协议</a>'); license_link.setOpenExternalLinks(True); box.addWidget(license_link)
        accepted = QCheckBox(self.tr('I have read and accept the Android SDK license terms.', '我已阅读并接受 Android SDK 许可条款。')); box.addWidget(accepted)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        accepted.toggled.connect(buttons.button(QDialogButtonBox.Ok).setEnabled); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); box.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted: return
        def done(config):
            self.runtime = config
            self.qemu_runtime = discover_qemu(self.home)
            if then: QTimer.singleShot(100, then)
        self.run_job(lambda task: setup(self.home, task), done)
    def install_qemu(self, *_ , then=None, required=None):
        if self.busy(): return
        def done(config):
            self.qemu_runtime = config
            self.refresh_support()
            if then: QTimer.singleShot(100, then)
        self.run_job(lambda task: setup_qemu(self.home, task, required), done)
    def refresh_library(self, select=None):
        self.devices = sorted((self.home / 'library').glob('*/device.json'))
        self.library_count.setText(f'{len(self.devices):02d}')
        self.empty_library.setVisible(not self.devices)
        self.library.setVisible(bool(self.devices))
        self.details.setVisible(bool(self.devices))
        self.library.blockSignals(True); self.library.clear()
        selected = 0
        for i, path in enumerate(self.devices):
            data = read_json(path); self.library.addItem(f"{data['name']}\n{data['version']}")
            self.library.item(i).setToolTip(f"{data['name']}\n{data['version']}")
            if select and path.parent == select: selected = i
        self.library.blockSignals(False)
        self.library.setCurrentRow(selected if self.devices else -1)
        if not self.devices: self.select_device(-1)
    def select_device(self, index):
        self.device = self.devices[index].parent if 0 <= index < len(self.devices) else None
        self.screen.frame = None; self.screen.update(); self.last_frame = None
        self.model_choice.blockSignals(True); self.model_choice.clear()
        if self.device:
            profile = read_json(self.device / 'device.json')
            if is_camera(profile):
                for model in available_models(profile): self.model_choice.addItem(MODELS[model]['label'].removeprefix('Hasselblad '),model)
                self.model_choice.setCurrentIndex(self.model_choice.findData(model_id(profile)))
            self.boot_editor.setPlainText(json.dumps(read_json(self.device / 'boot.json'), indent=2, ensure_ascii=False))
            self.refresh_support()
        else:
            self.boot_editor.clear(); self.support_report.clear()
        self.model_choice.blockSignals(False)
        self.refresh_apps()
        self.update_controls()
    def select_model(self,index):
        if not self.device or self.busy(): return
        profile = read_json(self.device / 'device.json')
        selected = self.model_choice.itemData(index)
        if selected not in available_models(profile): return
        profile['model'] = selected
        write_json(self.device / 'device.json',profile)
        self.refresh_apps()
        self.update_controls()
    def refresh_apps(self, select=None):
        self.app_choice.clear()
        self.app_choice.addItem(self.tr('Built-in Hello example', '内置 Hello 示例'), None)
        if self.device and is_camera(read_json(self.device / 'device.json')):
            from .packages import list_packages
            profile = read_json(self.device / 'device.json')
            abi = profile.get('abi') or MODELS[model_id(profile)]['abi']
            for package in list_packages(self.device):
                if package['abi'] == abi and model_id(profile) in package['models']:
                    key = (package['id'], package['version'])
                    self.app_choice.addItem(f"{package['id']} · {package['version']}", key)
                    if key == select: self.app_choice.setCurrentIndex(self.app_choice.count()-1)
    def import_app_package(self):
        if self.busy() or not self.device: return
        path, _ = QFileDialog.getOpenFileName(self, self.tr('Import QEMU guest app', '导入 QEMU 虚拟机应用'), '', 'DevKit guest app (*.xdevapp)')
        if not path: return
        from .packages import install_package
        def done(package):
            self.refresh_apps((package['id'], package['version']))
            self.log(self.tr('Package imported. Select Run app to test in QEMU.', '应用包已导入，点击「运行应用」在 QEMU 中测试。'))
        self.run_job(lambda task: install_package(self.device, path, task), done)
    def remove_app_package(self):
        if self.busy() or not self.device: return
        key = self.app_choice.currentData()
        if key is None: return
        from .packages import remove_package
        self.run_job(lambda task: remove_package(self.device, *key), lambda _: self.refresh_apps())
    def launch(self, checked=False, mode='live'):
        from .camera_ui import available_lab as menu_available
        if self.device and mode == 'live' and menu_available(self.device, self.home):
            return self.launch_nikon_lab()
        from .camera_lab import available_lab
        if self.device and mode == 'live' and available_lab(self.device, self.home):
            return self.launch_camera_lab()
        if self.busy() or not self.device: return
        if not is_camera(read_json(self.device / 'device.json')):
            if not self.save_boot(): return
            boot = read_json(self.device / 'boot.json')
            required = BOARDS[boot['board']]['binary']
            self.qemu_runtime = discover_qemu(self.home, required)
            if not self.qemu_runtime: return self.install_qemu(then=self.launch, required=required)
            try:
                build_command(read_json(self.device / 'boot.json'), self.device, self.qemu_runtime['qemu'])
            except (OSError, ValueError, KeyError) as error: return self.error(str(error))
            self.screen.frame = None; self.last_frame = None
            self.session = GenericSession(self.device, self.qemu_runtime)
            self.session.message.connect(self.log); self.session.console.connect(self.logs.appendPlainText)
            self.session.phase.connect(self.on_phase); self.session.failed.connect(self.error)
            self.session.finished.connect(self.update_controls)
            self.session.start(); self.navigate(3); self.update_controls(); return
        if not self.runtime:
            return self.install_runtime(then=lambda: self.launch(mode=mode))
        network = mode == 'app' and self.app_network.isChecked()
        if network and not network_supported(read_json(self.device / 'device.json'), mode):
            return self.error(self.tr('QEMU networking is verified only for X2D II ARM64 app sessions.', 'QEMU 网络目前只在 X2D II ARM64 应用会话中验证。'))
        app_entry = None
        if mode == 'app':
            key = self.app_choice.currentData()
            if key is None:
                if not (self.device / 'payload-stage/app/hello').exists():
                    return self.error(self.tr('Build the app first.', '请先编译应用。'))
            else:
                from .packages import activate_package, active_package
                try:
                    activate_package(self.device, *key)
                    package = active_package(self.device)
                    app_entry = f"apps/{package['id']}/{package['version']}/{package['entry']}"
                except (OSError, ValueError, RuntimeError, KeyError) as error:
                    return self.error(str(error))
        self.screen.frame = None; self.last_frame = None
        # The network choice applies to one launch only; a later app run starts offline.
        if network: self.app_network.setChecked(False)
        self.session = Session(self.device, self.runtime, mode, app_entry=app_entry, network=network); self.session.message.connect(self.log)
        self.session.console.connect(self.logs.appendPlainText)
        self.session.phase.connect(self.on_phase); self.session.failed.connect(self.error); self.session.finished.connect(self.update_controls)
        self.session.start(); self.navigate(0); self.update_controls()
    def on_phase(self, phase):
        self.current_phase = phase; self.update_controls()
        self.statusBar().showMessage(self.badge.text())
    def stop_session(self):
        if self.session: self.session.stop()
    def build_app(self):
        if self.device and not self.busy(): self.run_job(lambda task: build(self.device, self.ndk.text(), task), lambda result: self.log(str(result)))
    def send_touch(self, op, x, y):
        if self.session: self.session.touch(op, x, y)
    def send_command(self):
        if self.session and self.session.ready and self.command.text().strip():
            self.session.commands.put(self.command.text()); self.command.clear()
    def screenshot(self):
        if self.screen.frame is None: return
        path, _ = QFileDialog.getSaveFileName(self, self.tr('Save screenshot', '保存截图'), 'qemu-everything-preview.png', 'PNG (*.png)')
        if path and not self.screen.frame.save(path): self.error('Could not save screenshot')
    def tick(self):
        if self.closing:
            if not self.busy(): self.close()
            return
        if self.session and self.session.isRunning() and self.session.frames:
            latest = self.session.frames.latest
            if latest and latest is not self.last_frame:
                head, pixels = latest
                _, w, h, stride, fmt, seq = struct.unpack_from('<6I', head)
                self.screen.frame = QImage(pixels, w, h, stride, QImage.Format_RGB32).copy()
                self.last_frame = latest; self.screen.update(); self.shot.setEnabled(True)
    def update_controls(self):
        busy = self.busy(); live = bool(self.session and self.session.isRunning()); ready = bool(live and self.session.ready)
        profile = read_json(self.device / 'device.json') if self.device else {}
        camera = bool(self.device and is_camera(profile))
        from .camera_ui import available_lab as menu_available
        nikon_menu = menu_available(self.device, self.home) if self.device else None
        nikon_lab = bool(nikon_menu)
        assisted_menu = bool(nikon_menu and nikon_menu.get('assisted_startup', True))
        from .camera_lab import available_lab
        linux_lab = available_lab(self.device, self.home) if self.device else None
        configured = False
        if self.device and not camera:
            try:
                validate_profile(read_json(self.device / 'boot.json'), self.device); configured = True
            except (OSError, ValueError, TypeError, KeyError): pass
        self.start.setText(self.tr('Start original UI lab', '启动原厂 UI 实验') if nikon_lab else self.tr('Start camera Linux lab', '启动相机 Linux 实验') if linux_lab else self.tr('Start original UI', '启动原厂 UI') if camera else self.tr('Start QEMU', '启动 QEMU'))
        self.start.setEnabled((camera or configured or nikon_lab or bool(linux_lab)) and not busy); self.stop.setEnabled(live)
        self.nikon_lab_button.setEnabled(nikon_lab and not busy)
        self.camera_lab_button.setEnabled(bool(linux_lab) and not busy)
        self.library.setEnabled(not busy); self.drop.setEnabled(not busy); self.folder.setEnabled(bool(self.device))
        self.model_choice.setEnabled(not busy and self.model_choice.count()>1)
        self.model_choice.setVisible(camera)
        for key in self.camera_keys: key.setEnabled(ready and camera); key.setVisible(camera)
        self.build_button.setEnabled(camera and not busy); self.app_button.setEnabled(camera and not busy)
        self.app_choice.setEnabled(camera and not busy)
        self.import_app_button.setEnabled(camera and not busy)
        self.remove_app_button.setEnabled(camera and not busy and self.app_choice.currentData() is not None)
        supports_network = bool(camera and network_supported(profile, 'app'))
        if not supports_network and self.app_network.isChecked(): self.app_network.setChecked(False)
        self.app_network.setEnabled(supports_network and not busy)
        self.setup_button.setEnabled(not busy); self.setup_qemu_button.setEnabled(not busy)
        self.send_button.setEnabled(ready); self.command.setEnabled(ready)
        self.save_boot_button.setEnabled(bool(self.device) and not camera and not busy)
        self.boot_editor.setEnabled(bool(self.device) and not camera and not busy)
        self.board_choice.setEnabled(bool(self.device) and not camera and not busy)
        for button in self.component_buttons: button.setEnabled(bool(self.device) and not camera and not busy)
        self.report_button.setEnabled(bool(self.device) and not busy); self.export_button.setEnabled(bool(self.device) and not busy)
        identity = read_json(self.device / 'inspection.json').get('camera', {}) if self.device else {}
        self.unpack_button.setEnabled(bool(identity and identity.get('extractable')) and not camera and not busy and not (self.device / 'extracted').exists())
        self.shot.setEnabled(self.screen.frame is not None)
        self.progress.setVisible(busy and not ready); self.cancel.setVisible(bool(self.job and self.job.isRunning()))
        phase = getattr(self, 'current_phase', 'stopped') if live else 'stopped'
        statuses = {'starting': ('Starting…', '启动中…'), 'running': ('Running', '运行中'), 'stopping': ('Stopping…', '停止中…'), 'stopped': ('Stopped', '已停止')}
        self.badge.setText(self.tr('QEMU running · boot unverified', 'QEMU 运行中 · 启动未验证') if live and not camera and phase == 'running' else self.tr(*statuses[phase])); self.badge.setProperty('phase', phase)
        if nikon_lab and self.job and self.job.isRunning():
            self.badge.setText(self.tr('Assisted original UI lab', '原厂 UI 辅助实验中') if assisted_menu else self.tr('Original ROM UI lab', '原厂 ROM 界面实验中'))
        elif linux_lab and self.job and self.job.isRunning():
            self.badge.setText(self.tr('Camera Linux lab', '相机 Linux 实验中'))
        self.badge.style().unpolish(self.badge); self.badge.style().polish(self.badge)
        self.readouts[0].setText(f'{len(self.devices):02d}')
        self.readouts[1].setText(self.tr('READY', '已就绪') if self.runtime or self.qemu_runtime else self.tr('NOT INSTALLED', '尚未安装'))
        self.readouts[2].setText(self.tr(*statuses[phase]).upper() if live else self.tr('WORKING', '任务执行中') if busy else self.tr('IDLE', '空闲'))
        self.render_last_event()
        self.screen.caption = self.tr('Click Start original UI', '点击「启动原厂 UI」') if camera else self.tr('Use Platform support to review and save a boot configuration.', '在「平台适配」中查看报告并保存启动配置。')
        if nikon_lab:
            self.screen.caption = self.tr('The assisted original UI opens in your browser. Cancel stops its guest.', '原厂 UI 在浏览器打开，使用辅助启动；点击「取消」停止虚拟机。') if assisted_menu else self.tr('The original ROM menu opens in your browser. Cancel stops its guest.', '原厂 ROM 菜单在浏览器打开；点击「取消」停止虚拟机。')
        elif linux_lab:
            self.screen.caption = self.tr('A 30-second original Linux boot check. Console records progress; Cancel stops the guest.', '限时 30 秒验证原厂 Linux 启动；控制台显示进度，点击「取消」停止虚拟机。')
        if not self.device: self.screen.caption = self.tr('Import firmware to begin', '导入固件，开始开发')
        if live and not ready: self.screen.caption = self.tr('Starting guest…', '正在启动虚拟机…')
        if live and not camera: self.screen.caption = self.tr('Display and input are in the QEMU window. Serial output is in Console and logs.', '显示与输入在 QEMU 独立窗口中，串口输出在控制台中。')
        self.preview_hint.setText(self.tr('Click to tap · Drag to swipe · Camera data is simulated', '鼠标点击 = 触控 · 拖动 = 滑动 · 相机数据为模拟值') if camera else self.tr('Generic QEMU displays open in their own window.', '通用 QEMU 的画面在独立窗口显示。'))
        if nikon_lab:
            self.preview_hint.setText(self.tr('Original UI and input open in your browser.', '原厂画面与输入在浏览器中打开。'))
        elif linux_lab:
            self.preview_hint.setText(self.tr('Serial boot evidence is saved in the firmware workspace.', '串口启动证据保存在所选固件的工作区。'))
        self.preview_hint.setVisible(bool(self.device))
        self.screen.update()
        self.runtime_label.setText(self.tr('Camera runtime ready · QEMU + Android ARM64', '相机运行环境已就绪 · QEMU + Android ARM64') if self.runtime else self.tr('Camera runtime not installed.', '尚未安装相机运行环境。'))
        self.runtime_label.setText(self.runtime_label.text() + '\n' + self.tr('Generic QEMU ready', '通用 QEMU 已就绪') if self.qemu_runtime else self.runtime_label.text() + '\n' + self.tr('Generic QEMU not installed', '尚未安装通用 QEMU'))
        if self.device:
            if camera:
                model = model_id(profile)
                self.model_choice.setToolTip(MODELS[model]['label'])
                status = self.tr('UI verified','UI 已验证') if profile.get('validated') and model == 'x2dii' else self.tr('UI development preview','UI 开发预览')
                self.details.setText(f"{status}\n{MODELS[model]['abi']} · {profile['version']}")
                self.screen.message = MODELS[model]['label'].removeprefix('Hasselblad ')
            else:
                status = (self.tr('Assisted UI lab configured', '原厂 UI 辅助实验已配置') if assisted_menu else self.tr('Original ROM UI lab configured', '原厂 ROM 界面实验已配置')) if nikon_lab else self.tr('Linux lab configured · UI unverified', 'Linux 实验已配置 · UI 未验证') if linux_lab else self.tr('Configured · boot unverified', '配置有效 · 启动未验证') if configured else self.tr('Inspection · needs adaptation', '已分析 · 待适配')
                self.details.setText(f"{status}\n{nikon_menu['label']}" if nikon_lab else f"{status}\n{linux_lab['label']}" if linux_lab else f"{status}\n{profile.get('architecture') or 'Unknown / 未知'} · {profile['version']}")
                self.screen.message = 'QEMU Everything'
        else:
            self.details.setText(self.tr('Your imported firmware will appear here.', '导入的固件会显示在这里。'))
            self.screen.message = 'QEMU Everything'
    def render_last_event(self):
        message = self.last_event_message or (self.tr('Firmware selected. Review its support status before starting.', '已选中固件，确认适配状态后即可启动。') if self.device else self.tr('Workbench ready. Load a firmware to begin.', '工作台已就绪，载入固件以开始。'))
        self.last_event.setToolTip(message)
        self.last_event.setText(self.last_event.fontMetrics().elidedText(message, Qt.ElideRight, max(60, self.last_event.width())))
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'last_event'): self.render_last_event()
    def log(self, message):
        if message.strip():
            self.logs.appendPlainText(message); self.statusBar().showMessage(message.splitlines()[-1][:180])
            self.last_event_message = message.splitlines()[-1]
            self.render_last_event()
    def error(self, message):
        self.log('ERROR: ' + message)
        QMessageBox.warning(self, self.tr('DevKit needs attention', 'DevKit 提示'), message)
    def closeEvent(self, event):
        if self.busy():
            self.closing = True; self.cancel_job(); self.stop_session()
            self.statusBar().showMessage(self.tr('Stopping the guest before closing…', '正在关闭虚拟机…')); event.ignore()
        else:
            self.save_preferences(); event.accept()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('firmware', nargs='?')
    parser.add_argument('--home', type=Path)
    parser.add_argument('--smoke-test', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1]); app.setApplicationName('QEMU Everything'); app.setOrganizationName('QEMU-Everything')
    window = Window((args.home or data_home()).resolve()); window.show()
    if args.smoke_test:
        # Packaged-executable verification without requiring UI automation software.
        output = args.smoke_test.resolve(); output.mkdir(parents=True, exist_ok=True)
        def fail(message):
            write_json(output / 'result.json', {'ok': False, 'error': message})
            window.close()
        window.error = fail
        def begin_check():
            if not window.device:
                return fail('Import firmware before this check')
            from .camera_ui import available_lab as menu_available
            menu = menu_available(window.device, window.home)
            if menu:
                # Exercise the same GUI job and URL signal as a normal launch.
                # Only this explicit test suppresses opening an external browser.
                window.launch()
                window.job.url_ready.disconnect()
                def poll_menu(_url=None):
                    from urllib.request import urlopen
                    try:
                        with urlopen(f"http://127.0.0.1:{menu['port']}{menu['status_path']}", timeout=1) as response:
                            state = json.load(response)
                        with urlopen(f"http://127.0.0.1:{menu['port']}{menu['frame_path']}", timeout=1) as response:
                            pixels = response.read()
                        frame = QImage.fromData(pixels)
                        visible = not frame.isNull() and any(frame.pixelColor(x, y).red() > 40
                            for y in range(20, frame.height(), 30) for x in range(20, frame.width(), 30))
                        if state.get('phase') != 'running' or not visible:
                            return QTimer.singleShot(500, poll_menu)
                        (output / 'guest.png').write_bytes(pixels)
                        window.grab().save(str(output / 'workbench.png'))
                        write_json(output / 'result.json', {'ok': True, 'frozen': bool(getattr(sys, 'frozen', False)),
                            'model': menu['label'], 'assisted_startup': menu.get('assisted_startup', True),
                            'natural_power_on_verified': False, 'state': state})
                        window.close()
                    except (OSError, ValueError):
                        if not window.closing: QTimer.singleShot(500, poll_menu)
                window.job.url_ready.connect(lambda url: QTimer.singleShot(2500, lambda: poll_menu(url)))
                return
            from .camera_lab import available_lab
            linux_lab = available_lab(window.device, window.home)
            if linux_lab:
                window.launch()
                def check_linux(result):
                    evidence = result if isinstance(result, dict) else read_json(result)
                    ok = bool(evidence.get('kernel_booted') and evidence.get('userspace_booted')
                              and evidence.get('process_reaped'))
                    window.grab().save(str(output / 'workbench.png'))
                    write_json(output / 'result.json', {'ok':ok, 'frozen':bool(getattr(sys,'frozen',False)),
                        'model':linux_lab['label'], 'kernel_booted':evidence.get('kernel_booted'),
                        'userspace_booted':evidence.get('userspace_booted'),
                        'original_ui_verified':False, 'process_reaped':evidence.get('process_reaped')})
                    window.close()
                window.job.result.connect(check_linux)
                return
            if not window.runtime:
                return fail('Import firmware and prepare runtime before this check')
            window.launch()
            def phase(value):
                if value == 'running': QTimer.singleShot(5000, capture_check)
            window.session.phase.connect(phase)
        def capture_check():
            window.tick()
            if window.screen.frame is None: return fail('No frame')
            window.screen.frame.save(str(output / 'guest.png'))
            window.grab().save(str(output / 'workbench.png'))
            write_json(output / 'result.json', {'ok': True, 'frozen': bool(getattr(sys, 'frozen', False)), 'firmware': read_json(window.device / 'device.json')['version']})
            window.close()
        if args.firmware:
            def import_check():
                window.import_file(args.firmware)
                window.job.finished.connect(lambda: QTimer.singleShot(100, begin_check) if not window.closing else None)
            QTimer.singleShot(200, import_check)
        else:
            QTimer.singleShot(500, begin_check)
        QTimer.singleShot(240000, lambda: fail('Timed out') if not window.closing else None)
    elif args.firmware: QTimer.singleShot(200, lambda: window.import_file(args.firmware))
    sys.exit(app.exec())
