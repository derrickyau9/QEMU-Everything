"""Publish the subsystem's display/input inside the virtual camera only."""
import json
import os
from pathlib import Path
import select
import signal
import struct
import time
from rfb import RfbFrames

ROOT=Path('/dev/qe-subsystem')
running=True
def stop(*_):
    global running
    running=False
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
frames=None
deadline=time.monotonic()+60
while running and time.monotonic()<deadline:
    try:
        frames=RfbFrames(5900)
        break
    except (OSError,EOFError):
        time.sleep(.5)
if frames is None: raise RuntimeError('Guest-local X server connection failed')
fifo=ROOT/'input'
fifo.unlink(missing_ok=True)
os.mkfifo(fifo,0o600)
fd=os.open(fifo,os.O_RDWR|os.O_NONBLOCK)
pending=b''
sequence=-1
try:
    while running:
        if frames.error: raise RuntimeError(frames.error)
        if frames.latest and frames.sequence!=sequence:
            head,pixels=frames.latest
            tmp=ROOT/'frame.tmp'
            with tmp.open('wb') as out:
                out.write(head);out.write(pixels)
            tmp.replace(ROOT/'frame')
            sequence=frames.sequence
            (ROOT/'bridge-ready').write_text(str(os.getpid()))
        ready,_,_=select.select([fd],[],[],.1)
        if not ready: continue
        pending+=os.read(fd,4096)
        if len(pending)>8192: raise RuntimeError('Input buffer exceeds limit')
        while b'\n' in pending:
            line,pending=pending.split(b'\n',1)
            parts=line.decode('ascii').split()
            if len(parts)<3: continue
            op,x,y=parts[0],int(parts[1]),int(parts[2])
            if op=='keysym': frames.key(x,bool(y))
            elif op=='key' and 59<=x<=63: frames.key(0xffbe+x-59,bool(y))
            elif op=='tap':
                frames.pointer('down',x,y);frames.pointer('up',x,y)
            else: frames.pointer(op,x,y)
finally:
    frames.close();os.close(fd)
    (ROOT/'bridge-ready').unlink(missing_ok=True)
