# SPDX-License-Identifier: GPL-2.0-or-later
"""One real QEMU instance with audited stock-firmware menu input.

Frames are the guest GPU's actual rendered buffers. No host widgets are drawn
into the camera image. Diagnostic calls are made in the native InitTask at its
periodic service checkpoint, never in an interrupted idle/IRQ context.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import struct
import zlib
from run import ROOT, DEFAULT_FIRMWARE, command
from rsp import Remote
from guest_call import call

def png_from_ppm(data):
    magic,dimensions,maximum,pixels=data.split(b'\n',3)
    w,h=map(int,dimensions.split())
    if magic!=b'P6' or maximum!=b'255' or len(pixels)!=w*h*3: raise ValueError('Invalid PPM')
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload))
    rows=b''.join(b'\0'+pixels[y*w*3:(y+1)*w*3] for y in range(h))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')

class Session:
    def __init__(self,prefix,*,firmware_image=None,host_version=None,sd_image=None,sd_trace=False,device_env=None):
        self.out=ROOT/'.local'/prefix
        self.out.mkdir(parents=True,exist_ok=True)
        self.graphics=self.out/'graphics'; self.graphics.mkdir(exist_ok=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        self.args=command(ROOT/'.build/build/qemu-system-arm',firmware_image or DEFAULT_FIRMWARE,'assisted',2,port,True,host_version=host_version,sd_image=sd_image)
        self.args[self.args.index('-M')+1]='nikon-z50-display'
        self.args+=['-icount','shift=6,align=off,sleep=off','-d','guest_errors']
        if sd_trace:
            self.args+=['-trace','enable=sdhci_*','-trace','enable=sdcard_read_block']
        env=os.environ.copy(); env['Z50_MMIO_QUIET']='1'; env['Z50_FRAME_DIR']=str(self.graphics)
        if device_env:
            if any(not key.startswith('Z50_') for key in device_env): raise ValueError('Expected Z50 device options')
            env.update(device_env)
        if host_version is not None: env['Z50_MODULE_HOST_VERSION']=str(host_version)
        else: env.pop('Z50_MODULE_HOST_VERSION',None)
        # Keep inherited device switches in the private runtime audit too:
        # command arguments alone cannot distinguish MCU/RAW/ISP experiments.
        self.device_options={key:env[key] for key in sorted(env) if key.startswith('Z50_')}
        self.log=(self.out/'devices.log').open('w')
        self.proc=subprocess.Popen(self.args,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,env=env)
        self.remote=Remote(port); self.packet=None; self.audit=[]
    def run_to(self,address,seconds=20,*,observe=(),capture=None):
        r=self.remote
        armed=set(observe)|{address}; observations=[]
        deadline=time.monotonic()+seconds
        for point in armed: assert r.command(f'Z1,{point:x},2')=='OK'
        try:
            while True:
                r.send('c'); r.sock.settimeout(max(.01,deadline-time.monotonic()))
                try: packet=r.receive()
                except socket.timeout:
                    r.sock.sendall(b'\x03'); r.sock.settimeout(5); packet=r.receive()
                thread=packet.split('thread:')[1].split(';')[0]
                assert r.command('Hg'+thread)=='OK'
                regs=r.registers(); self.packet=packet; pc=int(regs['r15'],16)
                if pc==address or pc not in armed or time.monotonic()>=deadline: break
                item=dict(pc=hex(pc),registers=regs)
                if capture: item.update(capture(r,regs))
                observations.append(item)
                assert r.command(f'z1,{pc:x},2')=='OK'
                assert r.command('Hc'+thread)=='OK'
                r.send('s'); r.sock.settimeout(5); r.receive()
                assert r.command('Hc-1')=='OK'
                if len(observations)>=256:
                    for point in armed-{address,pc}: r.command(f'z1,{point:x},2')
                    armed={address}
                else: assert r.command(f'Z1,{pc:x},2')=='OK'
        finally:
            for point in armed: r.command(f'z1,{point:x},2')
        record=dict(checkpoint=hex(address),registers=regs)
        if observe:
            record['observations']=observations
            record['observation_limit_reached']=len(observations)>=256
        self.audit.append(record)
        if int(regs['r15'],16)!=address: raise RuntimeError('Checkpoint not reached: '+regs['r15'])
    def run_for(self,seconds):
        r=self.remote; r.send('c'); r.sock.settimeout(seconds)
        try:
            self.packet=r.receive()
            raise RuntimeError('Guest stopped unexpectedly: '+self.packet)
        except socket.timeout:
            r.sock.sendall(b'\x03'); r.sock.settimeout(5); self.packet=r.receive()
    def input(self,function,args,**diagnostic):
        self.run_to(0x100531bc,observe=diagnostic.get('observe',()),
                    capture=diagnostic.get('capture'))
        payload=diagnostic.pop('payload',None)
        scratch=None
        if payload is not None:
            if not 0 < len(payload) <= 512: raise ValueError('Scratch payload must be 1..512 bytes')
            scratch=(int(self.remote.registers()['r13'],16)-len(payload)-64)&~7
            previous=self.remote.memory(scratch,len(payload))
            assert self.remote.command(f'M{scratch:x},{len(payload):x}:'+payload.hex())=='OK'
            args=[scratch+x[1] if isinstance(x,tuple) and x[0]=='scratch' else x for x in args]
            diagnostic['stack_pointer']=scratch
        seconds=diagnostic.pop('seconds',45)
        result,self.packet=call(self.remote,self.packet,function,args,seconds=seconds,**diagnostic)
        if scratch is not None:
            result['scratch']=dict(address=hex(scratch),payload=payload.hex(),restored=False)
            if result['completed']:
                result['scratch']['result']=self.remote.memory(scratch,len(payload)).hex()
                assert self.remote.command(f'M{scratch:x},{len(previous):x}:'+previous.hex())=='OK'
                result['scratch']['restored']=True
        self.audit.append(result)
        self.write_audit()
        if not result['completed']: raise RuntimeError('Stock API did not return: '+str(result))
        return result
    def frame(self,name):
        data=(self.graphics/'gui-latest.ppm').read_bytes()
        target=self.out/(name+'.png'); target.write_bytes(png_from_ppm(data))
        self.audit.append(dict(frame=name,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data)))
        self.write_audit()
        return target
    def write_audit(self):
        (self.out/'session.json').write_text(json.dumps(dict(command=self.args,
            device_options=self.device_options,audit=self.audit),indent=2)+'\n')
    def close(self):
        # Preserve the actual blocked task frames when an original API waits.
        r=self.remote
        try:
            if (self.graphics/'gui-latest.ppm').exists(): self.frame('last-observed')
            tasks=[]
            for cell in (0xc2b6f008,0xc2b7a2e8,0xc2b7c580):
                raw=r.memory(cell,0xd8); sp=struct.unpack_from('<I',raw,0xbc)[0]
                tasks.append(dict(cell=hex(cell),raw=raw.hex(),saved_sp=hex(sp),stack=r.memory(sp,256).hex() if sp else None))
            self.audit.append(dict(final_tasks=tasks))
        except (OSError,ValueError,EOFError) as exc: self.audit.append(dict(capture_error=str(exc)))
        self.write_audit()
        self.remote.sock.close(); self.proc.terminate()
        try: self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait()
        self.log.close()
