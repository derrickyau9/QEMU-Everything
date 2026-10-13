# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile an authored read-only filesystem bridge, never vendor code.

The original Resource constructor, mutex/queue initialization and task start
are invoked. Only the low-level backend is replaced with exact RES files.
"""
import hashlib
from pathlib import Path
import struct
import subprocess


def build_bridge(directory, ui_component=False, icon_info=None):
    directory = Path(directory)
    source = Path(__file__).with_name('resource_backend.c')
    obj, elf, raw = (directory/name for name in ('resource-backend.o', 'resource-backend.elf', 'resource-backend.bin'))
    linker = directory/'resource-backend.ld'
    linker.write_text('SECTIONS { . = 0x543d5000; .text : { *(.text*) } .rodata : { *(.rodata*) } .data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } end = .; /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n')
    commands = [
        ['clang','--target=arm-none-eabi','-mcpu=cortex-a7','-marm','-mfloat-abi=soft','-Os','-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-unwind-tables','-Wno-initializer-overrides','-c',str(source),'-o',str(obj)],
        ['arm-linux-gnueabihf-ld','-T',str(linker),str(obj),'-o',str(elf)],
        ['arm-linux-gnueabihf-objcopy','-O','binary',str(elf),str(raw)]]
    source_bytes=source.read_bytes()
    frozen_source=directory/'authored-resource-backend.c'
    frozen_source.write_bytes(source_bytes)
    commands[0][commands[0].index(str(source))]=str(frozen_source)
    for command in commands: subprocess.run(command, check=True, capture_output=True)
    if source.read_bytes()!=source_bytes or frozen_source.read_bytes()!=source_bytes:
        raise ValueError('Authored backend source changed during compilation')
    symbols = {}
    for row in subprocess.check_output(['arm-linux-gnueabihf-nm',str(elf)],text=True).splitlines():
        fields = row.split()
        if len(fields) == 3: symbols[fields[2]] = int(fields[0],16)
    backend = raw.read_bytes()
    end = symbols['end']
    if end > 0x543d7000 or end < 0x543d5000+len(backend): raise ValueError('Backend placement outside scratch gap')
    backend += bytes(end-0x543d5000-len(backend))
    if ui_component and (not icon_info or not 0 < icon_info['size'] < 16*1024**2 or icon_info['size']%4):
        raise ValueError('Original UI component requires a verified exact icon extent')
    icon_setup=f'''
 mov r0,#48
 ldr r3, =0x538e09b0
 blx r3
 mov r4,r0
 mov r1,#0
 mov r2,#48
 ldr r3, =0x53156768
 blx r3
 ldr r1, ={symbols['icon_storage_vtable']:#x}
 str r1,[r4]
 str r4,[r7,#8]
 mov r0,#14
 ldr r3, =0x538dd620
 blx r3
 str r0,[r7,#4]
 ldr r0, =10800
 ldr r3, =0x538e09b0
 blx r3
 mov r4,r0
 mov r1,#0
 ldr r2, =10800
 ldr r3, =0x53156768
 blx r3
 str r4,[r7,#0x14]
 mov r0,#0
 strb r0,[r7,#0x10]
 ldr r0, ={icon_info['size']}
 ldr r1, =0x21c0
 add r1,r4,r1
 str r0,[r1]
 str r0,[r1,#0x14]
 mov r0,#1
 ldr r3, =0x538e09b0
 blx r3
 mov r4,r0
 ldr r3, =0x53830808
 blx r3
 str r4,[r0,#0x10]
''' if ui_component else ''
    component = f'''
 ldr r3, =0x53a923f8
 blx r3
 ldr r0,[r0,#0x10]
 ldr r3, =0x53a8e520
 blx r3
 ldr r0, =296
 ldr r3, =0x538e09b0
 blx r3
 mov r4,r0
 ldr r3, =0x538f9194
 blx r3
 ldr r3, =0x538f9098
 blx r3
 str r4,[r0,#4]
 ldr r3, =0x5323bbb8
 blx r3
 ldr r3, =0x5323bda0
 blx r3
 mov r1,#0
 ldr r3, =0x5323d2f0
 blx r3
 ldr r3, =0x53185f14
 blx r3
 ldr r1, =0x02000060
 ldr r3, =0x53185f7c
 blx r3
 mov r7,r0
 ldr r3, =0x5323ea84
 blx r3
 ldr r3, =0x5323ebf8
 blx r3
 ldr r1, =0x02000060
 ldr r3, =0x5325ca0c
 blx r3
 mov r0,r7
 ldr r1, =0x02000060
 mov r2,#0x10000000
 mov r3,#0
 ldr r8, =0x53184558
 blx r8
 mov r0,r7
 ldr r3,[r7]
 ldr r3,[r3,#0x2c]
 blx r3
 mov r0,r7
 ldr r3,[r7]
 ldr r3,[r3,#0x30]
 blx r3
 mov r0,#40
 ldr r3, =0x538e09b0
 blx r3
 mov r4,r0
 mov r1,#1
 ldr r2, =720
 ldr r3, =480
 ldr r8, =0x538fc99c
 blx r8
 ldr r0, =1382463
 ldr r3, =0x538e09b0
 blx r3
 add r1,r0,#63
 bic r1,r1,#63
 mov r0,r4
 ldr r2, =1382400
 ldr r3, =0x538fc9ec
 blx r3
 mov r0,r4
 ldr r1, =software_surface_descriptor
 ldr r3, =0x538fca58
 blx r3
 add r0,r7,#0xc
 mov r1,r4
 ldr r3, =0x538fcb3c
 blx r3
 ldr r0,[r7,#0x158]
 ldr r1, =software_canvas_rectangle
 ldr r3, =0x53587c3c
 blx r3
 ldr r0,[r7,#0x158]
 add r1,r7,#0xc
 ldr r3, =0x5356dd00
 blx r3
 ldr r0,[r7,#0x158]
 ldr r3, =0x535899fc
 blx r3
 ldr r0,[r7,#0x158]
 ldr r3, =0x5356dde4
 blx r3
 mov r0,r7
 ldr r3, =0x53830808
 blx r3
 ldr r1,[r0,#8]
 ldr r1,[r1,#8]
 mov r0,r7
 ldr r3, ={symbols['ui_component_loop']:#x}
 blx r3
''' if ui_component else ''
    assembly = directory/'resource-bridge.s'
    assembly.write_text(f''' .syntax unified
.arm
.text
.global _start
_start:
 push {{r4-r8,lr}}
 mov r0,#1
 ldr r3, =0x538ed63c
 blx r3
 mov r0,#32
 ldr r3, =0x538e09b0
 blx r3
 mov r4,r0
 ldr r3, =0x538ea670
 blx r3
 ldr r3, =0x538f21a0
 blx r3
 str r4,[r0,#4]
 ldr r1, =0x553a08e4
 str r4,[r1,#0x50]
 ldr r0, =5124
 ldr r3, =0x538e09b0
 blx r3
 mov r5,r0
 mov r1,#0
 ldr r2, =5124
 ldr r3, =0x53156768
 blx r3
 mov r0,#8
 ldr r3, =0x538e09b0
 blx r3
 mov r6,r0
 ldr r1, ={symbols['resource_backend_vtable']:#x}
 str r1,[r6]
 str r5,[r6,#4]
 ldr r3, =0x537a9648
 blx r3
 str r6,[r0,#0x14]
 mov r0,#80
 ldr r3, =0x538e09b0
 blx r3
 mov r4,r0
 ldr r3, =0x53838988
 blx r3
 ldr r3, =0x53830808
 blx r3
 str r4, [r0,#4]
 mov r0,r4
 ldr r3, =0x538385c8
 blx r3
 add r0,r4,#4
 ldr r3, =0x538e229c
 blx r3
 ldr r3, =0x5393f780
 blx r3
 ldr r3,[r0]
 ldr r3,[r3,#8]
 blx r3
 mov r0,#28
 ldr r3, =0x538e09b0
 blx r3
 mov r7,r0
 ldr r3, =0x538371ac
 blx r3
 ldr r3, =0x53830808
 blx r3
 str r7,[r0,#8]
{icon_setup}
 ldr r3, =0x53a81480
 blx r3
 mov r4,r0
 ldr r3,[r0]
 ldr r3,[r3,#8]
 blx r3
 mov r0,r4
 ldr r3, =0x53a8107c
 blx r3
 ldr r3, =0x53a923f8
 blx r3
 ldr r0,[r0,#0x10]
 mov r4,r0
 mov r1,#6
 ldr r3, =0x53a8bce8
 blx r3
 mov r0,r4
 mov r1,#8
 ldr r3, =0x53a8bce8
 blx r3
 ldr r3, =0x53182830
 blx r3
{component}
 pop {{r4-r8,pc}}
 .ltorg
 .align 2
software_surface_descriptor:
 .byte 5,0
 .short 720,0,720,480
 .align 2
software_canvas_rectangle:
 .word 0,0,720,480
''')
    bridge_obj, bridge_elf, bridge_raw = (directory/name for name in ('resource-bridge.o','resource-bridge.elf','resource-bridge.bin'))
    for command in ([ 'arm-linux-gnueabihf-as','-mcpu=cortex-a7',str(assembly),'-o',str(bridge_obj)],
                    ['arm-linux-gnueabihf-ld','-Ttext=0x543d4000',str(bridge_obj),'-o',str(bridge_elf)],
                    ['arm-linux-gnueabihf-objcopy','-O','binary',str(bridge_elf),str(bridge_raw)]):
        subprocess.run(command,check=True,capture_output=True)
    bridge = bridge_raw.read_bytes()
    if len(bridge) > 0x1000: raise ValueError('Resource initialization bridge exceeds assigned scratch slot')
    return bridge, backend, {'backend_source_sha256': hashlib.sha256(source_bytes).hexdigest(),
                             'builder_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                             'commands': commands, 'symbols': {name: hex(value) for name,value in symbols.items()},
                             'original_factory_init_calls':['Resource 0x538385c8','LedControlFactory 0x5393f5bc','DataRom original ctor 0x538371ac only: NV cache absent, physical-flash initialization skipped','DomainDataFactory original init and package link 0x53a8107c','SystemControl package link 0x53a8bce8 (modes 6,8): refresh original keyboard, LED, resource and domain references'],
                             'original_logging_configuration':{'native_setter':'0x538ed63c','address':'0x553a091c','value':1,'scope':'Existing original Log minimum-severity API (Log control+0x38); original 0x538ebbec filters debug-level0 through its native branch. Warning/error logging and original code stay unchanged; minimizes unconsumed debug log allocations in component startup'},
                             'original_log_datetime':{'constructor':'0x538ea670','bytes':32,'factory_getter':'0x538f21a0','factory_field':4,'log_cache_address':'0x553a0934','scope':'Original DateTime constructor and its original default fields, installed at the same ServiceFactory+4 and Log control+0x50 cache used by original package link 0x538edba8. Full ServiceFactory physical initialization and RTC updates are omitted. No clock/date or camera-ready value is fabricated; warnings/errors retain original logging.'},
                             'original_ui_component': {'enabled':ui_component,'scope':'Original TopMenuController construction, original UIManager current-ID setter, original Show and render callbacks; camera startup/ready state not injected',
                                                       'ui_id':'0x02000060','selector':'0x53185f7c','constructor':'0x531b421c','show':'0x53184558','render':'0x531835cc',
                                                       'font_initialization':'Original DrawFactory 0x5323bbb8, original lazy FontManager 0x5323bda0 and original language selection 0x5323d2f0(index0 Latin). Exact user-supplied Latin resources; no host font or glyph substitution.',
                                                       'icon_storage':{'enabled':ui_component,'block_bytes':4,'staging_address':'0x52000000','real_icon_bytes':icon_info['size'] if icon_info else None,'scope':'Authored read-only logical word storage; real original ICONBIN bytes only. Original DataRom constructor, native byte-size/ceil/block-read methods and original SystemControl 0x53a8e520 load the blob; only the original validity setter follows the actual copy result. A new development metadata table exposes item4 only; absent entries remain zero. This is not physical NAND geometry or hardware ACK.'},
                                                       'input_harness':{'enabled':ui_component,'native_handler':'current controller vtable+0x28; TopMenu 0x531b38e8','down_event':'0xd7','up_event':'0xd6','confirm_event':'0xe0','back_primitive':'0x53182e9c','native_transition_queue':'0x178 consumed by original AppMain/UIManager','completion_barrier':'original UIManager dispatch mutex +0x38','native_transition_settle':'0x535713bc','scope':'Authored heap mailbox is sequence-numbered and polled with ARM YIELD. Inputs call the current original controller handler. Confirm and Back enqueue native transitions consumed by the original AppMain task; the harness waits for the original current-ID change and original dispatch mutex before binding the completed page to the real software surface. TopMenu list animation uses its original completion/cancellation primitive before original Canvas drawing. Transitional animations are disabled for this component because full RTOS animation timer scheduling is unverified. Host submits commands only; no selection fields, captions or pixels are written.'},
                                                       'software_display':{'width':720,'height':480,'pitch_pixels':720,'bytes':1382400,'format':5,'memory_order':'RGBA','scope':'Original ScreenControl constructor binds real configuration state to ScreenFactory+4; physical DeviceFactory initialization remains skipped. Original input-buffer constructor, persistent original-heap backing, bind/activate/conversion, original Widget SetRect 0x53587c3c sets software-display bounds/clip to720x480, and original Canvas setter/draw; physical LCD ready gate is bypassed only for this component draw; no synthetic pixels or font replacements'}},
                             'readonly_adapter_error_convention':'Nonzero development error codes 2=missing/write unsupported, 5=read bounds, 9=invalid handle, 16=busy; not claimed as original vendor filesystem error numbering'}


def attach(execution, bridge, backend):
    """Redirect existing authored startup bridge; original code stays intact."""
    patched = bytearray(execution)
    site, target = 0x53a7881c, 0x543d4000
    offset = site-0x53000000
    branch = struct.pack('<I', 0xeb000000|(((target-site-8)//4)&0xffffff))
    previous = bytes(patched[offset:offset+4])
    expected=struct.pack('<I',0xeb000000|(((0x543d3000-site-8)//4)&0xffffff))
    if previous!=expected: raise ValueError('Read-only bridge requires the reviewed DomainData startup bridge')
    patched[offset:offset+4] = branch
    changes = [{'address':hex(site),'size':4,'original_bytes':previous.hex(),'execution_bytes':branch.hex(),
                'semantic':'Replace earlier authored DomainData startup bridge call with authored read-only resource initialization, then invoke the same original DomainData and UI factories'}]
    for address, content, semantic in sorted(((0x543d5000,backend,'Authored bounded read-only backend stat/open/read/close; exact user firmware files only, nonexistent files and all writes fail'),
                                       (0x543d4000,bridge,'Authored AAPCS bridge creates original Resource using original allocator/constructor/init/start; substitutes FileSystemFactory+0x14 read-only backend; initializes original LedControlFactory; constructs original DataRom with absent NV cache (physical-flash init skipped); calls original DomainData init/package link and SystemControl package links modes6/8; then invokes original UI factory. No NV fields or peripheral responses are generated')),key=lambda item:item[0]):
        position=address-0x53000000
        if position < len(patched): raise ValueError('Read-only bridge overlaps existing snapshot')
        patched.extend(bytes(position-len(patched))); patched.extend(content)
        changes.append({'address':hex(address),'size':len(content),'original_bytes':None,'execution_bytes_sha256':hashlib.sha256(content).hexdigest(),'semantic':semantic})
    return bytes(patched),changes
