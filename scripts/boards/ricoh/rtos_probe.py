# SPDX-License-Identifier: GPL-2.0-or-later
"""Finite GR IV original RTOS reset trace in a separately built QEMU board.

Run inside Linux/WSL. User firmware files stay unchanged; opt-in auxiliary
startup experiments use separately hashed, owned execution snapshots. The
auxiliary TopMenu route verifies original pixels and native Up/Down input;
normal camera power-on and the separate service-core Linux lab are not run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import time
import zlib

PAYLOAD_SHA256 = 'c4e597c7c9ca1bc181b30e35139ed90a4fd876ddcca14f12be0e00b6702233ae'
RTOS_SHA256 = 'a9a9a51530c35819df47dee1cce9f4f560a17eeed1da258950167d282884fce1'
BOARD_SHA256 = '0afe2fd9c3b04846507b1a4c417b38efc5646951c00d982ef5dc39daf24839fd'
ARCHIVE_SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
MAX_LOG = 4 * 1024**2


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''): result.update(block)
    return result.hexdigest()


def no_links(path):
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink(): raise ValueError('RTOS probe paths cannot contain symbolic links')
    return path


def regular(path):
    path = no_links(path)
    if not stat.S_ISREG(path.stat().st_mode): raise ValueError('RTOS probe input must be a regular file')
    return path


def diagnostic_sric_failure_image(original):
    """Skip unavailable peripheral waits with their documented false result.

    This modifies only the new owned execution image. It cannot establish
    real sensor/SRIC support, and never changes menu, drawing or font code.
    """
    expected = bytes.fromhex('0dc0a0e17110ffe6')
    replacement = bytes.fromhex('0000a0e31eff2fe1')  # mov r0,#0; bx lr
    patched = bytearray(original)
    changes = []
    for address in (0x53961ed4, 0x53961f5c):
        offset = address - 0x53000000
        if original[offset:offset+8] != expected:
            raise ValueError('Original SRIC wait function bytes do not match reviewed GR IV code')
        patched[offset:offset+8] = replacement
        changes.append({'address': hex(address), 'size': 8,
                        'original_bytes': expected.hex(), 'execution_bytes': replacement.hex(),
                        'semantic': 'SricM343PeripheralController GPIO wait returns false (failure); no ready/ACK is generated'})
    return bytes(patched), changes


def diagnostic_main_ui_image(original, direct=False, domain_init=False):
    """Enter an existing MainTask boot branch without claiming hardware ready.

    The original branch calls the original service/UI singleton factories.
    Only its preceding event wait and mode selection change in this snapshot;
    menu, canvas, font and drawing instructions are never patched.
    """
    address = 0x53a7881c
    expected = bytes.fromhex('1a0c00eb7420d4e5')
    replacement = (bytes.fromhex('0328dceb230000ea') if direct
                   else bytes.fromhex('0000a0e10520a0e3'))
    offset = address - 0x53000000
    if original[offset:offset+8] != expected:
        raise ValueError('Original MainTask boot wait/selector bytes do not match reviewed GR IV code')
    patched = bytearray(original)
    patched[offset:offset+8] = replacement
    changes = [{'address': hex(address), 'size': 8,
                            'original_bytes': expected.hex(), 'execution_bytes': replacement.hex(),
                            'semantic': ('Auxiliary MainTask directly calls original UI singleton/factory entry 0x53182830, then exits through original MainTask epilogue at 0x53a788b4; hardware startup/cleanup flow is bypassed and no event or peripheral acknowledgement is injected' if direct else 'Auxiliary MainTask entry bypasses three hardware event waits and selects existing boot mode 5; no event or peripheral acknowledgement is injected')}]
    if domain_init:
        if not direct: raise ValueError('Original domain initialization requires direct original UI entry')
        scratch = 0x543d3000
        if len(original) > scratch-0x53000000:
            raise ValueError('Diagnostic trampoline would overlap supplied original bytes')
        # An authored call bridge invokes the original DomainDataFactory
        # singleton and its real Initialize virtual function before the UI.
        # No domain values, persistent settings or camera replies are injected.
        words = [0xe92d4010, 0xe59f3020, 0xe12fff33, 0xe1a04000,
                 0xe5903000, 0xe5933008, 0xe12fff33, 0xe3a00000,
                 0xe59f3008, 0xe12fff33, 0xe8bd8010,
                 0x53a81480, 0x53182830]
        trampoline = struct.pack('<13I', *words)
        patched.extend(b'\0'*(scratch-0x53000000-len(patched)))
        patched.extend(trampoline)
        branch = 0xeb000000 | (((scratch-address-8)//4)&0xffffff)
        patched[offset:offset+4] = struct.pack('<I', branch)
        changes[0]['execution_bytes'] = patched[offset:offset+8].hex()
        changes[0]['semantic'] = 'Auxiliary MainTask calls recorded original DomainDataFactory initialization bridge then original UI factory, then original task epilogue; hardware startup/cleanup sequence is bypassed'
        changes.append({'address': hex(scratch), 'size': len(trampoline),
                        'original_bytes': None, 'execution_bytes': trampoline.hex(),
                        'semantic': 'Authored auxiliary call bridge in new snapshot extension: original singleton 0x53a81480, original Initialize virtual method +8 (0x53a80a70), original UI factory 0x53182830; no fabricated settings or peripheral responses'})
    return bytes(patched), changes


def diagnostic_empty_domain_image(original):
    """Let original callers handle missing persistent settings as missing.

    Original 0x53a7f374 returns NULL when its file is absent. The owned
    development snapshot uses that negative result because the filesystem
    package is absent, instead of dereferencing its null package object.
    """
    expected = bytes.fromhex('0dc0a0e13c20a0e3')
    replacement = bytes.fromhex('0000a0e31eff2fe1')
    patched = bytearray(original); changes = []
    for address, semantic in ((0x53a7f374, 'file load returns NULL (missing file)'),
                              (0x53a7ec70, 'file existence check returns false (missing file)')):
        offset = address-0x53000000
        if original[offset:offset+8] != expected:
            raise ValueError('Original DomainDataStorage missing-file function bytes do not match')
        patched[offset:offset+8] = replacement
        changes.append({'address': hex(address), 'size': 8,
                        'original_bytes': expected.hex(), 'execution_bytes': replacement.hex(),
                        'semantic': 'DomainDataStorage '+semantic+', an original negative result; no persistent settings, camera state or successful read is fabricated'})
    return bytes(patched), changes


def diagnostic_null_filesystem_image(original):
    """A missing filesystem yields missing file; nonnull original code runs."""
    branch = lambda src, target: 0xea000000 | (((target-src-8)//4)&0xffffff)
    patched = bytearray(original); changes = []
    for address, scratch, expected_hex, label in (
            (0x5383b22c, 0x543d3100, 'c83801e30dc0a0e1', 'Resource file size'),
            (0x5383a3c4, 0x543d3200, '0dc0a0e1f0df2de9', 'File/cache lookup')):
        expected = bytes.fromhex(expected_hex); offset = address-0x53000000
        if original[offset:offset+8] != expected:
            raise ValueError('Original filesystem method bytes do not match reviewed GR IV code')
        if len(patched) > scratch-0x53000000:
            raise ValueError('Filesystem call guard would overlap supplied execution bytes')
        words = [0xe3500000, 0x012fff1e, *struct.unpack('<2I', expected),
                 branch(scratch+16, address+8)]
        bridge = struct.pack('<5I', *words)
        replacement = struct.pack('<I', branch(address, scratch))
        patched[offset:offset+4] = replacement
        patched.extend(b'\0'*(scratch-0x53000000-len(patched))); patched.extend(bridge)
        changes.extend([
            {'address': hex(address), 'size': 4, 'original_bytes': expected[:4].hex(),
             'execution_bytes': replacement.hex(),
             'semantic': label+' redirects to a recorded NULL filesystem guard; missing backend returns original NULL missing-file result, nonnull backend executes displaced original prologue and remaining original function'},
            {'address': hex(scratch), 'size': len(bridge), 'original_bytes': None,
             'execution_bytes': bridge.hex(),
             'semantic': 'Authored auxiliary guard: CMP r0,#0; BXEQ lr; original two prologue instructions; B original+8. No successful read or file contents are generated'}])
    return bytes(patched), changes


def diagnostic_missing_settings_objects_image(original):
    """Return failure only for absent non-GUI persistent-setting objects."""
    patched=bytearray(original);changes=[]
    branch=lambda src,dst:0xea000000|(((dst-src-8)//4)&0xffffff)
    for site,scratch,expected,continuation in (
            (0x537493c8,0x543d3300,bytes.fromhex('0dc0a0e10010a0e3'),0x537493d0),
            (0x53749db8,0x543d3340,bytes.fromhex('0c0090e594feffea'),0x53749814)):
        offset=site-0x53000000
        if patched[offset:offset+8]!=expected:raise ValueError('Original missing-settings method bytes mismatch')
        displaced=list(struct.unpack('<2I',expected)) if site==0x537493c8 else [0xe590000c]
        words=[0xe3500000,0x012fff1e,*displaced,branch(scratch+8+4*len(displaced),continuation)]
        guard=struct.pack('<'+'I'*len(words),*words)
        if len(patched)>scratch-0x53000000:raise ValueError('Missing-settings guard overlaps snapshot')
        patched.extend(bytes(scratch-0x53000000-len(patched)));patched.extend(guard)
        replacement=struct.pack('<I',branch(site,scratch));patched[offset:offset+4]=replacement
        changes.extend([{'address':hex(site),'size':4,'original_bytes':expected[:4].hex(),'execution_bytes':replacement.hex(),
                         'semantic':'Absent persistent-settings object (r0=NULL) returns zero/failure; nonnull object uses original instructions and continuation. No settings, ready state, font or GUI instruction is replaced'},
                        {'address':hex(scratch),'size':len(guard),'original_bytes':None,'execution_bytes':guard.hex(),
                         'semantic':'Authored NULL-only persistent-settings guard; PC-relative original tail branch is relocated to its original absolute target'}])
    site=0x53a91bc0;offset=site-0x53000000
    expected=bytes.fromhex('0dc0a0e1f0db2de9');replacement=bytes.fromhex('0000a0e31eff2fe1')
    if patched[offset:offset+8]!=expected:raise ValueError('Original SystemControl event handler bytes mismatch')
    patched[offset:offset+8]=replacement
    changes.append({'address':hex(site),'size':8,'original_bytes':expected.hex(),'execution_bytes':replacement.hex(),
                    'semantic':'Isolated software-display component skips non-GUI SystemControl hardware/NV event dispatch; returns zero and sends no acknowledgement. Original message queues, TopMenu/Canvas/font code and source firmware stay unchanged. This cannot represent a normally booted camera or functioning hardware business state'})
    return bytes(patched),changes


def capture_native_canvas(stream, output):
    """Read original TopMenu/Canvas/surface objects; never generate UI pixels."""
    def save(address, size, name):
        target=output/name
        stream.write((json.dumps({'execute':'human-monitor-command','arguments':{
            'command-line':f'pmemsave {address:#x} {size} '+json.dumps(str(target))}})+'\n').encode())
        while True:
            reply=json.loads(stream.readline())
            if 'error' in reply:raise ValueError('QMP canvas memory capture failed')
            if 'return' in reply:break
        if not target.is_file() or target.stat().st_size!=size:raise ValueError('Incomplete original canvas memory capture')
        return target.read_bytes()
    def ram(p,n):
        return n>=0 and ((0x40000000<=p and p+n<=0x80000000) or (0xa0000000<=p and p+n<=0xe0000000))
    result={'scope':'Original TopMenu software-display component; original camera power-on and physical LCD are not verified','synthetic_pixels':False}
    controller=struct.unpack('<I',save(0x55080dc0,4,'original-topmenu-pointer.bin'))[0]
    datarom=struct.unpack('<I',save(0x5538c510,4,'component-datarom.bin'))[0]
    if ram(datarom,28):
        storage=struct.unpack_from('<I',save(datarom,28,'component-datarom-object.bin'),8)[0]
        if ram(storage,48):
            state=struct.unpack('<12I',save(storage,48,'component-mailbox.bin'))
            if state[9]:controller=state[9]
            result['native_current_id']=hex(state[11])

    result['controller']=hex(controller)
    if not ram(controller,0x13ec):return {**result,'blocker':'Original TopMenuController is not allocated'}
    header=save(controller,0x158,'original-topmenu-header.bin')
    result['controller_return_id']=hex(struct.unpack_from('<I',header,0x150)[0])
    canvas=struct.unpack_from('<I',header,4)[0]
    result['canvas']=hex(canvas)
    if not ram(canvas,0xf0):return {**result,'blocker':'Original TopMenu primary Canvas is absent'}
    canvas_header=save(canvas,0xf0,'original-canvas-header.bin')
    result['canvas_bounds']=list(struct.unpack_from('<4i',canvas_header,0x20))
    result['canvas_clip']=list(struct.unpack_from('<4i',canvas_header,0x54))
    begin,end=struct.unpack_from('<2I',canvas_header,0xc)
    if end>=begin and (end-begin)%4==0 and 0<(end-begin)//4<=64 and ram(begin,end-begin):
        children=struct.unpack('<'+'I'*((end-begin)//4),save(begin,end-begin,'original-canvas-child-pointers.bin'))
        result['children']=[]
        for i,child in enumerate(children):
            if not ram(child,0xa4):
                result['children'].append({'index':i,'pointer':hex(child),'invalid':True});continue
            h=save(child,0xa4,f'original-canvas-child-{i:02d}.bin')
            result['children'].append({'index':i,'pointer':hex(child),'vtable':hex(struct.unpack_from('<I',h)[0]),
                                       'bounds':list(struct.unpack_from('<4i',h,0x20)),
                                       'clip':list(struct.unpack_from('<4i',h,0x54)),
                                       'visible':h[0x9e],'dirty':h[0xa0]})
    surface=struct.unpack_from('<I',canvas_header,0xec)[0]
    result.update(canvas_visible=canvas_header[0x9e],canvas_dirty=canvas_header[0xa0],surface=hex(surface))
    if not ram(surface,40):return {**result,'blocker':'Original Canvas has no persistent DrawSurface'}
    descriptor=save(surface,40,'original-draw-surface.bin')
    pixels=struct.unpack_from('<I',descriptor,8)[0]
    pitch,unknown,width,height=struct.unpack_from('<4H',descriptor,0x18)
    result.update(pixels=hex(pixels),valid=descriptor[0],format=descriptor[2],pitch_pixels=pitch,width=width,height=height)
    size=pitch*height*4
    if descriptor[2]!=5 or not descriptor[0] or not(0<width<=pitch<=2048 and 0<height<=2048) or size>16*1024**2 or not ram(pixels,size):
        return {**result,'blocker':'Original software DrawSurface is invalid or unsupported'}
    raw=save(pixels,size,'original-canvas-rgba.bin')
    rows=b''.join(b'\0'+raw[y*pitch*4:y*pitch*4+width*4] for y in range(height))
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')
    (output/'original-canvas.png').write_bytes(png)
    colors=set(struct.iter_unpack('<I',raw))
    result.update(bytes=size,raw_sha256=hashlib.sha256(raw).hexdigest(),png_sha256=hashlib.sha256(png).hexdigest(),distinct_colors=len(colors),nonzero_bytes=sum(bool(b) for b in raw),frame_path=str(output/'original-canvas.png'))
    return result


def capture_resource_reads(stream, output, resource_info, payload_bytes):
    """Pause the owned guest and hash actual destination RAM after real reads."""
    def request(value):
        stream.write((json.dumps(value)+'\n').encode())
        while True:
            response=json.loads(stream.readline())
            if 'return' in response or 'error' in response: return response
    request({'execute':'stop'})
    def save(address, size, name):
        path=output/name
        result=request({'execute':'human-monitor-command','arguments':{'command-line':
                       f'pmemsave {address:#x} {size} '+json.dumps(str(path))}})
        if 'error' in result or not path.is_file() or path.stat().st_size != size:
            raise ValueError('QMP did not capture the requested guest memory extent')
        return path.read_bytes()
    ram = lambda p,n: n>=0 and ((0x40000000<=p and p+n<=0x80000000) or (0xa0000000<=p and p+n<=0xe0000000))
    backend=struct.unpack('<I',save(0x5537df40,4,'resource-backend-pointer.bin'))[0]
    if not ram(backend,8): raise ValueError('Original factory does not reference the owned RAM backend')
    vtable,state=struct.unpack('<2I',save(backend,8,'resource-backend-object.bin'))
    if not ram(state,1028): raise ValueError('Backend telemetry state is outside the development RAM banks')
    telemetry=save(state,1028,'resource-read-telemetry.bin')
    count=struct.unpack_from('<I',telemetry)[0]
    if count>64: raise ValueError('Resource telemetry count exceeds bounded backend limit')
    records=[]
    for i in range(count):
        item_id,destination,size,file_offset=struct.unpack_from('<4I',telemetry,4+i*16)
        if item_id>=resource_info['count']: raise ValueError('Resource telemetry file index is invalid')
        item=resource_info['items'][item_id]
        if size>item['size']-file_offset or not ram(destination,size):
            raise ValueError('Resource telemetry destination/extent is invalid')
        captured=save(destination,size,f'original-resource-read-{i:02d}.bin')
        expected=payload_bytes[item['payload_offset']+file_offset:item['payload_offset']+file_offset+size]
        records.append({'path':item['path'],'bytes':size,'destination':hex(destination),
                        'file_offset':file_offset,'source_sha256':hashlib.sha256(expected).hexdigest(),
                        'captured_destination_sha256':hashlib.sha256(captured).hexdigest(),
                        'destination_matches_original_bytes':captured==expected,
                        'note':'Captured after pausing all CPUs; original consumer may have subsequently reused a destination buffer'})
    return {'backend':hex(backend),'vtable':hex(vtable),'state':hex(state),'read_count':count,
            'reads':records,'guest_paused_for_capture':True}


def capture_icon_read(stream, output, icon_info):
    """Hash original native IconBinaryData after the real logical read."""
    def save(address,size,name):
        target=output/name
        stream.write((json.dumps({'execute':'human-monitor-command','arguments':{
            'command-line':f'pmemsave {address:#x} {size} '+json.dumps(str(target))}})+'\n').encode())
        while True:
            reply=json.loads(stream.readline())
            if 'error' in reply:raise ValueError('Original icon memory capture failed')
            if 'return' in reply:break
        if not target.is_file() or target.stat().st_size!=size:raise ValueError('Incomplete original icon memory capture')
        return target.read_bytes()
    ram=lambda p,n:n>=0 and ((0x40000000<=p and p+n<=0x80000000) or (0xa0000000<=p and p+n<=0xe0000000))
    table=save(0x55019ddc,15*16,'original-memory-area-table.bin')
    name,capacity,pixels,status=struct.unpack_from('<4I',table,2*16)
    result={'item':2,'name':'IconBinaryData','capacity':capacity,'pixels':hex(pixels),
            'status_pointer':hex(status),'source_sha256':icon_info['sha256'],
            'synthetic_icon_pixels':False,'logical_block_bytes':4}
    if not ram(status,4):return {**result,'blocker':'Original icon validity pointer is invalid'}
    valid=struct.unpack('<I',save(status,4,'original-icon-validity.bin'))[0]
    result['original_validity_word']=hex(valid)
    if valid!=0xa55a5aa5 or capacity!=icon_info['size'] or not ram(pixels,capacity):
        return {**result,'blocker':'Original SystemControl has not loaded and marked the real icon extent valid'}
    raw=save(pixels,capacity,'original-icon-loaded-pixels.bin')
    result.update(bytes=capacity,captured_sha256=hashlib.sha256(raw).hexdigest(),
                  destination_matches_original_bytes=hashlib.sha256(raw).hexdigest()==icon_info['sha256'])
    datarom=struct.unpack('<I',save(0x5538c510,4,'original-icon-datarom-pointer.bin'))[0]
    if not ram(datarom,28):raise ValueError('Original icon DataRom object is invalid')
    descriptor=save(datarom,28,'original-icon-datarom-object.bin')
    storage=struct.unpack_from('<I',descriptor,8)[0]
    if not ram(storage,20):raise ValueError('Original icon read-only storage object is invalid')
    vt,reads,destination,readbytes,start=struct.unpack('<5I',save(storage,20,'original-icon-storage-telemetry.bin'))
    result['actual_block_read']={'storage':hex(storage),'vtable':hex(vt),'read_count':reads,
                                'destination':hex(destination),'bytes':readbytes,'start_block':start,
                                'completed_exact_source_read':reads>0 and destination==pixels and readbytes==capacity and start==0}
    return result


def probe(payload, manifest, output, seconds=5, bootloader_gic_flag=False,
          experimental_latches=False, experimental_gate_idle=False,
          experimental_memory_layout=False, diagnostic_skip_sric_wait=False,
          diagnostic_main_ui=False, diagnostic_gui_entry=False,
          diagnostic_domain_init=False, diagnostic_empty_domain_storage=False,
          diagnostic_resources=False, diagnostic_ui_component=False, viewer_port=None,
          *, ui_platform=None, stop_event=None, on_ready=None, startup_budget=None,
          session_seconds=900, finish_after_ui=False):
    if os.name != 'posix': raise ValueError('Run the RTOS probe inside Linux or WSL')
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 60:
        raise ValueError('RTOS probe duration must be 1..60 seconds')
    if startup_budget is not None and (type(startup_budget) is not int or not 90<=startup_budget<=180):
        raise ValueError('Original menu startup budget must be 90..180 seconds')
    if type(session_seconds) is not int or not 30<=session_seconds<=3600:
        raise ValueError('Original menu session must be 30..3600 seconds')
    if experimental_gate_idle and not experimental_latches:
        raise ValueError('Diagnostic gate-idle hypothesis requires experimental latches')
    if diagnostic_skip_sric_wait and not (experimental_latches and experimental_memory_layout):
        raise ValueError('SRIC failure-return diagnostic requires experimental latches and memory layout')
    if (diagnostic_main_ui or diagnostic_gui_entry) and not (experimental_latches and experimental_memory_layout):
        raise ValueError('Auxiliary MainTask diagnostic requires experimental latches and memory layout')
    if diagnostic_main_ui and diagnostic_gui_entry:
        raise ValueError('Choose either original boot mode 5 or direct original UI entry')
    if diagnostic_domain_init and not diagnostic_gui_entry:
        raise ValueError('Original domain initialization requires direct original UI entry')
    if diagnostic_empty_domain_storage and not diagnostic_domain_init:
        raise ValueError('Empty domain storage diagnostic requires original domain initialization')
    if diagnostic_resources and not diagnostic_empty_domain_storage:
        raise ValueError('Read-only original resources require original DomainData initialization and missing settings adapter')
    if diagnostic_ui_component and not diagnostic_resources:
        raise ValueError('Original UI component requires read-only original resources')
    if viewer_port is not None and (isinstance(viewer_port,bool) or not isinstance(viewer_port,int) or not 1024<=viewer_port<=65535 or not diagnostic_ui_component):
        raise ValueError('Original UI viewer requires the component profile and a local port1024..65535')
    payload, manifest, output = regular(Path(payload)), regular(Path(manifest)), no_links(Path(output))
    build = json.loads(manifest.read_text())
    if (build.get('qemu_version') != '10.2.4' or build.get('machine') != 'ricoh-rtos-lab'
            or build.get('upstream_archive_sha256') != ARCHIVE_SHA256
            or build.get('sources', {}).get('ricoh-rtos-lab.c') != BOARD_SHA256):
        raise ValueError('Build the reviewed separate Ricoh RTOS board first')
    binary = regular(Path(build['binary']))
    if digest(binary) != build.get('binary_sha256'): raise ValueError('RTOS QEMU binary hash mismatch')
    if payload.stat().st_size != 67948280 or digest(payload) != PAYLOAD_SHA256:
        raise ValueError('RTOS probe requires the exact unpacked official GR IV 1.11 payload')
    # Original reset literals copy initialized data from 0x5437b640, length
    # 0x57480. This establishes the needed ROM prefix extent for reset tracing;
    # it does not establish a full standalone RTOS update-partition boundary.
    offset, load = 0x17d10, 0x53000000
    extent = 0x5437b640 + 0x57480 - load
    with payload.open('rb') as stream:
        stream.seek(offset); original = stream.read(extent)
    if len(original) != extent or hashlib.sha256(original).hexdigest() != RTOS_SHA256:
        raise ValueError('Original RTOS reset ROM prefix changed')
    execution, adaptations = (diagnostic_sric_failure_image(original) if diagnostic_skip_sric_wait
                              else (original, []))
    if diagnostic_main_ui or diagnostic_gui_entry:
        execution, main_changes = diagnostic_main_ui_image(execution, diagnostic_gui_entry,
                                                           diagnostic_domain_init)
        adaptations.extend(main_changes)
    if diagnostic_empty_domain_storage:
        execution, storage_changes = diagnostic_empty_domain_image(execution)
        adaptations.extend(storage_changes)
        execution, guard_changes = diagnostic_null_filesystem_image(execution)
        adaptations.extend(guard_changes)
    if diagnostic_ui_component:
        execution, settings_changes=diagnostic_missing_settings_objects_image(execution)
        adaptations.extend(settings_changes)
    output.mkdir(parents=True, exist_ok=False)
    resource_image = resource_info = resource_build = icon_image = icon_info = None
    if diagnostic_resources:
        from resources import parse_bundle, readonly_image, parse_icon_blob, verify_griv_icon_descriptors, readonly_icon_image
        from resource_boot import build_bridge, attach
        resource_payload = payload.read_bytes()
        if hashlib.sha256(resource_payload).hexdigest() != PAYLOAD_SHA256:
            raise ValueError('Source payload changed before resource snapshot creation')
        resource_info = parse_bundle(resource_payload, 0x3c62f50, require_payload_tail=True)
        resource_bytes, placements = readonly_image(resource_payload, resource_info)
        resource_info['placements'] = placements
        resource_info['image_sha256'] = hashlib.sha256(resource_bytes).hexdigest()
        resource_image = output/'original-readonly-resources.bin'
        resource_image.write_bytes(resource_bytes)
        if diagnostic_ui_component:
            icon_info=parse_icon_blob(resource_payload,0x2e93ae0,following_resource_offset=resource_info['header_offset'])
            icon_info['native_descriptors']=verify_griv_icon_descriptors(original,icon_info)
            icon_bytes=readonly_icon_image(resource_payload,icon_info)
            icon_info['image_sha256']=hashlib.sha256(icon_bytes).hexdigest()
            icon_image=output/'original-readonly-icons.bin';icon_image.write_bytes(icon_bytes)
            (output/'original-readonly-icons.json').write_text(json.dumps(icon_info,indent=2)+'\n')
        if ui_platform is None:
            bridge, backend, resource_build = build_bridge(output,diagnostic_ui_component,icon_info)
        else:
            bridge, backend, resource_build = ui_platform
        execution, resource_changes = attach(execution, bridge, backend)
        adaptations.extend(resource_changes)
        (output/'original-readonly-resources.json').write_text(json.dumps(resource_info,indent=2)+'\n')
    execution_sha = hashlib.sha256(execution).hexdigest()
    image = output / 'original-rtos-reset-rom.bin'; image.write_bytes(original)
    if adaptations:
        image = output / 'diagnostic-rtos-execution.bin'; image.write_bytes(execution)
    record = {'schema_version': 1, 'model': 'Ricoh GR IV', 'firmware': '1.11',
              'build': build, 'source_payload_sha256': PAYLOAD_SHA256,
              'rtos_source_offset': offset, 'rtos_load_address': load,
              'rtos_extent': extent, 'rtos_sha256': RTOS_SHA256,
              'seconds': seconds, 'firmware_modified': bool(adaptations),
              'source_firmware_modified': False, 'execution_image_sha256': execution_sha,
              'execution_adaptations': adaptations, 'original_gui_code_modified': False,
              'probe_source_sha256': digest(Path(__file__)),
              'original_ui_verified': False, 'natural_power_on_verified': False,
              'camera_access': False, 'host_network_exposed': False,
              'experimental_latches': experimental_latches,
              'experimental_gate_idle': experimental_gate_idle,
              'experimental_memory_layout': experimental_memory_layout,
              'diagnostic_skip_sric_wait': diagnostic_skip_sric_wait,
              'diagnostic_main_ui': diagnostic_main_ui,
              'diagnostic_gui_entry': diagnostic_gui_entry,
              'diagnostic_domain_init': diagnostic_domain_init,
              'diagnostic_empty_domain_storage': diagnostic_empty_domain_storage,
              'diagnostic_resources': diagnostic_resources,
              'diagnostic_ui_component': diagnostic_ui_component,
              'original_resources': resource_info, 'resource_backend_build': resource_build,'original_icon_blob':icon_info,
              'interventions': [
                  'Three secure Cortex-A7 cores start at the original vector address; the camera Linux core is absent.',
                  '512 MiB development RAM at 0x40000000; zeroed SRAM at 0x01000000. The original guest publishes its own rendezvous values.',
                  'GIC at 0x1d000000; actual virtual countdown timer 13 at 0x1e0000d0, 6.75 MHz, SPI 99.',
                  'ARM CNTControlBase at 0x1d010000 uses a real 40 MHz elapsed-time counter. Explicit development bootloader configuration starts CNTCR.EN=1, matching the always-on Generic Timer in Ricoh GPL sc2000.dtsi; real-camera bootloader configuration remains unverified.',
                  'USB3_JT_ADJ at 0x1b110298 retains guest-written configuration only; its unknown reset value remains an error on reads before the first write. Official GR IIIx GPL dwc3-sn.c identifies offset 0x18 in the USB3 glue register block.',
                  'No camera IPCU replies, display, storage, sensor, USB transfers or network peripherals are implemented.',
                  'Original ROM prefix loaded at 0x53000000; its original MMU table, initialized data and vector code are unchanged.',
                  'QMP reads CPU registers only. No device-register reads or writes are injected.']}
    record['interventions'].append('Polled USIO TX console at original 0x1de00000: SSR.TDRE/TBI reflect an empty virtual transmitter, DR writes are consumed by the local serial log. RX input and UART interrupts are absent.')
    if bootloader_gic_flag:
        record['interventions'].append('Explicit bootloader flag 0x0100fff8=0xbeefcafe selects the GIC branch already present in the original reset code; real-camera bootloader provenance remains unverified.')
    if experimental_latches:
        record['interventions'].append('DIAGNOSTIC EXIU WITH NO EXTERNAL SOURCES: documented interrupt-mask/source/polarity/edge configuration at 0x1c26f000 from GPL pinctrl-milbeaut.c and sc2000.dtsi starts at guessed zero. External GPIO inputs have no attached camera devices, so EIREQSTA/EIRAWREQSTA have no pending requests; EIREQCLR clears an empty pending set. Software interrupt requests remain unsupported errors. No button or camera IRQ event is fabricated.')
        record['interventions'].append('DIAGNOSTIC EMPTY I2C BUS: three documented SC2000 I2C controllers at 0x1e003000/0x1e003400/0x1e003800 use START/STOP/CLRST and ST mode definitions from official GR IV GPL i2c-snisc2000.c, SPI101+channel from sc2000.dtsi. Event timing uses actual virtual time with assumed 50 MHz PCLK and programmed SCL divider. Reset starts at assumed zero. Each address receives a negative acknowledgement because no slave is attached; no PMIC, touch, power, sensor or camera-device response is fabricated.')
        record['interventions'].append('DIAGNOSTIC TIMER CLOCK EXTRAPOLATION: all 16 documented reload-timer register blocks at 0x1e000000 stride 0x10 use actual virtual countdown events, SPI86+channel (GPL channel5=SPI91, original channel13=SPI99). Extrapolated 27 MHz source gives CSL0=13.5 MHz and CSL1=6.75 MHz; selectors 2/3 are rejected when enabled. Only timer13 rate has original reload/time-conversion verification; no expiry is synthesized.')
        record['interventions'].append('DIAGNOSTIC XDMAC RESET HYPOTHESIS: XDACS and four channel blocks at 0x1c250000/0x19140000 retain guest configuration, initially guessed zero. Original 0x53aace00 confirms the documented 0x30 stride / XDDES +0x28 CE bit28 from milbeaut-xdmac.c. A CE+SE transfer request is rejected; no DMA completion or IRQ is generated. These are DMA blocks, not adjacent IPCU mailboxes.')
        record['interventions'].append('DIAGNOSTIC EXSTOP RESET HYPOTHESIS: selected soft-reset/RAM power/clock/USB2 configuration words at 0x1b110000 (official GPL reset-exstop.c and original +0x60 RMW functions) retain guest writes from guessed zero reset. Additional +0x10/+0x14/+0x1c/+0x30 accesses are only decoded RMW configuration from original 0x53aada68..0x53aadee0, with unverified bit semantics. No USB2 transfers, peripheral readiness or power-device responses are generated.')
        record['interventions'].append('DIAGNOSTIC HDMAC RESET HYPOTHESIS: documented DMACR and eight DMACA/DMACB/SRC/DST channels at each of 0x1e100000/0x1e110000 plus four USB2 DMA channels at 0x1b040000 retain initial guest configuration with guessed zero reset. A channel request with EB+ST set is explicitly rejected, since no actual transfer/interrupt is implemented; no completion is invented.')
        record['interventions'].append('DIAGNOSTIC CLOCK RESET HYPOTHESIS: selected source bits at 0x1d020000 aligned offsets 0..0x20 and documented CLKSEL/PLLCNT/CLKSTOP/CRSWR/CRRRS/CRRSM words at 0x1d021000 through +0x94 retain original guest writes (CLKSTOP uses the documented paired write-mask / stop bits), starting at guessed zero. clk-mlb01.c and original 0x53a3d138/0x53a3f3e8 establish register usage; clock tree timing, PLL lock and apply acknowledgements are not synthesized.')
        record['interventions'].append('DIAGNOSTIC GPIO INPUT/RESET HYPOTHESIS: CM0 pin controller at 0x1dffb000 (48 pins) and main MLB01 GPIO at 0x1d022000 (208 pins) model documented DDR/EPCR/PUDER/PUDCR retention and PDR upper-halfword write masks from official GR IV GPL pinctrl-mlb01-cm0.c and GR IIIx GPL pinctrl-milbeaut.c. Initial configuration assumes zero; unconnected inputs follow programmed pull resistors, or assumed zero without pulls; no button, power, battery or camera pin response is represented as observed hardware.')
        record['interventions'].append('DIAGNOSTIC HYPOTHESIS ONLY: UART clock selector at 0x1dffe000 and divisor at +0x100 assume zero, selecting the original code\'s 100 MHz source / divider 1. The selector/reset wiring has not been confirmed; this does not change the independently verified 40 MHz ARM system counter.')
        record['interventions'].append('DIAGNOSTIC HYPOTHESIS ONLY: six channels at 0x1e002000 stride 0x20 retain guest configuration at offsets 4,0xc,0x14, as decoded from original 0x53ac298c. First reads assume zero; peripheral identity, reset values and interrupt/event behavior are unverified. No interrupts are generated by this block.')
        record['interventions'].append('DIAGNOSTIC HYPOTHESIS ONLY: selected configuration arrays at 0x28820000/0x28840000 and the seven register blocks in original mapping switch 0x53aca24c (0x28000000,0x28400000,0x28500000,0x28810000,0x28814000,0x28818000,0x2881c000) start with guessed zero values and retain guest writes at specific decoded RW offsets/arrays only. Reset values and peripheral identity/behavior are unverified; this cannot establish genuine camera hardware or UI boot.')
        record['interventions'].append('DIAGNOSTIC HYPOTHESIS ONLY: 4 KiB of zeroed configuration-table backing memory at each of 0x29400000,0x29500000,0x29800000 allows the original guest to clear/fill its byte tables. Physical peripheral behavior and hardware table transfer are not implemented.')
    if experimental_gate_idle:
        record['interventions'].append('DIAGNOSTIC HYPOTHESIS ONLY: after the original guest writes gate control offset 0xc with bit 0 clear, status offset 0x38 reports bit 0 set (inferred idle). This transition is an unverified behavioral model, not an observed real-camera completion.')
    if experimental_memory_layout:
        record['interventions'].append('DIAGNOSTIC MEMORY LAYOUT: replace the baseline 512 MiB with two distinct 1 GiB RAM banks at 0x40000000 and 0xa0000000. Original pool descriptors in 0x538e3c18..0x538e3c4c request a pool ending at 0x80000000 and another at 0xa0000000 of size 0x40000000; original first-level MMU entries are identity sections. Physical bank wiring/capacity is unverified; these banks are independent, not guessed aliases.')
    if diagnostic_skip_sric_wait:
        record['interventions'].append('AUXILIARY STARTUP DIAGNOSTIC: the owned execution snapshot replaces the first eight bytes of two SricM343PeripheralController GPIO wait helpers at 0x53961ed4/0x53961f5c with mov r0,#0; bx lr. They return failure, not success/ready. Original source, GUI controls, drawing and fonts are unchanged. Exact original/execution bytes and snapshot hashes are recorded; this is not an unmodified natural power-on.')
    if diagnostic_main_ui:
        record['interventions'].append('AUXILIARY MAIN TASK DIAGNOSTIC: owned snapshot changes 0x53a7881c from BL 0x53a7b88c to NOP and 0x53a78820 from LDRB r2,[r4,#0x74] to MOV r2,#5. This skips three hardware startup event waits and selects the original boot mode 5 branch, whose code calls original factories at 0x5358b678, 0x53182830, 0x535e323c, 0x536922f0. No event bits or hardware responses are fabricated; original GUI controls, canvas and fonts remain unchanged. Selection of this boot mode is an explicit development assumption, not observed power-on behavior.')
    if diagnostic_gui_entry:
        record['interventions'].append('AUXILIARY ORIGINAL UI ENTRY: owned snapshot replaces the hardware wait/mode selection at 0x53a7881c with a direct BL to original 0x53182830 and B to original MainTask epilogue at 0x53a788b4, skipping hardware cleanup as well. Original function initializes UI singleton/factory entries 0x5323ea84, 0x532400a8, 0x5323baa0, 0x53182b48. Hardware boot sequence is explicitly bypassed; no hardware event bits or successful peripheral replies are fabricated. GUI controls, canvas and fonts remain the original code. This is diagnostic component startup, not verified natural power-on.')
    if diagnostic_domain_init:
        record['interventions'].append('AUXILIARY DOMAIN INITIALIZATION: actual branch target is an authored 52-byte call bridge appended at 0x543d3000, beyond the validated original ROM prefix. It calls original DomainDataFactory singleton 0x53a81480 and original Initialize virtual method +8 (0x53a80a70), then original UI factory 0x53182830. This compensates for the skipped original hardware startup order; no settings values or positive device replies are fabricated. Exact bridge bytes and hash are recorded, original source firmware and drawing code stay unchanged.')
    if diagnostic_empty_domain_storage:
        record['interventions'].append('AUXILIARY MISSING SETTINGS STORAGE: original file-load function 0x53a7f374 has a NULL return and original existence check 0x53a7ec70 returns false for a missing file. Their owned execution snapshot returns these negative results immediately because no filesystem package exists, avoiding NULL filesystem dereferences. Original callers choose their existing missing-data paths; settings values and successful camera/storage replies are not injected. This adapter changes two eight-byte sites outside GUI drawing/menu/font code.')
        record['interventions'].append('AUXILIARY MISSING FILESYSTEM GUARDS: Resource file-size method 0x5383b22c and file/cache lookup 0x5383a3c4 redirect to two 20-byte authored guards at appended 0x543d3100/0x543d3200. Only a NULL filesystem object returns zero (missing file); nonnull objects run the displaced original prologue and remaining original code. This avoids AppMain missing-backend dereferences without inventing files, font data or successful reads. Bridge and replacement bytes are recorded separately.')
    if diagnostic_resources:
        record['interventions'].append('AUXILIARY READ-ONLY ORIGINAL RESOURCES: complete original 31-file RES bundle verified against source payload SHA, record extents and per-file SHA; exact font/image/sound bytes loaded at 0x54400000 (original identity MMU sections). Authored backend implements original stat/open/read/close ABI only; absent files and writes fail. Original Resource allocator, constructor, mutex/queue initialization and task-start code run, FileSystemFactory+0x14 points to the development backend. Original font loader, menus and pixel drawing remain unchanged. This is a development filesystem adapter, not emulated flash hardware.')
    if diagnostic_ui_component:
        record['interventions'].append('AUXILIARY READ-ONLY ICON STORAGE: original ICONBIN body is verified against its complete 16-byte header, following RES boundary and all663 native descriptors, then staged at0x52000000 outside original RTOS heap pools (59000000 onward). Authored logical storage uses4-byte blocks and exact original bytes, without NAND padding or physical flash emulation. Original DataRom methods and SystemControl0x53a8e520 perform allocation/read and mark validity only after the actual bounded copy. Development metadata exposes only real icon item4; other entries are absent. Original glyph blitters are unchanged.')
    process = control = viewer = None
    with tempfile.TemporaryDirectory(prefix='ricoh-rtos-qmp-') as temporary:
        qmp = Path(temporary) / 'qmp'
        gdb = Path(temporary) / 'gdb'
        machine = 'ricoh-rtos-lab' + (',experimental-latches=on' if experimental_latches else '')
        if experimental_gate_idle: machine += ',experimental-gate-idle=on'
        if experimental_memory_layout: machine += ',experimental-memory-layout=on'
        command = [str(binary), '-M', machine, '-smp', '3', '-m', '2048' if experimental_memory_layout else '512',
                   '-display', 'none', '-monitor', 'none', '-serial', f'file:{output/"serial.log"}', '-nic', 'none',
                   '-device', f'loader,file={image},addr={load:#x},force-raw=on']
        for cpu in range(3): command.extend(['-device', f'loader,addr={load:#x},cpu-num={cpu}'])
        if resource_image:
            command.extend(['-device', f'loader,file={resource_image},addr=0x54400000,force-raw=on'])
        if icon_image:
            command.extend(['-device',f'loader,file={icon_image},addr=0x52000000,force-raw=on'])
        if bootloader_gic_flag:
            command.extend(['-device', 'loader,addr=0x0100fff8,data=0xbeefcafe,data-len=4'])
        command.extend(['-no-reboot', '-d', 'in_asm,guest_errors,unimp,int', '-D', str(output/'trace.log'),
                        '-qmp', f'unix:{qmp},server=on,wait=off'])
        if diagnostic_ui_component:command.extend(['-gdb',f'unix:{gdb},server=on,wait=off'])
        record['command'] = command
        try:
            # Freeze/hash the actual guest input before QEMU's loader reads it.
            if digest(image) != execution_sha: raise ValueError('RTOS launch snapshot changed')
            if resource_image and digest(resource_image) != resource_info['image_sha256']:
                raise ValueError('Original resource snapshot changed before launch')
            if icon_image and digest(icon_image)!=icon_info['image_sha256']:
                raise ValueError('Original icon snapshot changed before launch')
            with (output/'stderr.log').open('wb') as errors:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors,
                                           start_new_session=True)
                started = time.monotonic()
                trace_limit=16*1024**2 if diagnostic_ui_component else MAX_LOG
                record['trace_limit_bytes']=trace_limit
                budget=startup_budget or seconds
                viewer_deadline=None
                while process.poll() is None:
                    if stop_event is not None and stop_event.is_set():
                        record['owner_requested_stop']=True;break
                    if viewer_deadline is not None and time.monotonic()>=viewer_deadline:
                        record['session_duration_reached']=True;break
                    if viewer is None and time.monotonic()-started>=budget:break
                    trace = output/'trace.log'
                    if trace.exists() and trace.stat().st_size > trace_limit:
                        record['log_limit_reached'] = True; break
                    serial = output/'serial.log'
                    if diagnostic_ui_component and control is None and serial.exists() and b'[UI COMPONENT READY]' in serial.read_bytes():
                        from component_control import ComponentControl, roundtrip
                        control=ComponentControl(qmp,gdb,output,stop_event)
                        record['original_native_ui_roundtrip']=roundtrip(control)
                        if not record['original_native_ui_roundtrip']['verified']:
                            raise ValueError('Original Confirm/Back menu roundtrip failed')
                        if viewer_port is not None:
                            if not record['original_native_ui_roundtrip']['verified']:raise ValueError('Original UI input roundtrip failed')
                            from component_control import QMP
                            check=QMP(qmp)
                            try:
                                check.request({'execute':'stop'})
                                record['original_icon_read_verification']=capture_icon_read(check.stream,output,icon_info)
                            finally:
                                try:check.request({'execute':'cont'})
                                finally:check.close()
                            icon_check=record['original_icon_read_verification']
                            if not (icon_check.get('destination_matches_original_bytes') is True and
                                    icon_check.get('actual_block_read',{}).get('completed_exact_source_read') is True):
                                raise ValueError('Original UI icon-copy verification failed')
                            from component_viewer import Viewer
                            viewer=Viewer(control,viewer_port,stop_event=stop_event)
                            viewer_deadline=time.monotonic()+session_seconds
                            record['viewer_url']=f'http://127.0.0.1:{viewer_port}'
                            with_qmp=__import__('component_control').QMP(qmp)
                            try:with_qmp.request({'execute':'human-monitor-command','arguments':{'command-line':'log guest_errors,unimp'}})
                            finally:with_qmp.close()
                            record['viewer_trace_scope']='After exact native roundtrip, verbose instruction/SVC tracing disabled; guest_errors and unimplemented accesses remain enabled. Frame/event audit retained.'
                            (output/'viewer-ready.json').write_text(json.dumps({'url':record['viewer_url'],'original_native_ui_roundtrip':record['original_native_ui_roundtrip'],'original_icon_read_verification':icon_check,'scope':'Original TopMenu auxiliary component; no normal camera boot or photography'},indent=2)+'\n')
                            if on_ready is not None:on_ready(viewer,control,record)
                            else:print('RICOH_ORIGINAL_UI_READY '+record['viewer_url'],flush=True)
                        if finish_after_ui:break
                    if serial.exists() and serial.stat().st_size > MAX_LOG:
                        record['serial_limit_reached'] = True; break
                    time.sleep(.02)
                record['duration_seconds'] = round(time.monotonic()-started, 3)
                if process.poll() is None and not (stop_event is not None and stop_event.is_set()):
                    with socket.socket(socket.AF_UNIX) as connection:
                        connection.settimeout(5); connection.connect(str(qmp))
                        stream = connection.makefile('rwb', buffering=0); stream.readline()
                        stream.write(b'{"execute":"qmp_capabilities"}\n'); stream.readline()
                        stream.write(b'{"execute":"human-monitor-command","arguments":{"command-line":"info registers -a"}}\n')
                        while True:
                            row = json.loads(stream.readline())
                            if 'return' in row: record['registers'] = row['return']; break
                        if resource_build:
                            record['original_resource_read_verification']=capture_resource_reads(
                                stream,output,resource_info,resource_payload)
                        if diagnostic_ui_component:
                            record['original_icon_read_verification']=capture_icon_read(stream,output,icon_info)
                            record['original_canvas_capture']=capture_native_canvas(stream,output)
        except BaseException as error:
            record['error'] = str(error) or type(error).__name__
            record['error_traceback'] = __import__('traceback').format_exc()[-8192:]
        finally:
            if viewer:viewer.close()
            if control:record['native_input_audit']=control.audit
            if control:control.close()
            if process is not None and process.poll() is None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(3)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(3)
            record['qemu_exit_code'] = process.returncode if process else None
            record['process_reaped'] = bool(process and process.poll() is not None)
    trace = (output/'trace.log').read_bytes() if (output/'trace.log').exists() else b''
    record['trace_size'] = len(trace); record['trace_sha256'] = hashlib.sha256(trace).hexdigest()
    serial = (output/'serial.log').read_bytes() if (output/'serial.log').exists() else b''
    record['serial_size'] = len(serial); record['serial_sha256'] = hashlib.sha256(serial).hexdigest()
    text = trace.decode(errors='replace')
    record['original_reset_executed'] = '0x53000000:' in text
    record['irq_exceptions'] = text.count('[IRQ]')
    record['svc_exceptions'] = text.count('[SVC]')
    executed = {int(value, 16) for value in re.findall(r'^0x([0-9a-f]+):', text, re.M)}
    record['original_component_entries_executed'] = {
        label: address in executed for label, address in (
            ('main_hardware_wait_site', 0x53a78818),
            ('original_ui_factory', 0x53182830),
            ('original_ui_singleton', 0x53182b48),
            ('original_ui_service_factory', 0x5358b678),
            ('original_appmain_task',0x53182950),
            ('original_resource_ctor',0x53838988),
            ('original_resource_init',0x538385c8),
            ('original_resource_size',0x5383b22c),
            ('original_resource_read',0x538396a4),
            ('original_led_factory_init',0x5393f5bc),
            ('original_font_manager_ctor',0x5323cf40),
            ('original_font_load',0x5323d198),
            ('original_screen_factory_init',0x538fe7f8),
            ('original_screen_lcd_ctor',0x53908390),
            ('original_screen_device_init',0x538fe9cc),
            ('original_topmenu_ctor',0x531b421c),
            ('original_topmenu_show',0x53184558),
            ('original_window_render',0x531835cc),
            ('original_window_draw',0x5356dde4))}
    faults = re.findall(r'DFSR 0x([0-9a-f]+) DFAR 0x([0-9a-f]+)', text)
    record['data_abort_count'] = len(faults)
    # Exception cascades can repeat the same address thousands of times.
    # Preserve distinct ordered faults and counts; the original trace remains
    # the authoritative complete event sequence with a recorded digest.
    unique = list(dict.fromkeys(faults))
    record['data_abort_faults'] = unique[:128]
    record['data_abort_faults_truncated'] = len(unique) > 128
    record['data_abort_counts'] = [dict(dfsr=a, dfar=b, count=faults.count((a, b)))
                                 for a, b in unique[:128]]
    record['blockers'] = ['Original RTOS camera tasks still require unimplemented peripherals and bootloader inputs. Linux/camera-control IPC and the physical LCD path are not connected.']
    if record.get('original_native_ui_roundtrip',{}).get('verified') and record.get('original_icon_read_verification',{}).get('destination_matches_original_bytes'):
        record['original_ui_verified']=True
        record['original_ui_scope']='Original GR IV TopMenu and Focus settings page on auxiliary software display; native Up/Down, Confirm and Back with original font/icon rendering. Original camera power-on, live-view, photography, physical LCD and Linux/RTOS IPC remain unverified.'
    if any(address == '28825000' for _, address in record['data_abort_faults']):
        record['blockers'].insert(0, 'Original PC 0x53acc2dc reads 0x28825000 before updating 32 configuration entries; this peripheral block has no confirmed reset values or register behavior in the reviewed official GPL source, so the access remains a real abort.')
    (output/'result.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=5)
    parser.add_argument('--bootloader-gic-flag', action='store_true')
    parser.add_argument('--experimental-latches', action='store_true',
                        help='Diagnostic guessed zero-reset configuration latches; never verifies hardware or UI')
    parser.add_argument('--experimental-gate-idle', action='store_true',
                        help='Diagnostic inferred idle state after guest disables gate; hardware behavior unverified')
    parser.add_argument('--experimental-memory-layout', action='store_true',
                        help='Diagnostic two 1 GiB RAM banks inferred from original pool descriptors')
    parser.add_argument('--diagnostic-skip-sric-wait', action='store_true',
                        help='Owned snapshot only: SRIC GPIO waits return failure; original GUI code unchanged')
    parser.add_argument('--diagnostic-main-ui', action='store_true',
                        help='Owned snapshot only: bypass MainTask hardware wait and enter existing boot mode 5')
    parser.add_argument('--diagnostic-gui-entry', action='store_true',
                        help='Owned snapshot only: directly call the original UI singleton/factory entry')
    parser.add_argument('--diagnostic-domain-init', action='store_true',
                        help='Owned call bridge initializes the original DomainDataFactory before original UI')
    parser.add_argument('--diagnostic-empty-domain-storage', action='store_true',
                        help='Owned snapshot returns missing file from unavailable persistent settings storage')
    parser.add_argument('--diagnostic-resources', action='store_true',
                        help='Authored read-only backend serves exact original RES bundle to original Resource/font code')
    parser.add_argument('--diagnostic-ui-component', action='store_true',
                        help='Auxiliary entry invokes original TopMenu construction, Show and render callbacks')
    parser.add_argument('--viewer-port',type=int,
                        help='Keep the verified original component running with a loopback-only Up/Down viewer; Ctrl+C stops/reaps the owned guest')
    args = parser.parse_args()
    record = probe(args.payload, args.manifest, args.output, args.seconds,
                   args.bootloader_gic_flag, args.experimental_latches,
                   args.experimental_gate_idle, args.experimental_memory_layout,
                   args.diagnostic_skip_sric_wait, args.diagnostic_main_ui, args.diagnostic_gui_entry,
                   args.diagnostic_domain_init, args.diagnostic_empty_domain_storage,
                   args.diagnostic_resources,args.diagnostic_ui_component,args.viewer_port)
    print(json.dumps({key: record.get(key) for key in ('model','original_reset_executed','irq_exceptions','svc_exceptions','data_abort_faults','original_ui_verified','process_reaped','error')}, indent=2))
    if record.get('error') or not record.get('process_reaped') or not record['original_reset_executed'] or (args.diagnostic_ui_component and record.get('original_ui_verified') is not True):
        raise SystemExit(1)


if __name__ == '__main__': main()
