"""Original RES boundaries and read-only resource snapshot regression tests."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import json
import shlex
import os
import subprocess
import signal
import time
import shutil
import zlib

import pytest

path = Path(__file__).parents[1]/'scripts/boards/ricoh/resources.py'
spec = importlib.util.spec_from_file_location('ricoh_resources_test',path)
resources = importlib.util.module_from_spec(spec); spec.loader.exec_module(resources)


def bundle(items):
    data = bytearray(struct.pack('<8sII', b'RES\0\0\0\0\0',0xffff0006,len(items)))
    for name,content in items:
        data.extend(name.encode('ascii').ljust(32,b'\0'))
        data.extend(struct.pack('<I',len(content))); data.extend(content)
    data.extend(bytes((-len(data))&3)); data.extend(b'CRC!')
    return bytes(data)


def test_original_icon_blob_preserves_exact_extent_and_hash():
    body=bytes.fromhex('ff0000ff00ff00ff')
    data=struct.pack('<8sII',b'ICONBIN\0',0x00040004,len(body))+body+bundle([('A:\\Resource\\x.bin',b'abc')])
    result=resources.parse_icon_blob(data,0,following_resource_offset=24)
    assert result['payload_offset']==16 and result['size']==8 and result['end_offset']==24
    assert result['sha256']==hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize('change',['magic','kind','size','alignment','tail','boundary','offset'])
def test_original_icon_blob_rejects_ambiguous_or_unbounded_data(change):
    data=bytearray(struct.pack('<8sII',b'ICONBIN\0',0x00040004,8)+bytes(8)+bundle([('A:\\Resource\\x.bin',b'abc')]))
    offset,boundary=0,24
    if change=='magic':data[0]=ord('X')
    elif change=='kind':struct.pack_into('<I',data,8,0xffff0004)
    elif change=='size':struct.pack_into('<I',data,12,resources.MAX_ICON_BYTES+4)
    elif change=='alignment':struct.pack_into('<I',data,12,7)
    elif change=='tail':data[24]=ord('X')
    elif change=='boundary':boundary=25
    elif change=='offset':offset=True
    with pytest.raises(ValueError):resources.parse_icon_blob(data,offset,following_resource_offset=boundary)


def test_native_icon_descriptor_extents_must_match_real_pixel_bytes():
    rom=bytearray(0x137b640+0x13410+663*4)
    for index in range(663):
        offset=0x10000+index*12
        struct.pack_into('<I',rom,0x137b640+0x13410+index*4,0x53000000+offset)
        struct.pack_into('<BBHHHI',rom,offset,int(index>0),0,int(index>0),int(index>0),0,max(0,index-1))
    verified=resources.verify_griv_icon_descriptors(rom,{'size':662*4})
    assert verified['count']==663 and verified['maximum_byte_extent']==2648
    # A descriptor pointer beyond the source ROM must fail before any pixels
    # can be staged; it cannot be accepted merely because ICONBIN is valid.
    struct.pack_into('<I',rom,0x137b640+0x13410+4,0x60000000)
    with pytest.raises(ValueError,match='pointer'):resources.verify_griv_icon_descriptors(rom,{'size':662*4})


def test_native_icon_descriptor_overread_is_rejected():
    rom=bytearray(0x137b640+0x13410+663*4)
    for index in range(663):
        offset=0x10000+index*12
        struct.pack_into('<I',rom,0x137b640+0x13410+index*4,0x53000000+offset)
        struct.pack_into('<BBHHHI',rom,offset,int(index>0),0,int(index>0),int(index>0),0,max(0,index-1))
    struct.pack_into('<I',rom,0x10000+662*12+8,0xffffffff)
    with pytest.raises(ValueError,match='pixels'):resources.verify_griv_icon_descriptors(rom,{'size':662*4})


def test_icon_staging_rejects_changed_original_pixels():
    pixels=b'\x01\x02\x03\x04'
    data=struct.pack('<8sII',b'ICONBIN\0',0x00040004,4)+pixels+bundle([('A:\\Resource\\x.bin',b'abc')])
    info=resources.parse_icon_blob(data,0,following_resource_offset=20)
    image=resources.readonly_icon_image(data,info)
    assert image==struct.pack('<4I',0x4e4f4349,1,4,4)+pixels
    changed=bytearray(data);changed[16]^=1
    with pytest.raises(ValueError,match='changed'):resources.readonly_icon_image(changed,info)


def test_component_remote_can_only_submit_bounded_command_mailbox():
    spec=importlib.util.spec_from_file_location('ricoh_control_test',path.with_name('component_control.py'))
    control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)
    remote=control.MailboxRemote.__new__(control.MailboxRemote);commands=[]
    remote.command=lambda command:commands.append(command) or 'OK'
    remote.submit(0x59610000,1,0,2)
    assert commands==['M59610014,10:'+struct.pack('<4I',1,0,2,0).hex()]
    for address,sequence,operation in [(0x1000,1,1),(0x80000000,1,1),(0x59610000,0,1),(0x59610000,1,99)]:
        with pytest.raises(ValueError):remote.submit(address,sequence,0,operation)
    assert len(commands)==1  # invalid requests never reach GDB


@pytest.mark.parametrize('client',['QMP','MailboxRemote'])
def test_component_control_closes_socket_when_connection_setup_fails(monkeypatch,client):
    spec=importlib.util.spec_from_file_location('ricoh_control_cleanup_test',path.with_name('component_control.py'))
    control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)
    class FailedSocket:
        closed=False
        def settimeout(self,value):pass
        def connect(self,value):raise ConnectionRefusedError('Owned guest is absent')
        def close(self):self.closed=True
    # The runtime runs in Linux/WSL; this pure mocked cleanup test also runs
    # under Windows Python builds which do not expose Unix socket constants.
    monkeypatch.setattr(control.socket,'AF_UNIX',1,raising=False)
    owned=FailedSocket();monkeypatch.setattr(control.socket,'socket',lambda *args:owned)
    with pytest.raises(ConnectionRefusedError):getattr(control,client)('/absent/owned/socket')
    assert owned.closed


def test_component_viewer_rejects_foreign_origins_and_never_accepts_arbitrary_commands():
    import http.client,threading
    spec=importlib.util.spec_from_file_location('ricoh_viewer_test',path.with_name('component_viewer.py'))
    viewer_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(viewer_module)
    class Control:
        lock=threading.Lock();frame=b'original png bytes';audit=[]
        def key(self,key):
            assert key=='down';return {},{'sequence':1,'key':key}
    viewer=viewer_module.Viewer(Control(),0);port=viewer.server.server_address[1]
    try:
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        connection.request('GET','/status')
        value=json.loads(connection.getresponse().read())
        assert value['phase']=='running' and value['ready'] is True
        connection.close()
        viewer.control.frame=None
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        connection.request('GET','/status')
        value=json.loads(connection.getresponse().read())
        assert value['phase']=='starting' and value['ready'] is False
        viewer.control.frame=b'original png bytes';connection.close()
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        connection.request('POST','/input',b'{"key":"down"}',{'Content-Type':'application/json','Origin':'https://unrelated.example'})
        assert connection.getresponse().status==403;connection.close()
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        connection.request('POST','/input',b'{"key":"shell"}',{'Content-Type':'application/json','X-Session':viewer.session})
        assert connection.getresponse().status==400;connection.close()
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        connection.request('POST','/input',b'{"key":"down"}',{'Content-Type':'application/json','X-Session':viewer.session})
        response=connection.getresponse();assert response.status==200
        assert json.loads(response.read())['sequence']==1;connection.close()
    finally:viewer.close()


def test_complete_unaligned_chain_preserves_real_bytes_and_guest_addresses():
    items=[('A:\\Resource\\x.bin',b'abc'),('A:\\Resource\\a.jpg',b'\xff\xd8abcd\xff\xd9')]
    data=bundle(items)
    index=resources.parse_bundle(data,0,require_payload_tail=True)
    assert index['count']==2 and index['total_file_bytes']==11
    assert index['items'][1]['payload_offset']==91  # no fabricated record alignment
    image,placements=resources.readonly_image(data,index)
    assert struct.unpack_from('<4I',image)==(2,1,0,0)
    for i,(_,content) in enumerate(items):
        name,at,size=struct.unpack_from('<3I',image,16+i*12)
        assert image[at-0x54400000:at-0x54400000+size]==content
        assert placements[i]['sha256']==hashlib.sha256(content).hexdigest()
        assert image[name-0x54400000:].startswith(items[i][0].encode()+b'\0')


@pytest.mark.parametrize('offset',[True,-1,1.2,1000])
def test_resource_header_offset_must_be_bounded_integer(offset):
    with pytest.raises(ValueError):resources.parse_bundle(bundle([('A:\\Resource\\x.bin',b'a')]),offset)


@pytest.mark.parametrize('data',[b'',b'RES\0'*4,struct.pack('<8sII',b'RES\0\0\0\0\0',0xffff0006,513)])
def test_incomplete_or_excessive_header_rejected(data):
    with pytest.raises(ValueError):resources.parse_bundle(data,0)


@pytest.mark.parametrize('name',['B:\\Resource\\x.bin','A:\\..\\x.bin','A:\\Resource/x.bin','A:\\Resource\\x:ads','A:\\Resource\\x\x01'])
def test_guest_paths_reject_invalid_resource_namespace(name):
    with pytest.raises(ValueError,match='path'):resources.parse_bundle(bundle([(name,b'abc')]),0)


def test_aliases_and_nonzero_path_padding_rejected():
    with pytest.raises(ValueError,match='Duplicate'):
        resources.parse_bundle(bundle([('A:\\Resource\\x.bin',b'a'),('A:\\resource\\X.bin',b'b')]),0)
    with pytest.raises(ValueError,match='Duplicate'):
        resources.parse_bundle(bundle([('A:\\Resource\\x.bin',b'a'),('A:\\Resource\\\\x.bin',b'b')]),0)
    broken=bytearray(bundle([('A:\\Resource\\x.bin',b'a')]))
    broken[47]=1
    with pytest.raises(ValueError,match='padded'):resources.parse_bundle(broken,0)


def test_declared_extent_and_total_output_cap_enforced(monkeypatch):
    data=bytearray(bundle([('A:\\Resource\\x.bin',b'abc')]))
    struct.pack_into('<I',data,48,0xffffffff)
    with pytest.raises(ValueError,match='extent'):resources.parse_bundle(data,0)
    monkeypatch.setattr(resources,'MAX_RESOURCE_BYTES',2)
    with pytest.raises(ValueError,match='limit'):
        resources.parse_bundle(bundle([('A:\\Resource\\x.bin',b'abc')]),0)


@pytest.mark.parametrize('name,content',[('A:\\Resource\\x.jpg',b'\xff\xd8missing end'),
    ('A:\\Resource\\x.wav',b'RIFF\x00\x00\x00\x00WAVEabc'),
    ('A:\\Resource\\LE_Test.bin',b'LELR'+bytes(12))])
def test_structured_resource_extent_checks(name,content):
    with pytest.raises(ValueError,match='incomplete'):resources.parse_bundle(bundle([(name,content)]),0)


def test_wave_font_tail_and_changed_snapshot_bytes():
    font=b'LELR'+bytes(4)+struct.pack('<I',16)+bytes(4)
    wave=b'RIFF'+struct.pack('<I',7)+b'WAVEabc'
    data=bundle([('A:\\Resource\\LE_A.bin',font),('A:\\Resource\\x.wav',wave)])
    index=resources.parse_bundle(data,0,require_payload_tail=True)
    assert [i['format'] for i in index['items']]==['ricoh-le-font','riff-wave']
    with pytest.raises(ValueError,match='trailer'):resources.parse_bundle(data+b'x',0,require_payload_tail=True)
    changed=bytearray(data);changed[index['items'][0]['payload_offset']+4]=1
    with pytest.raises(ValueError,match='bytes changed'):resources.readonly_image(changed,index)
    with pytest.raises(ValueError,match='overlap'):resources.readonly_image(data,index,load=0x54ffffff)


@pytest.mark.parametrize('pitch,width,height,fmt,valid,expected',[(2,2,2,5,1,True),(1,2,2,5,1,False),(2,2,2,2,1,False),(2,2,2,5,0,False),(2048,2048,2049,5,1,False)])
def test_original_canvas_capture_keeps_actual_rgba_and_rejects_invalid_extents(tmp_path,pitch,width,height,fmt,valid,expected):
    probe_path=path.with_name('rtos_probe.py')
    spec=importlib.util.spec_from_file_location('ricoh_canvas_oracle_test',probe_path)
    probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
    ctrl,canvas,surface,pixels=0x59600000,0x59610000,0x59620000,0x59630000
    h=bytearray(0x158);struct.pack_into('<I',h,4,canvas)
    c=bytearray(0xf0);struct.pack_into('<I',c,0xec,surface);c[0x9e]=1
    s=bytearray(40);s[0]=valid;s[2]=fmt;struct.pack_into('<I',s,8,pixels);struct.pack_into('<4H',s,0x18,pitch,0,width,height)
    raw=bytes.fromhex('ff0000ff00ff00ff0000ffffffffffff')
    memory={0x55080dc0:struct.pack('<I',ctrl),0x5538c510:bytes(4),ctrl:bytes(h),canvas:bytes(c),surface:bytes(s),pixels:raw}
    class QMP:
        def __init__(self):self.commands=[]
        def write(self,value):
            request=json.loads(value);self.commands.append(request)
            assert request['execute']=='human-monitor-command'
            _,address,size,target=shlex.split(request['arguments']['command-line'])
            data=memory[int(address,16)];assert len(data)==int(size);Path(target).write_bytes(data)
        def readline(self):return b'{"return":""}\n'
    qmp=QMP();record=probe.capture_native_canvas(qmp,tmp_path)
    assert record['synthetic_pixels'] is False
    if expected:
        assert record['distinct_colors']==4 and record['raw_sha256']==hashlib.sha256(raw).hexdigest()
        png=(tmp_path/'original-canvas.png').read_bytes();offset=8;compressed=b''
        while offset<len(png):
            size,kind=struct.unpack_from('>I4s',png,offset)
            if kind==b'IDAT':compressed+=png[offset+8:offset+8+size]
            offset+=12+size
        assert zlib.decompress(compressed)==b'\0'+raw[:8]+b'\0'+raw[8:]
    else:
        assert 'blocker' in record and not (tmp_path/'original-canvas.png').exists()
        assert len(qmp.commands)==5  # no invalid framebuffer read


@pytest.mark.parametrize('changed',[False,True])
def test_qmp_resource_oracle_hashes_actual_ram_and_never_treats_reuse_as_match(tmp_path,changed):
    probe_path=path.with_name('rtos_probe.py')
    spec=importlib.util.spec_from_file_location('ricoh_resource_oracle_test',probe_path)
    probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
    payload=bundle([('A:\\Resource\\x.bin',b'original bytes')])
    info=resources.parse_bundle(payload,0,require_payload_tail=True)
    destination=0x59630000
    telemetry=struct.pack('<I4I',1,0,destination,14,0)+bytes(1008)
    memory={0x5537df40:struct.pack('<I',0x59610000),
            0x59610000:struct.pack('<II',0x543d3888,0x59620000),
            0x59620000:telemetry,
            destination:b'changed  bytes' if changed else b'original bytes'}
    class QMP:
        def __init__(self):self.commands=[]
        def write(self,value):
            request=json.loads(value);self.commands.append(request)
            if request['execute']=='human-monitor-command':
                _,address,size,target=shlex.split(request['arguments']['command-line'])
                data=memory[int(address,16)]
                assert len(data)==int(size)
                Path(target).write_bytes(data)
        def readline(self):return b'{"return":""}\n'
    qmp=QMP();result=probe.capture_resource_reads(qmp,tmp_path,info,payload)
    assert qmp.commands[0]['execute']=='stop'
    assert result['read_count']==1 and result['guest_paused_for_capture']
    record=result['reads'][0]
    assert record['destination_matches_original_bytes']==(not changed)
    assert record['source_sha256']==hashlib.sha256(b'original bytes').hexdigest()
    assert (record['captured_destination_sha256']==record['source_sha256'])==(not changed)
    assert all(r['execute'] in ('stop','human-monitor-command') for r in qmp.commands)


@pytest.mark.skipif(os.name=='nt' or not os.environ.get('RICOH_RTOS_TEST_MANIFEST'),reason='Requires separate Ricoh QEMU build in Linux/WSL')
def test_authored_arm_readonly_backend_rejects_writes_and_overreads_and_copies_exact_bytes(tmp_path):
    """Run authored ARM test code; no vendor program/firmware is involved."""
    import sys
    for command in ('clang','arm-linux-gnueabihf-as','arm-linux-gnueabihf-ld','arm-linux-gnueabihf-objcopy'):
        if not shutil.which(command):pytest.skip('ARM build tools unavailable')
    sys.path.insert(0,str(path.parent))
    try:
        from resource_boot import build_bridge
        _,backend,metadata=build_bridge(tmp_path)
    finally:sys.path.remove(str(path.parent))
    source=bundle([('A:\\Resource\\x.bin',b'abc')]);index=resources.parse_bundle(source,0)
    image,_=resources.readonly_image(source,index)
    (tmp_path/'files.bin').write_bytes(image)
    vtable=metadata['symbols']['resource_backend_vtable']
    icon_vtable=metadata['symbols']['icon_storage_vtable']
    (tmp_path/'icons.bin').write_bytes(struct.pack('<4I',0x4e4f4349,1,8,4)+bytes.fromhex('ff0000ff00ff00ff'))
    script=f'''.syntax unified
.arm
.text
.global _start
_start:
 mrc p15,0,r0,c0,c0,5
 ands r0,r0,#3
 bne idle
 ldr sp,=0x50010000
 ldr r4,=0x50020000
 ldr r0,={vtable}
 str r0,[r4]
 ldr r0,=0x50021000
 str r0,[r4,#4]
 ldr r5,=0x50030000
 ldr r6,=0x54400000
 ldr r7,[r6,#16]
 mov r0,r4
 mov r1,r7
 mov r2,#2
 mov r3,r5
 sub sp,sp,#8
 add r8,r5,#4
 str r8,[sp]
 ldr r9,[r4]
 ldr r9,[r9,#0x20]
 blx r9
 cmp r0,#0
 bne failed
 ldr r4,=0x50040000
 ldr r0,={icon_vtable}
 str r0,[r4]
 mov r0,r4
 ldr r9,[r4]
 ldr r9,[r9,#0x4c]
 blx r9
 cmp r0,#4
 bne failed
 mov r0,r4
 mov r1,#0
 mov r2,#3
 ldr r3,=0x50041000
 ldr r9,[r4]
 ldr r9,[r9,#0x30]
 blx r9
 cmp r0,#0
 bne failed
 mov r0,r4
 mov r1,#0
 mov r2,#2
 ldr r3,=0x50041000
 blx r9
 cmp r0,#1
 bne failed
 ldr r0,=0x50041000
 ldr r1,[r0]
 ldr r2,=0xff0000ff
 cmp r1,r2
 bne failed
 ldr r1,[r0,#4]
 ldr r2,=0xff00ff00
 cmp r1,r2
 bne failed
 ldr r1,[r4,#4]
 cmp r1,#1
 bne failed
 ldr r1,[r4,#0xc]
 cmp r1,#8
 bne failed
 mov r0,r4
 mvn r1,#0
 mov r2,#1
 ldr r3,=0x50041000
 blx r9
 cmp r0,#0
 bne failed
 ldr r4,=0x50020000
 ldr r9,[r4]
 ldr r9,[r9,#0x20]
 mov r0,r4
 mov r1,r7
 mov r2,#1
 mov r3,r5
 blx r9
 cmp r0,#1
 bne failed
 mov r0,r4
 ldr r1,[r5]
 add r2,r5,#8
 mov r3,#4
 ldr r9,[r4]
 ldr r9,[r9,#0x28]
 blx r9
 cmp r0,#0
 bne failed
 ldr r0,[r5,#8]
 cmp r0,#0
 bne failed
 mov r0,r4
 ldr r1,[r5]
 add r2,r5,#8
 mov r3,#3
 blx r9
 cmp r0,#1
 bne failed
 ldr r0,[r5,#8]
 ldr r1,=0x00636261
 cmp r0,r1
 bne failed
 mov r0,r4
 ldr r1,[r5]
 mov r2,r8
 ldr r9,[r4]
 ldr r9,[r9,#0x24]
 blx r9
 cmp r0,#1
 bne failed
 mov r0,r4
 ldr r1,[r5]
 mov r2,r8
 blx r9
 cmp r0,#0
 bne failed
 ldr r0,=passed_message
 b print
failed:
 ldr r0,=failed_message
print:
 ldr r2,=0x1de00004
1: ldrb r1,[r0],#1
 cmp r1,#0
 beq idle
 strh r1,[r2]
 b 1b
idle: b idle
 .ltorg
passed_message: .asciz "AUTHORED_ROFS_ABI_PASS\\n"
failed_message: .asciz "AUTHORED_ROFS_ABI_FAIL\\n"
'''
    assembly=tmp_path/'harness.s';assembly.write_text(script)
    for command in (['arm-linux-gnueabihf-as','-mcpu=cortex-a7',str(assembly),'-o',str(tmp_path/'harness.o')],
                    ['arm-linux-gnueabihf-ld','-Ttext=0x40000000',str(tmp_path/'harness.o'),'-o',str(tmp_path/'harness.elf')],
                    ['arm-linux-gnueabihf-objcopy','-O','binary',str(tmp_path/'harness.elf'),str(tmp_path/'harness.bin')]):
        subprocess.run(command,check=True,capture_output=True)
    manifest=json.loads(Path(os.environ['RICOH_RTOS_TEST_MANIFEST']).read_text())
    command=[manifest['binary'],'-M','ricoh-rtos-lab','-smp','3','-m','512','-display','none','-monitor','none','-serial',f'file:{tmp_path/"serial.log"}','-nic','none',
             '-device',f'loader,file={tmp_path/"harness.bin"},addr=0x40000000,force-raw=on',
             '-device',f'loader,file={tmp_path/"resource-backend.bin"},addr=0x543d3400,force-raw=on',
             '-device',f'loader,file={tmp_path/"files.bin"},addr=0x54400000,force-raw=on',
             '-device',f'loader,file={tmp_path/"icons.bin"},addr=0x52000000,force-raw=on']
    for cpu in range(3):command.extend(['-device',f'loader,addr=0x40000000,cpu-num={cpu}'])
    process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
    try:
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and process.poll() is None:
            if (tmp_path/'serial.log').is_file() and b'AUTHORED_ROFS_ABI_' in (tmp_path/'serial.log').read_bytes():break
            time.sleep(.02)
        serial=(tmp_path/'serial.log').read_bytes()
        assert b'AUTHORED_ROFS_ABI_PASS' in serial and b'AUTHORED_ROFS_ABI_FAIL' not in serial
    finally:
        if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
        process.communicate(timeout=3)
