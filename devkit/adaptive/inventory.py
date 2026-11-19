"""Structural ELF loader; no source-hash whitelist, symbols, or truth files."""
from pathlib import Path
import struct
from .contracts import SemanticGap, digest


class Firmware:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.sha256 = digest(self.path)
        self.data = self.path.read_bytes()
        if len(self.data) < 64 or self.data[:6] != b'\x7fELF\x02\x01':
            raise SemanticGap('This backend needs a little-endian ELF64 executable; container/raw discovery is not implemented')
        header = struct.unpack_from('<HHIQQQIHHHHHH', self.data, 16)
        file_type, machine, _, self.entry, phoff, _, _, _, phsize, phnum, *_ = header
        if machine != 183 or file_type != 2 or phsize != 56 or phnum > 128:
            raise SemanticGap('Need an ET_EXEC AArch64 ELF with bounded program headers')
        self.segments = []
        for index in range(phnum):
            start = phoff + index * phsize
            if start + 56 > len(self.data):
                raise ValueError('Truncated ELF program header')
            kind, flags, offset, va, pa, filesz, memsz, _ = struct.unpack_from('<IIQQQQQQ', self.data, start)
            if kind != 1:
                continue
            if memsz == 0:
                continue
            if pa != va or filesz > memsz or offset + filesz > len(self.data):
                raise SemanticGap('Need identity-mapped, complete PT_LOAD segments')
            if not 0x40000000 <= va < va + memsz <= 0x47e00000:
                raise SemanticGap('PT_LOAD outside this research board RAM envelope')
            self.segments.append(dict(address=va, file_offset=offset, file_size=filesz,
                                      memory_size=memsz, executable=bool(flags & 1)))
        if not self.is_code(self.entry):
            raise SemanticGap('ELF entry is not in executable file-backed data')
        self.base = min(s['address'] for s in self.segments)

    def is_code(self, address):
        return any(s['executable'] and s['address'] <= address < s['address'] + s['file_size']
                   for s in self.segments)

    def read(self, address, size):
        for segment in self.segments:
            offset = address - segment['address']
            if 0 <= offset and offset + size <= segment['file_size']:
                start = segment['file_offset'] + offset
                return self.data[start:start + size]
        raise SemanticGap(f'Local window references unavailable code/data at {address:#x}')

    def manifest(self):
        return dict(schema_version=1, source=str(self.path), source_sha256=self.sha256,
                    input_level='F0', format='ELF64', isa='aarch64', entry=self.entry,
                    segments=self.segments, profile_matched=False,
                    prior='generic molten-aarch64 research board ABI; no machine-specific addresses')
