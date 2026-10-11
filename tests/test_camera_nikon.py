"""Original synthetic containers/keys; no Nikon firmware or recovered key data."""
import binascii
import hashlib
import json
import struct

import pytest

from devkit.camera_formats import nikon
from devkit.core import Cancelled, Task


def _keys(tmp_path):
    tables = (bytes(range(256)), bytes((i * 7 + 1) & 255 for i in range(256)),
              bytes((i * 3 + 19) & 255 for i in range(256)))
    document = {"body_offset": 32, "period_bytes": 1 << 24,
                "self_check": {}}
    for name, table in zip(("T1", "T2", "T3"), tables):
        document[name] = table.hex()
        document["self_check"][name] = hashlib.sha256(table).hexdigest()
    key = tmp_path / "synthetic-key.json"
    key.write_text(json.dumps(document))
    return key, tables


def _body(names=(b"ex0001_test.bi", b"eg0001_test.bi"), payloads=(b"external MCU", b"main payload")):
    count = len(payloads)
    directory = 48 + count * 32
    body = bytearray(b" " * 32 + struct.pack(">II", count, directory) + bytes(8))
    modules = []
    offset = directory
    for name, payload in zip(names, payloads):
        stored = payload + struct.pack(">H", binascii.crc_hqx(payload, 0))
        body += name.ljust(16, b"\0") + struct.pack(">II", offset, len(stored)) + bytes(8)
        modules.append(stored)
        offset += len(stored)
    body += b"".join(modules)
    body += struct.pack(">H", binascii.crc_hqx(body, 0)) + bytes(14)
    return body


def _encrypt(body, tables):
    one, two, three = tables
    return bytes(32) + body[:32] + bytes(
        value ^ one[index & 255] ^ two[(index >> 8) & 255] ^ three[(index >> 16) & 255]
        for index, value in enumerate(body[32:], 32))


def _paths(tmp_path, raw):
    source, destination = tmp_path / "Z_30_0120.bin", tmp_path / "decoded"
    source.write_bytes(raw)
    destination.mkdir()
    return source, destination


def test_decrypt_validates_and_extracts_crc_free_modules(tmp_path):
    key, tables = _keys(tmp_path)
    body = _body()
    raw = _encrypt(body, tables)
    source, destination = _paths(tmp_path, raw)
    info = nikon.identify(source, Task(lambda _: None))
    assert info["extractable"] and info["key_required"]
    report = nikon.extract(source, destination, Task(lambda _: None), key)
    assert (destination / "decrypted.bin").read_bytes() == bytes(32) + body
    assert (destination / "module-001.bin").read_bytes() == b"external MCU"
    assert (destination / "module-002.bin").read_bytes() == b"main payload"
    assert report["modules"][1]["sha256"] == hashlib.sha256(b"main payload").hexdigest()
    assert source.read_bytes() == raw


def test_missing_and_damaged_key_write_nothing(tmp_path):
    key, tables = _keys(tmp_path)
    source, destination = _paths(tmp_path, _encrypt(_body(), tables))
    with pytest.raises(ValueError, match="key-file"):
        nikon.extract(source, destination, Task(lambda _: None))
    document = json.loads(key.read_text())
    document["T1"] = "00" * 256
    key.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="table hash"):
        nikon.extract(source, destination, Task(lambda _: None), key)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("mutation,error", [
    (lambda body: struct.pack_into(">I", body, 32, 0xFFFFFFFF), "module count"),
    (lambda body: struct.pack_into(">I", body, 36, 0xFFFFFFFF), "directory"),
    (lambda body: body.__setitem__(40, 1), "reserved"),
    (lambda body: body.__setitem__(48, ord("/")), "name"),
    (lambda body: struct.pack_into(">I", body, 64, 0xFFFFFFFF), "extents"),
    (lambda body: struct.pack_into(">I", body, 68, 0xFFFFFFFF), "extents"),
    (lambda body: body.__setitem__(-1, 1), "padding"),
    (lambda body: body.__setitem__(112, body[112] ^ 1), "module CRC"),
    (lambda body: body.__setitem__(-16, body[-16] ^ 1), "package CRC"),
])
def test_untrusted_directory_crc_and_trailer_fail_without_partial_outputs(tmp_path, mutation, error):
    key, tables = _keys(tmp_path)
    body = _body()
    mutation(body)
    source, destination = _paths(tmp_path, _encrypt(body, tables))
    with pytest.raises(ValueError, match=error):
        nikon.extract(source, destination, Task(lambda _: None), key)
    assert list(destination.iterdir()) == []


def test_cipher_positions_survive_chunk_and_period_boundaries(tmp_path):
    key, tables = _keys(tmp_path)
    payload = b"0123456789abcdef" * ((1 << 24) // 16) + b"after period"
    body = _body((b"eg0001_test.bi",), (payload,))
    source, destination = _paths(tmp_path, _encrypt(body, tables))
    report = nikon.extract(source, destination, Task(lambda _: None), key)
    assert report["modules"][0]["payload_size"] == len(payload)
    assert (destination / "module-001.bin").read_bytes() == payload


def test_new_scheme_rejected_and_suffix_alone_is_not_identification(tmp_path):
    key, _ = _keys(tmp_path)
    raw = bytes(0x260) + b" " * 32
    source, destination = _paths(tmp_path, raw)
    info = nikon.identify(source, Task(lambda _: None))
    assert not info["extractable"]
    with pytest.raises(ValueError, match="newer encryption"):
        nikon.extract(source, destination, Task(lambda _: None), key)
    source.write_bytes(b"garbage" * 100)
    assert nikon.identify(source, Task(lambda _: None)) is None


def test_existing_outputs_and_cancellation_are_preserved(tmp_path):
    key, tables = _keys(tmp_path)
    source, destination = _paths(tmp_path, _encrypt(_body(), tables))
    protected = destination / "decrypted.bin"
    protected.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        nikon.extract(source, destination, Task(lambda _: None), key)
    assert protected.read_bytes() == b"keep"
    task = Task(lambda _: None)
    task.cancelled.set()
    with pytest.raises(Cancelled):
        nikon.extract(source, destination, task, key)
    assert protected.read_bytes() == b"keep"


def test_total_output_limit_is_enforced_and_cleaned(tmp_path, monkeypatch):
    key, tables = _keys(tmp_path)
    source, destination = _paths(tmp_path, _encrypt(_body(), tables))
    monkeypatch.setattr(nikon, "MAX_OUTPUT", 1)
    with pytest.raises(ValueError, match="total 2 GiB"):
        nikon.extract(source, destination, Task(lambda _: None), key)
    assert list(destination.iterdir()) == []
