"""Bounded loopback RFB client for the isolated XFCE software display."""
import socket
import struct
import threading
import time


class RfbFrames:
    def __init__(self, port):
        self.socket = socket.create_connection(('127.0.0.1', port), timeout=.5)
        self.socket.settimeout(.5)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.latest = self.error = None
        self.sequence = 0
        self.last_full_request = time.monotonic()
        self.handshake_deadline = time.monotonic()+8
        self.keys = set()
        self.buttons = 0
        try:
            version = self._read(12)
            if version not in (b'RFB 003.008\n', b'RFB 003.007\n'):
                raise ValueError('Unsupported local desktop RFB version')
            self.socket.sendall(b'RFB 003.008\n')
            count = self._read(1)[0]
            if count == 0:
                raise ValueError('Desktop RFB server rejected connection')
            offered = self._read(count)
            if 1 not in offered:
                raise ValueError('Unexpected desktop RFB security configuration')
            self.socket.sendall(b'\x01')
            if struct.unpack('!I', self._read(4))[0]:
                raise ValueError('Local desktop RFB handshake failed')
            self.socket.sendall(b'\x01')
            init = self._read(24)
            self.width, self.height = struct.unpack_from('!HH', init)
            self._dimensions(self.width, self.height)
            name_length = struct.unpack_from('!I', init, 20)[0]
            if name_length > 4096:
                raise ValueError('Desktop name exceeds limit')
            self.name = self._read(name_length).decode('utf-8', errors='replace')
            # 32-bit little-endian BGRX, matching the workbench RGB32 frames.
            pixel = struct.pack('!BBBBHHHBBB3x', 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
            self.socket.sendall(b'\x00\0\0\0' + pixel)
            self.socket.sendall(struct.pack('!BBHii', 2, 0, 2, 0, -223))
            self.pixels = bytearray(self.width*self.height*4)
            self._request(False)
            self.handshake_deadline = None
        except Exception:
            self.socket.close()
            raise
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @staticmethod
    def _dimensions(width, height):
        if not 1 <= width <= 4096 or not 1 <= height <= 4096 or width*height*4 > 32*1024**2:
            raise ValueError('Desktop dimensions exceed limit')

    def _read(self, size):
        data = bytearray()
        while len(data) < size and not self.stopped.is_set():
            if self.handshake_deadline is not None and time.monotonic() > self.handshake_deadline:
                raise TimeoutError('Desktop RFB handshake timed out')
            try:
                block = self.socket.recv(min(size-len(data), 262144))
            except socket.timeout:
                # Some software X servers miss incremental damage notices.
                # Periodic full requests also recover a stale initial frame.
                if self.handshake_deadline is None and time.monotonic()-self.last_full_request > 2:
                    self._request(False)
                continue
            if not block:
                raise EOFError('Desktop display closed')
            data.extend(block)
        if len(data) != size:
            raise EOFError('Desktop receiver stopped')
        return bytes(data)

    def _send(self, data):
        with self.lock:
            self.socket.sendall(data)

    def _request(self, incremental):
        self._send(struct.pack('!BBHHHH', 3, int(incremental), 0, 0, self.width, self.height))
        if not incremental: self.last_full_request = time.monotonic()

    def _run(self):
        try:
            while not self.stopped.is_set():
                kind = self._read(1)[0]
                if kind == 0:
                    _, count = struct.unpack('!BH', self._read(3))
                    if count > 4096:
                        raise ValueError('Too many desktop rectangles')
                    for _ in range(count):
                        x, y, width, height, encoding = struct.unpack('!HHHHi', self._read(12))
                        if encoding == -223:
                            self._dimensions(width, height)
                            if (self.width, self.height) != (width, height):
                                self.width, self.height = width, height
                                self.pixels = bytearray(width*height*4)
                        elif encoding == 0:
                            if not width or not height or x+width > self.width or y+height > self.height:
                                raise ValueError('Desktop rectangle outside framebuffer')
                            data = self._read(width*height*4)
                            for row in range(height):
                                offset = ((y+row)*self.width+x)*4
                                self.pixels[offset:offset+width*4] = data[row*width*4:(row+1)*width*4]
                        else:
                            raise ValueError(f'Unexpected desktop encoding: {encoding}')
                    self.sequence += 1
                    head = struct.pack('<6I', 0x58463244, self.width, self.height, self.width*4, 0, self.sequence)
                    self.latest = (head+b'\0'*(512-len(head)), bytes(self.pixels))
                    self._request(time.monotonic()-self.last_full_request < 2)
                elif kind == 2:
                    continue  # bell
                elif kind == 3:
                    length = struct.unpack('!3xI', self._read(7))[0]
                    if length > 1024**2:
                        raise ValueError('Desktop clipboard exceeds limit')
                    self._read(length)  # guest clipboard is not exported to host
                else:
                    raise ValueError(f'Unexpected desktop server message: {kind}')
        except (OSError, EOFError, ValueError) as error:
            if not self.stopped.is_set():
                self.error = str(error)

    def pointer(self, operation, x, y):
        x, y = max(0, min(self.width-1, x)), max(0, min(self.height-1, y))
        masks = {'down':1, 'up':1, 'middle_down':2, 'middle_up':2, 'right_down':4, 'right_up':4}
        if operation in masks:
            if operation.endswith('down'): self.buttons |= masks[operation]
            else: self.buttons &= ~masks[operation]
        if operation in ('wheel_up', 'wheel_down'):
            wheel = 8 if operation == 'wheel_up' else 16
            self._send(struct.pack('!BBHH', 5, self.buttons|wheel, x, y))
        self._send(struct.pack('!BBHH', 5, self.buttons, x, y))

    def key(self, keysym, down):
        self._send(struct.pack('!BBHI', 4, int(down), 0, keysym))
        if down: self.keys.add(keysym)
        else: self.keys.discard(keysym)

    def release_all(self):
        try:
            for key in tuple(self.keys): self.key(key, False)
            self.buttons = 0
            self.pointer('move', 0, 0)
        except OSError:
            pass

    def close(self):
        self.release_all()
        self.stopped.set()
        try: self.socket.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.socket.close()
        self.thread.join(timeout=2)
