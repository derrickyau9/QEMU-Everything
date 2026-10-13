"""Synthetic native-C checks; no original Sony resource bytes are included."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'scripts/boards/sony'


def _pack(tokens):
    result = bytearray(b'\xf0')
    for base in range(0, len(tokens), 8):
        group = tokens[base:base+8]
        result.append(sum(match << i for i, (match, _) in enumerate(group)))
        for _, data in group:
            result.extend(data)
    return bytes(result)


def _block(prefix: bytes, code: int | None = None, distance: int = 1):
    tokens = [(0, bytes([byte])) for byte in prefix]
    output = bytearray(prefix)
    if code is not None:
        tokens.append((1, ((code << 12) | distance).to_bytes(2, 'big')))
        size = 64 if code == 15 else 32 if code == 14 else code+3
        for _ in range(size):
            output.append(output[-distance])
    tokens.append((1, b'\0\0'))
    return _pack(tokens), bytes(output)


def _repeated_block(character: int, length: int):
    assert 0 < length <= 4096
    tokens = [(0, bytes([character]))]
    remain = length - 1
    lengths = [(15,64),(14,32)] + [(code,code+3) for code in range(13,-1,-1)]
    while remain:
        found = next(((code, count) for code,count in lengths if count <= remain),None)
        if found is None:
            tokens.append((0, bytes([character])))
            remain -= 1
        else:
            code,count = found
            tokens.append((1, ((code << 12) | 1).to_bytes(2,'big')))
            remain -= count
    tokens.append((1,b'\0\0'))
    return _pack(tokens), bytes([character]) * length


def _native_gate(library_path):
    lib = ctypes.CDLL(str(library_path))
    decode = lib.qe_ldec_decode
    decode.argtypes = [ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t]
    decode.restype = ctypes.c_int
    calls = 0
    def run(data, expected):
        nonlocal calls
        source = ctypes.create_string_buffer(data)
        destination = ctypes.create_string_buffer(b'\xa5' * (expected+32))
        rc = decode(source,len(data),destination,expected)
        assert destination.raw[expected:expected+32] == b'\xa5' * 32
        calls += 1
        return rc,destination.raw[:expected]
    vectors = []
    for code in range(16):
        for prefix,distance in [(b'A',1),(b'ABC',3),(b'0123456789abcdef',16)]:
            data, output = _block(prefix,code,distance)
            assert run(data,len(output)) == (len(output),output)
            vectors.append((data,output))
    first, first_output = _repeated_block(65,4096)
    last, last_output = _repeated_block(66,17)
    multi = first + last
    assert run(multi,4113) == (4113,first_output+last_output)
    vectors.append((multi,first_output+last_output))
    rng = random.Random(0x4c444543)
    for _ in range(64):
        size = rng.randrange(1,256)
        prefix = bytes(rng.randrange(256) for _ in range(size))
        data,output = _block(prefix)
        assert run(data,size) == (size,output)
        vectors.append((data,output))
    rejected = 0
    for data,output in vectors:
        for end in range(len(data)):
            assert run(data[:end],len(output))[0] < 0
            rejected += 1
        for expected in (len(output)-1,len(output)+1):
            assert run(data,expected)[0] < 0
            rejected += 1
        assert run(data+b'\0',len(output))[0] < 0
        rejected += 1
    # Missing local history, zero distance, exact block overflow, foreign
    # header, and a reference to the previous chunk all fail explicitly.
    malformed = [(b'\xf0\x01\x00\x01',3),
                 (b'\xf0\x01\x10\x00',4),
                 (b'\xf0\x02A\xf0\x01',2),
                 (b'\xf1\x02A\0\0',1),
                 (first+b'\xf0\x03\x00\x01\0\0',4099)]
    for data,expected in malformed:
        assert run(data,expected)[0] < 0
        rejected += 1
    same = ctypes.create_string_buffer(b'\xf0\x02A\0\0')
    assert decode(same,5,same,1) == -22
    assert decode(None,5,same,1) == -22
    assert decode(same,5,None,1) == -22
    assert decode(same,0,same,1) == -22
    assert decode(same,5,same,0) == -22
    assert decode(same,5,same,64*1024*1024+1) == -27
    top = (1 << (ctypes.sizeof(ctypes.c_void_p)*8)) - 1
    assert decode(ctypes.c_void_p(top-1),5,same,1) == -22
    assert decode(same,5,ctypes.c_void_p(top),1) == -22
    return {'synthetic_vectors':len(vectors),'rejected_cases':rejected,
            'native_calls_with_canaries':calls,'pointer_and_limit_checks':8}


def _linux_path(path):
    resolved = Path(path).resolve()
    if len(resolved.drive) != 2 or resolved.drive[1] != ':':
        raise ValueError('WSL gate requires a drive-letter path')
    return '/mnt/'+resolved.drive[0].lower()+resolved.as_posix()[2:]


if __name__ == '__main__' and len(sys.argv)==3 and sys.argv[1]=='--native-gate':
    print(json.dumps(_native_gate(sys.argv[2])))
    sys.exit(0)

import pytest


def test_public_ldec_source_and_evidence_are_packaged():
    import tomllib
    config = tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))
    entries = config['tool']['setuptools']['data-files']['share/qemu-everything/boards/sony']
    assert all('scripts/boards/sony/*'+suffix in entries for suffix in ('.c','.h','.json','.md'))
    report = json.loads((BOARD/'ldec-validation-20261011.json').read_text())
    assert report['public_decoder_sha256'] == hashlib.sha256((BOARD/'ldec.c').read_bytes()).hexdigest()
    assert report['decoder_sha256'] == report['public_decoder_sha256']
    assert report['public_header_sha256'] == hashlib.sha256((BOARD/'ldec.h').read_bytes()).hexdigest()
    assert report['native_c_vectors_passed'] == len(report['vectors']) == 306
    assert report['firmware_distributed'] is False
    assert report['camera_ui_startup_verified'] is False
    assert report['unsupported_alternate_image_record_indices'] == [222,224,285]
    assert all(len(row['input_sha256'])==64 and len(row['output_sha256'])==64 for row in report['vectors'])
    assert 'AGPL-3.0-or-later' in (BOARD/'ldec.c').read_text()


@pytest.mark.parametrize('code',range(16))
def test_synthetic_vectors_encode_expected_length_codes(code):
    data,output = _block(b'A',code)
    assert data[:3] == b'\xf0\x06A'
    assert data[3:5] == ((code << 12)|1).to_bytes(2,'big')
    assert data[-2:] == b'\0\0'
    assert len(output) == 1+(64 if code==15 else 32 if code==14 else code+3)


def test_native_c_decoder_on_synthetic_vectors(tmp_path):
    compiler = next((shutil.which(name) for name in ('cc','gcc','clang') if shutil.which(name)),None)
    if os.name != 'nt' and compiler:
        library = tmp_path/'ldec.so'
        subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Wconversion','-Werror',
                        '-shared','-fPIC','-O2',str(BOARD/'ldec.c'),'-o',str(library)],
                       check=True,capture_output=True,timeout=30)
        result = _native_gate(library)
    elif os.name == 'nt' and shutil.which('wsl.exe'):
        listed = subprocess.run(['wsl.exe','--list','--quiet'],capture_output=True,timeout=10)
        names = listed.stdout.decode('utf-16-le',errors='replace').replace('\0','')
        if listed.returncode or 'Debian' not in names:
            pytest.skip('native C compiler or Debian WSL distribution required')
        library = _linux_path(tmp_path/'ldec.so')
        subprocess.run(['wsl.exe','-d','Debian','--','gcc','-std=c11','-Wall','-Wextra',
                        '-Wconversion','-Werror','-shared','-fPIC','-O2',
                        _linux_path(BOARD/'ldec.c'),'-o',library],
                       check=True,capture_output=True,timeout=30)
        # The self-test mode needs the standard library only, not pytest.
        completed = subprocess.run(['wsl.exe','-d','Debian','--','python3',
                                    _linux_path(__file__),'--native-gate',library],
                                   check=True,capture_output=True,timeout=30)
        result = json.loads(completed.stdout)
    else:
        pytest.skip('native C compiler or Debian WSL distribution required')
    assert result['synthetic_vectors'] == 113
    assert result['rejected_cases'] == 9764
    assert result['pointer_and_limit_checks'] == 8
