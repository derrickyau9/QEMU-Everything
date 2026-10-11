"""Corruption, format boundaries and cancellation for the FIR importer."""
import struct

import pytest

from devkit.camera_formats import canon
from devkit.core import Cancelled, Task


def _checksum(data):
    struct.pack_into("<I", data, 0x20, (~(sum(data[:0x20]) + sum(data[0x24:]))) & 0xFFFFFFFF)
    return data


def _fir(model=0x80000261, modern=False):
    # Authored synthetic packet, not a Canon firmware fixture. The 16-byte
    # updater XOR vector decodes to four zeros, "gaonisoy", four zeros.
    data = bytearray(0x1B0)
    struct.pack_into("<I", data, 0, model)
    data[0x10:0x15] = b"1.0.9"
    struct.pack_into("<6I", data, 0x24, 0x100 if modern else 0xB0, 0x120, 0xFFFFFFFF, 0x130, 0xFFFFFFFF, len(data))
    struct.pack_into("<I", data, 0x64, 0x80)
    struct.pack_into("<I", data, 0x100 if modern else 0xB0, 16)
    if modern:
        data[0xC0] = 1
    data[0x120:0x130] = bytes.fromhex("884dedbce588862d0694b8e3b2554c52")
    struct.pack_into("<III", data, 0x130, 12, 0x7C, 0x80)
    data[-4:] = b"test"
    return _checksum(data)


def _save(tmp_path, data):
    path = tmp_path / "input.FIR"
    path.write_bytes(data)
    return path


def test_decodes_updater_but_never_labels_it_a_rom(tmp_path):
    source = _save(tmp_path, _fir())
    info = canon.identify(source, Task(lambda _: None))
    assert info["extractable"] and info["model"] == "EOS 50D"
    assert any("ROM0/ROM1" in blocker for blocker in info["blockers"])
    destination = tmp_path / "out"
    destination.mkdir()
    result = canon.extract(source, destination, Task(lambda _: None))
    assert result["method"] == "Canon legacy XOR updater-only extraction"
    assert (destination / "updater1.bin").read_bytes() == b"\0" * 4 + b"gaonisoy" + b"\0" * 4
    assert (destination / "main-payload.encrypted.bin").read_bytes() == b"test"
    assert not (destination / "ROM1.BIN").exists()


@pytest.mark.parametrize("offset,value,message", [
    (0x38, 2000, "file length"),
    (0x28, 0xFFFFFFF0, "offsets"),
    (0xB0, 0xFFFFFFF0, "updater length"),
    (0x138, 0xFFFFFFF0, "payload length"),
])
def test_rejects_checksum_valid_out_of_bounds_fields(tmp_path, offset, value, message):
    data = _fir()
    struct.pack_into("<I", data, offset, value)
    source = _save(tmp_path, _checksum(data))
    info = canon.identify(source, Task(lambda _: None))
    assert not info["extractable"]
    assert message in info["blockers"][0]


def test_checksum_and_decoded_signature_are_required(tmp_path):
    data = _fir()
    data[0x125] ^= 1
    source = _save(tmp_path, data)
    assert "checksum" in canon.identify(source, Task())["blockers"][0]
    source.write_bytes(_checksum(data))
    assert "signature" in canon.identify(source, Task())["blockers"][0]


def test_modern_eos_and_powershot_are_not_treated_as_50d(tmp_path):
    source = _save(tmp_path, _fir(model=0x80000433, modern=True))
    result = canon.identify(source, Task())
    assert result["model"] == "EOS RP"
    assert not result["extractable"]
    assert "AES" in result["summary"]
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(ValueError, match="legacy XOR"):
        canon.extract(source, destination, Task())
    assert not any(destination.iterdir())
    source.write_bytes(b"Powershot" + b"\0" * 300)
    assert canon.identify(source, Task()) is None


def test_unknown_eos_model_stays_unknown(tmp_path):
    source = _save(tmp_path, _fir(model=0x8000FFFF))
    result = canon.identify(source, Task())
    assert result["model"] is None
    assert "0x8000ffff" in result["summary"]


def test_cancelled_extraction_cleans_its_own_files(tmp_path):
    source = _save(tmp_path, _fir())
    destination = tmp_path / "out"
    destination.mkdir()

    class CancelOnOutput(Task):
        def check(self):
            if (destination / "updater1.bin").exists():
                self.cancelled.set()
            super().check()

    with pytest.raises(Cancelled):
        canon.extract(source, destination, CancelOnOutput())
    assert not any(destination.iterdir())


def test_refuses_existing_destination_and_large_input(tmp_path, monkeypatch):
    source = _save(tmp_path, _fir())
    destination = tmp_path / "out"
    destination.mkdir()
    kept = destination / "keep.txt"
    kept.write_text("keep")
    with pytest.raises(ValueError, match="empty directory"):
        canon.extract(source, destination, Task())
    assert kept.read_text() == "keep"
    assert canon.MAX_BYTES == 2 * 1024**3
    # Exercise the size boundary with a small limit instead of allocating
    # a multi-gigabyte synthetic file on Windows.
    monkeypatch.setattr(canon, "MAX_BYTES", source.stat().st_size - 1)
    result = canon.identify(source, Task())
    assert not result["extractable"]
    assert "2 GiB" in result["blockers"][0]
