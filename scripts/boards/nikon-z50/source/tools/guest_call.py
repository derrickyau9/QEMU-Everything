# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit diagnostic calls into stock firmware; never passive boot evidence.

Based on the paused-call approach in WonderLantern/z7-qemu tools/probe.py.
Guest code is unchanged. Only argument registers, caller scratch and context
are supplied. Original firmware and kernel perform the resulting state changes.
"""
import socket
import struct
import time

def call(remote, packet, function, arguments, seconds=10, *, observe=(), capture=None, stack_pointer=None):
    thread=packet.split('thread:')[1].split(';')[0]
    assert remote.command('Hg'+thread)=='OK'
    saved=remote.command('g')
    registers=bytearray.fromhex(saved)
    if len(registers)!=68: raise ValueError('Expected negotiated 68-byte ARM context')
    if len(arguments)>4: raise ValueError('Only register arguments supported')
    for index, value in enumerate(arguments): struct.pack_into('<I',registers,index*4,value)
    if stack_pointer is not None: struct.pack_into('<I',registers,13*4,stack_pointer)
    # The hardware breakpoint prevents executing this existing ROM address.
    sentinel=0x100200ac
    struct.pack_into('<I',registers,14*4,sentinel)
    struct.pack_into('<I',registers,15*4,function&~1)
    cpsr=struct.unpack_from('<I',registers,64)[0]
    struct.pack_into('<I',registers,64,cpsr|0x20)
    assert remote.command(f'Z1,{sentinel:x},4')=='OK'
    for address in observe: assert remote.command(f'Z1,{address:x},2')=='OK'
    assert remote.command('G'+registers.hex())=='OK'
    deadline=time.monotonic()+seconds; observations=[]
    remote.send('c')
    while True:
        remote.sock.settimeout(max(.01,deadline-time.monotonic()))
        try: returned=remote.receive()
        except socket.timeout:
            remote.sock.sendall(b'\x03'); remote.sock.settimeout(5); returned=remote.receive()
        return_thread=returned.split('thread:')[1].split(';')[0]
        assert remote.command('Hg'+return_thread)=='OK'
        after=remote.registers()
        pc=int(after['r15'],16)
        if pc not in observe or len(observations)>=256 or time.monotonic()>=deadline: break
        item=dict(pc=hex(pc),registers=after)
        if capture: item.update(capture(remote,after))
        observations.append(item)
        # Execute the stopped instruction with this observation disabled, then
        # re-arm it. No register or memory contents are changed by the trace.
        assert remote.command(f'z1,{pc:x},2')=='OK'
        assert remote.command('Hc'+return_thread)=='OK'
        remote.send('s'); remote.sock.settimeout(5); remote.receive()
        assert remote.command(f'Z1,{pc:x},2')=='OK'
        assert remote.command('Hc-1')=='OK'
        remote.send('c')
    for address in observe: remote.command(f'z1,{address:x},2')
    remote.command(f'z1,{sentinel:x},4')
    completed=int(after['r15'],16)==sentinel
    record=dict(function=hex(function),arguments=list(map(hex,arguments)),stop=returned,
                registers=after,completed=completed,context_restored=False,
                kind='explicit diagnostic input through stock firmware API')
    if observe: record['observations']=observations
    if completed:
        assert remote.command('G'+saved)=='OK'
        record['context_restored']=True
    return record,returned
