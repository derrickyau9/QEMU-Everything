import queue
import socket
import struct
import threading
import time
from pathlib import Path
import pytest
from devkit.core import write_json
from devkit.desktop_session import DesktopSession
from devkit.rfb import RfbFrames


def exact(sock, size):
    data = b''
    while len(data)<size:
        chunk=sock.recv(size-len(data))
        if not chunk: raise EOFError('test client disconnected')
        data+=chunk
    return data


def test_desktop_transport_pixels_and_virtual_input():
    server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1)
    events=queue.Queue(); errors=queue.Queue()
    def serve():
        try:
            client,_=server.accept()
            with client:
                client.settimeout(3)
                client.sendall(b'RFB 003.008\n'); assert exact(client,12)==b'RFB 003.008\n'
                client.sendall(b'\x01\x01'); assert exact(client,1)==b'\x01'
                client.sendall(b'\0'*4); assert exact(client,1)==b'\x01'
                client.sendall(struct.pack('!HH',2,1)+b'\0'*16+struct.pack('!I',4)+b'test')
                assert exact(client,20)[0]==0
                assert exact(client,12)[0]==2
                assert exact(client,10)[0]==3
                client.sendall(struct.pack('!BBH',0,0,1)+struct.pack('!HHHHi',0,0,2,1,0)+b'\x11\x22\x33\0\x44\x55\x66\0')
                for _ in range(12):
                    kind=exact(client,1)[0]
                    if kind==3:exact(client,9)
                    elif kind==5:events.put(('pointer',struct.unpack('!BHH',exact(client,5))))
                    elif kind==4:events.put(('key',struct.unpack('!BHI',exact(client,7))))
                    else: raise AssertionError(kind)
        except (EOFError, ConnectionResetError): pass
        except Exception as exc: errors.put(exc)
    worker=threading.Thread(target=serve,daemon=True);worker.start()
    frames=RfbFrames(server.getsockname()[1])
    try:
        end=time.monotonic()+2
        while frames.latest is None and time.monotonic()<end: time.sleep(.01)
        assert frames.latest and frames.latest[1]==b'\x11\x22\x33\0\x44\x55\x66\0'
        frames.pointer('down',99,99)
        frames.key(0xff0d,True)
        assert events.get(timeout=2)==('pointer',(1,1,0))
        assert events.get(timeout=2)==('key',(1,0,0xff0d))
        frames.release_all()
        assert events.get(timeout=2)==('key',(0,0,0xff0d))
        assert events.get(timeout=2)==('pointer',(0,0,0))
        frames.pointer('right_down',1,0)
        assert events.get(timeout=2)==('pointer',(4,1,0))
        frames.release_all()
        assert events.get(timeout=2)==('pointer',(0,0,0))
    finally:
        frames.close();server.close();worker.join(timeout=3)
    assert errors.empty(),list(errors.queue)


def test_desktop_guest_preserves_disk_layout_and_restricts_network(tmp_path):
    device=tmp_path/'device';device.mkdir()
    write_json(device/'device.json',{'model':'x2dii','abi':'arm64'})
    image=tmp_path/'rootfs.ext4';image.touch()
    session=DesktopSession(device,{'image':str(tmp_path/'sdk'),'qemu':str(tmp_path/'qemu')},{'image':str(image)})
    args=session.build_command(10001,10002,10003)
    disks=[args[i+1] for i,x in enumerate(args) if x=='-drive']
    assert 'id=xfceroot' in disks[0] and 'snapshot=on' in disks[0]
    assert ['id=framebuffer' in disks[1], 'id=payload' in disks[2], 'id=system' in disks[-1]]==[True,True,True]
    network=args[args.index('-netdev')+1]
    assert 'restrict=on' in network and 'hostfwd=tcp:127.0.0.1:' in network
    assert '-nic' not in args and not session.network


def test_desktop_dimensions_are_bounded():
    for dimensions in ((0,1),(1,0),(4097,1),(4096,4096)):
        with pytest.raises(ValueError): RfbFrames._dimensions(*dimensions)


def test_desktop_readiness_rejects_black_x_server_cursor():
    pixels=bytearray(1024*768*4)
    pixels[512*1024*4:512*1024*4+64]=b'\xff'*64
    assert not DesktopSession.has_desktop_content(pixels)
    assert DesktopSession.has_desktop_content(b'\x80\x40\x20\0'*(1024*768))
