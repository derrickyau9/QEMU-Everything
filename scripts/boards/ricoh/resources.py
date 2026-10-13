# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only GR IV RES bundle parser; never executes or publishes assets.

The record layout was independently derived from the official 1.11 payload:
16-byte RES header, fixed 32-byte NUL-padded ASCII path, LE32 byte length,
then that many unpadded file bytes. Paths are metadata, never host filenames.
"""
import hashlib
from pathlib import PureWindowsPath
import struct

MAX_ASSETS = 512
MAX_RESOURCE_BYTES = 64 * 1024**2
MAX_ICON_BYTES = 128 * 1024**2


def parse_icon_blob(data, base, *, following_resource_offset):
    """Check the original GR IV ICONBIN extent, without inventing pixel data.

    The 1.11 update stores this 16-byte header and exact icon pixels directly
    before RES. The header's 0x00040004 field is retained as an opaque kind;
    its camera/partition semantics are not inferred. The source container's
    checksum and SHA are verified independently by the diagnostic caller.
    """
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
           for v in (base, following_resource_offset)):
        raise ValueError('Invalid icon bundle offset')
    if base+16 > len(data): raise ValueError('Truncated icon bundle header')
    magic, kind, size = struct.unpack_from('<8sII', data, base)
    if magic != b'ICONBIN\0' or kind != 0x00040004:
        raise ValueError('Unknown original icon bundle header')
    start, end = base+16, base+16+size
    if not 0 < size <= MAX_ICON_BYTES or size%4 or end > len(data):
        raise ValueError('Original icon bundle extent is invalid')
    if end != following_resource_offset or end+16 > len(data):
        raise ValueError('Original icon bundle does not end at the resource boundary')
    if bytes(data[end:end+12]) != b'RES\0\0\0\0\0\x06\x00\xff\xff':
        raise ValueError('Original icon bundle is not followed by the verified RES header')
    return {'header_offset':base,'header_kind':hex(kind),'payload_offset':start,
            'size':size,'end_offset':end,
            'sha256':hashlib.sha256(data[start:end]).hexdigest(),
            'verification':'Original ICONBIN 16-byte header, bounded word-aligned extent ending exactly at the independently verified RES header; complete source checksum/SHA checked separately'}


def verify_griv_icon_descriptors(original_rom, icon_info):
    """Validate all native 1.11 icon descriptors against the actual blob.

    Original reset copies initialized data from ROM+0x137b640 to 0x55000000.
    The native getter at 0x533ef604 uses the 663-pointer table 0x55013410.
    Original GlyphDraw 0x5323e518 scales each descriptor offset by four.
    Addresses are metadata only and never executable or host path names.
    """
    table = 0x137b640+0x13410
    if table+663*4 > len(original_rom):
        raise ValueError('Original icon descriptor table is truncated')
    rows, maximum, live = [], 0, 0
    for index in range(663):
        address = struct.unpack_from('<I', original_rom, table+index*4)[0]
        offset = address-0x53000000
        if offset < 0 or offset+12 > len(original_rom):
            raise ValueError('Original icon descriptor pointer is outside the validated ROM')
        enabled, flag, width, height, reserved, pixel_offset = struct.unpack_from('<BBHHHI', original_rom, offset)
        if enabled not in (0,1) or flag or reserved:
            raise ValueError('Original icon descriptor has unsupported flags')
        start, end = pixel_offset*4, pixel_offset*4+width*height*4
        if enabled:
            if not 0 < width <= 2048 or not 0 < height <= 2048 or end > icon_info['size']:
                raise ValueError('Original icon descriptor exceeds real icon pixels')
            maximum=max(maximum,end);live+=1
        elif index or width or height or pixel_offset:
            raise ValueError('Unknown disabled original icon descriptor')
        rows.append({'id':index,'metadata_address':hex(address),'enabled':enabled,
                     'width':width,'height':height,'pixel_offset':pixel_offset,
                     'byte_offset':start,'byte_end':end})
    if live != 662 or maximum != icon_info['size']:
        raise ValueError('Original icon descriptors do not span the exact ICONBIN extent')
    return {'pointer_table':'0x55013410','count':663,'live_count':live,
            'maximum_byte_extent':maximum,'all_extents_within_original_blob':True,
            'format':'Original four-byte pixels, little-endian memory RGBA in the native format-5 blitter',
            'rows':rows}


def readonly_icon_image(data, icon_info):
    """Freeze exact pixels in a word-addressed logical storage image."""
    start,size=icon_info['payload_offset'],icon_info['size']
    if (start < 16 or not 0 < size <= MAX_ICON_BYTES or size%4
            or start+size > len(data) or 0x52000000+16+size > 0x53000000):
        raise ValueError('Original icon staging extent is invalid')
    content=bytes(data[start:start+size])
    if hashlib.sha256(content).hexdigest()!=icon_info['sha256']:
        raise ValueError('Original icon staging bytes changed')
    return struct.pack('<4I',0x4e4f4349,1,size,4)+content


def parse_bundle(data, base, *, require_payload_tail=False):
    if isinstance(base, bool) or not isinstance(base, int) or base < 0:
        raise ValueError('Invalid resource bundle offset')
    if base+16 > len(data): raise ValueError('Truncated resource header')
    magic, kind, count = struct.unpack_from('<8sII', data, base)
    if magic != b'RES\0\0\0\0\0' or kind != 0xffff0006:
        raise ValueError('Unknown Ricoh resource header')
    if not 1 <= count <= MAX_ASSETS: raise ValueError('Invalid resource item count')
    cursor, total, names, items = base+16, 0, set(), []
    for index in range(count):
        if cursor+36 > len(data): raise ValueError('Truncated resource record')
        field = bytes(data[cursor:cursor+32])
        zero = field.find(b'\0')
        if zero < 1 or field[zero:] != b'\0'*(32-zero):
            raise ValueError('Resource path must be NUL padded within 32 bytes')
        try: name = field[:zero].decode('ascii')
        except UnicodeError as error: raise ValueError('Resource path is not ASCII') from error
        path = PureWindowsPath(name)
        if (path.drive != 'A:' or path.root != '\\' or len(path.parts) < 3
                or any(part in ('.', '..') for part in name.split('\\'))
                or '/' in name or ':' in name[2:] or any(ord(c) < 32 for c in name)):
            raise ValueError('Invalid Ricoh resource path')
        canonical = str(path).casefold()
        if canonical in names: raise ValueError('Duplicate Ricoh resource path')
        names.add(canonical)
        size = struct.unpack_from('<I', data, cursor+32)[0]
        start = cursor+36
        if size == 0 or start+size > len(data): raise ValueError('Resource extent is outside payload')
        total += size
        if total > MAX_RESOURCE_BYTES: raise ValueError('Resource bundle exceeds 64 MiB limit')
        content = bytes(data[start:start+size])
        fmt = 'binary'
        if name.casefold().endswith('.jpg'):
            if not content.startswith(b'\xff\xd8') or not content.endswith(b'\xff\xd9'):
                raise ValueError('Resource JPEG extent is incomplete')
            fmt = 'jpeg'
        elif name.casefold().endswith('.wav'):
            if (len(content) < 12 or content[:4] != b'RIFF' or content[8:12] != b'WAVE'
                    or struct.unpack_from('<I', content, 4)[0]+8 != size):
                raise ValueError('Resource WAVE length is incomplete')
            fmt = 'riff-wave'
        elif name.casefold().split('\\')[-1].startswith('le_'):
            if len(content) < 16 or content[:4] != b'LELR' or struct.unpack_from('<I', content, 8)[0] != size:
                raise ValueError('Resource LE font length is incomplete')
            fmt = 'ricoh-le-font'
        elif '\\font\\rt_' in canonical:
            fmt = 'ricoh-rt-font'
        items.append({'index': index, 'path': name, 'payload_offset': start, 'size': size,
                      'format': fmt, 'sha256': hashlib.sha256(content).hexdigest()})
        cursor = start+size
    if require_payload_tail:
        padding = (-cursor)&3
        if cursor+padding+4 != len(data) or bytes(data[cursor:cursor+padding]) != b'\0'*padding:
            raise ValueError('Resource bundle does not end at payload checksum trailer')
    return {'header_offset': base, 'header_kind': hex(kind), 'count': count,
            'end_offset': cursor, 'total_file_bytes': total, 'items': items,
            'verification': 'complete bounded record chain, unique paths, JPEG/RIFF/LE-font extents; source payload checksum and SHA must be checked separately'}


def readonly_image(data, bundle, load=0x54400000):
    """Build guest descriptors and copy only already verified original bytes."""
    count = bundle['count']
    if not 1 <= count <= MAX_ASSETS or count != len(bundle['items']):
        raise ValueError('Invalid resource index')
    image = bytearray(struct.pack('<4I', count, 1, 0, 0)+bytes(count*12))
    placements = []
    for i, item in enumerate(bundle['items']):
        start, size = item['payload_offset'], item['size']
        if start < 0 or size <= 0 or start+size > len(data):
            raise ValueError('Resource indexed extent changed')
        content = bytes(data[start:start+size])
        if hashlib.sha256(content).hexdigest() != item['sha256']:
            raise ValueError('Resource indexed bytes changed')
        name = item['path'].encode('ascii')+b'\0'
        name_at = load+len(image); image.extend(name)
        image.extend(bytes((-len(image))&3))
        content_at = load+len(image); image.extend(content)
        image.extend(bytes((-len(image))&3))
        struct.pack_into('<3I', image, 16+i*12, name_at, content_at, size)
        placements.append(dict(item, guest_name_address=hex(name_at), guest_data_address=hex(content_at)))
    if load+len(image) > 0x55000000:
        raise ValueError('Resource image would overlap original initialized data')
    return bytes(image), placements
