# SPDX-License-Identifier: GPL-2.0-or-later
"""Pinned HERO8 component platform construction and immutable input helpers."""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import zlib

RTOS_SIZE = 70503296
RTOS_SHA256 = '49e0d40253f517fc6512f4bc9ff59a9d63c9a86e7cd84a6b0c97f523278de2e5'
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
PROFILE = 'gopro-hero8-tara-ui-v1'
ROOT = Path(__file__).resolve().parent
SOURCES = ('build_ui.py', 'run_ui.py', 'component.py', 'component_viewer.py',
           'gopro-hero8-reset-lab.c', 'ew_component.c', 'ew_entry.S')
WIDTH, HEIGHT = 1058, 540
SHOOT_SHA256 = '6e479746029ed434fe1ae5641fd11cd4c5d291ed724f4a6eedf9154bd52b7127'
MENU_SHA256 = 'b9a6208933604906fe8b72999cd12654d09c946d36b001c6bd32606da21e1dfb'
NAMES = ('magic stage result heap_next alloc_count free_count failed_size fatal_code '
         'exception exception_lr dfar dfsr root viewport bitmap canvas width height '
         'rect_x0 rect_y0 rect_x1 rect_y1 frames request complete operation event_result').split()

# Only reviewed eight-byte branch veneers are changed in an owned RAM image.
# The original SHA pins all vendor text/resources; each veneer also checks its
# exact original bytes. All UI code, fonts, icons and widget state stay native.
HOOKS = (
    (0xa11d2070, '10482de908b08de2', 'lab_alloc', 'Bounded real 64 MiB EW heap'),
    (0xa0c0f5a8, '30482de94c4603e3', 'lab_alloc', 'Bounded real native SDK heap'),
    (0xa0c0f5e4, '78482de90040a0e1', 'lab_free', 'Free deferred until owned session cleanup'),
    (0xa0212c9c, '10402de908d04de2', 'lab_alloc', 'Native malloc with real backing'),
    (0xa0212d78, '10402de908d04de2', 'lab_alloc_r', 'Reentrant malloc context/size ABI'),
    (0xa0212e54, '10402de908d04de2', 'lab_free', 'Standard free deferred until cleanup'),
    (0xa0212f60, '10402de908d04de2', 'lab_free_r', 'Reentrant free context/pointer ABI'),
    (0xa11d20f0, '00482de904b08de2', 'lab_free', 'EW free deferred; finite session only'),
    (0xa11d2170, '00482de904b08de2', 'lab_time', 'Actual 40 MHz counter to milliseconds'),
    (0xa11d2180, '00482de904b08de2', 'lab_time', 'Actual 40 MHz counter to milliseconds'),
    (0xa0c10f20, '00482de904b08de2', 'lab_time', 'Native scalar SDK monotonic clock'),
    (0xa0c0ff84, 'f0492de9046080e2', 'lab_mutex_create', 'Owned software recursive mutex object'),
    (0xa0c10194, '70482de90050a0e1', 'lab_mutex_wait', 'Valid single-thread recursive acquisition'),
    (0xa0c10264, '70482de910b08de2', 'lab_mutex_unlock', 'Valid held mutex release'),
    (0xa02113d8, '0f002de930402de9', 'lab_log', 'Original formatter with bounded real character capture'),
    (0xa092e240, '10482de908b08de2', 'lab_camera_config', 'Select original HERO8 table A5265E18'),
    (0xa11bbd20, 'f04f2de920b08de2', 'lab_display_update', 'Copy actual original native raster to software display'),
    (0xa11d2100, 'd03f00e329354ae3', 'lab_fatal', 'Preserve fatal failure, record and park'),
    (0xa021306c, '30402de90cd04de2', 'lab_calloc', 'Checked calloc with actual zeroed allocation'),
    (0xa0213170, '30402de90cd04de2', 'lab_calloc_r', 'Checked reentrant calloc'),
)


def regular(path, bound=256*1024**2):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected absolute input path')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Input path contains a symlink')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > bound:
        raise ValueError('Expected bounded non-hardlinked regular input')
    return path


def digest(path, bound=256*1024**2):
    value = hashlib.sha256()
    with regular(path, bound).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            value.update(block)
    return value.hexdigest()


def freeze(source, destination, expected, size=None, bound=256*1024**2):
    """Copy checked bytes with open/stat identity verification before executing."""
    source = regular(source, bound)
    before = source.stat(); value = hashlib.sha256(); total = 0
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as reader, Path(destination).open('xb') as writer:
        opened = os.fstat(reader.fileno())
        if not stat.S_ISREG(opened.st_mode) or (size is not None and opened.st_size != size):
            raise ValueError('Snapshot input has wrong size/type')
        for block in iter(lambda: reader.read(1024**2), b''):
            total += len(block)
            if total > bound or (size is not None and total > size):
                raise ValueError('Snapshot input grew beyond limit')
            value.update(block); writer.write(block)
        after = os.fstat(reader.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if (len({identity(s) for s in (before, opened, after, source.stat())}) != 1
            or value.hexdigest() != expected or (size is not None and total != size)):
        raise ValueError('Snapshot source changed or fingerprint mismatch')
    return Path(destination)


def compile_bridge(output):
    output = Path(output); output.mkdir(exist_ok=True)
    script = output/'component.ld'
    script.write_text('ENTRY(_start)\nSECTIONS { . = 0xa9000000; .entry : { *(.entry) } .text : { *(.text*) } .rodata : { *(.rodata*) } .data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } end = .; /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n')
    commands = []
    for name in ('ew_component.c', 'ew_entry.S'):
        copied = freeze(ROOT/name, output/name, digest(ROOT/name))
        cmd = ['clang', '--target=arm-none-eabi', '-mcpu=cortex-a7', '-marm', '-mfloat-abi=hard',
               '-mfpu=neon', '-O2', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
               '-fno-unwind-tables', '-c', str(copied), '-o', str(copied.with_suffix('.o'))]
        subprocess.run(cmd, check=True); commands.append(cmd)
    cmd = ['arm-linux-gnueabihf-ld', '-T', str(script), str(output/'ew_component.o'),
           str(output/'ew_entry.o'), '-o', str(output/'component.elf')]
    subprocess.run(cmd, check=True); commands.append(cmd)
    cmd = ['arm-linux-gnueabihf-objcopy', '-O', 'binary', str(output/'component.elf'), str(output/'component.bin')]
    subprocess.run(cmd, check=True); commands.append(cmd)
    symbols = {}
    for row in subprocess.check_output(['arm-linux-gnueabihf-nm', str(output/'component.elf')], text=True).splitlines():
        fields = row.split()
        if len(fields) == 3: symbols[fields[2]] = int(fields[0], 16)
    size = (output/'component.bin').stat().st_size
    if not 256 <= size <= 65536 or any(not 0xa9000000 <= symbols[name] < 0xa9000000+size for _,_,name,_ in HOOKS):
        raise ValueError('Compiled bridge or required hook target invalid')
    return dict(platform=str(output/'component.bin'), platform_sha256=digest(output/'component.bin'),
                platform_symbols={name: hex(value) for name,value in symbols.items()}, commands=commands)


def patched_snapshot(original, output, symbols):
    blob = bytearray(Path(original).read_bytes())
    if len(blob) != RTOS_SIZE or hashlib.sha256(blob).hexdigest() != RTOS_SHA256:
        raise ValueError('Requires exact original HERO8 HD8.01.02.51.00 RTOS')
    patches = []
    for address, expected, name, semantic in HOOKS:
        offset = address-0xa0000000; target = int(symbols[name], 16)
        if bytes(blob[offset:offset+8]) != bytes.fromhex(expected) or not 0xa9000000 <= target < 0xa9010000:
            raise ValueError('Original hook bytes or bridge target mismatch')
        replacement = struct.pack('<II', 0xe51ff004, target)
        blob[offset:offset+8] = replacement
        patches.append(dict(address=hex(address), original_bytes=expected,
                            replacement_bytes=replacement.hex(), target=hex(target), semantic=semantic))
    Path(output).write_bytes(blob)
    return patches


def rgba_png(raw):
    if len(raw) != WIDTH*HEIGHT*4: raise ValueError('Unexpected original RGBA framebuffer length')
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    scan = b''.join(b'\0'+raw[y*WIDTH*4:(y+1)*WIDTH*4] for y in range(HEIGHT))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',WIDTH,HEIGHT,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(scan))+chunk(b'IEND',b'')
