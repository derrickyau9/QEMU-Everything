"""Parser invariants using original, firmware-free GR-format fixtures."""
import hashlib
import struct

import pytest

from devkit.camera_formats import ricoh
from devkit.core import Cancelled, Task


def _sum(data):
    return sum(word[0] for word in struct.iter_unpack("<I", data)) & 0xFFFFFFFF


def _literal(data):
    assert 0 < len(data) <= 0x7FFF
    return struct.pack(">H", 0x8000 | len(data)) + data


def _container(frames, decoded=None):
    header = bytearray(128)
    header[:8], header[8:16] = b"RICOH\0\0\0", b"GR IIIx\0"
    header[0x38:0x3C] = bytes((1, 60, 11, 17))
    if decoded is not None:
        assert len(decoded) % 4 == 0
        decoded_crc = struct.pack("<I", -_sum(decoded) & 0xFFFFFFFF)
        frames += _literal(decoded_crc)
        decoded += decoded_crc
    data = bytes(header) + frames + b"\0\0"
    data += b"\0" * (-len(data) % 4)
    data += struct.pack("<I", -_sum(data) & 0xFFFFFFFF)
    return data, decoded


def _paths(tmp_path, data):
    source, destination = tmp_path / "camera.bin", tmp_path / "decoded"
    source.write_bytes(data)
    destination.mkdir()
    return source, destination


def test_literals_decode_and_keep_source_unchanged(tmp_path):
    data, decoded = _container(_literal(b"LinuxTestPayload"), b"LinuxTestPayload")
    source, destination = _paths(tmp_path, data)
    task = Task(lambda _: None)
    info = ricoh.identify(source, task)
    assert info["model"] == "GR IIIx"
    assert info["extractable"]
    report = ricoh.extract(source, destination, task)
    assert (destination / "payload.bin").read_bytes() == decoded
    assert report["sha256"] == hashlib.sha256(decoded).hexdigest()
    assert report["decoded_size"] == len(decoded)
    assert source.read_bytes() == data


def test_overlapping_reference_and_cross_frame_history(tmp_path):
    # After literal ABCD, distance 4/count 0 writes ABC; D is a literal.
    compressed = b"\x80\x00\x00\x04D"
    frames = _literal(b"ABCD") + struct.pack(">H", len(compressed)) + compressed
    data, decoded = _container(frames, b"ABCDABCD")
    source, destination = _paths(tmp_path, data)
    report = ricoh.extract(source, destination, Task(lambda _: None))
    assert (destination / "payload.bin").read_bytes() == decoded
    assert report["frames"] == 3


def test_extended_length_and_self_overlapping_run(tmp_path):
    # Literal A then distance 1, count 7 + 255 + 4 + 3 = 269 A bytes.
    compressed = b"\x40\x00A\x07\x01\xff\x04"
    frames = struct.pack(">H", len(compressed)) + compressed + _literal(b"BB")
    data, decoded = _container(frames, b"A" * 270 + b"BB")
    source, destination = _paths(tmp_path, data)
    ricoh.extract(source, destination, Task(lambda _: None))
    assert (destination / "payload.bin").read_bytes() == decoded


def test_zero_distance_frame_end_is_supported(tmp_path):
    compressed = b"\x08\x00ABCD\x00\x00"
    data, decoded = _container(struct.pack(">H", len(compressed)) + compressed, b"ABCD")
    source, destination = _paths(tmp_path, data)
    ricoh.extract(source, destination, Task(lambda _: None))
    assert (destination / "payload.bin").read_bytes() == decoded


@pytest.mark.parametrize("frames,error", [
    (b"\x00\x01X", "flags"),
    (b"\x00\x03\x80\x00\x00", "back reference"),
    (b"\x00\x05\x80\x00\x07\x01\xff", "extended"),
    (b"\x00\x04\x80\x00\x00\x01", "distance"),
    (b"\x00\x05\x80\x00\x00\x00X", "trailing frame data"),
    (b"\xff\xffX", "extent"),
])
def test_malformed_frames_fail_after_valid_container_checksum(tmp_path, frames, error):
    data, _ = _container(frames)
    source, destination = _paths(tmp_path, data)
    with pytest.raises(ValueError, match=error):
        ricoh.extract(source, destination, Task(lambda _: None))


def test_corrupted_container_and_payload_checksums_are_distinct(tmp_path):
    data, _ = _container(_literal(b"ABCD"), b"ABCD")
    damaged = bytearray(data)
    damaged[28] ^= 1
    source, destination = _paths(tmp_path, damaged)
    with pytest.raises(ValueError, match="container word checksum"):
        ricoh.extract(source, destination, Task(lambda _: None))
    data, _ = _container(_literal(b"ABCD"))
    source.write_bytes(data)
    with pytest.raises(ValueError, match="payload word checksum"):
        ricoh.extract(source, destination, Task(lambda _: None))


def test_decode_limit_and_existing_output_are_enforced(tmp_path, monkeypatch):
    data, _ = _container(_literal(b"ABCD"), b"ABCD")
    source, destination = _paths(tmp_path, data)
    monkeypatch.setattr(ricoh, "MAX_OUTPUT", 4)
    with pytest.raises(ValueError, match="2 GiB limit"):
        ricoh.extract(source, destination, Task(lambda _: None))
    original = (destination / "payload.bin").read_bytes()
    with pytest.raises(FileExistsError):
        ricoh.extract(source, destination, Task(lambda _: None))
    assert (destination / "payload.bin").read_bytes() == original


def test_cancellation_and_unknown_models(tmp_path):
    data, _ = _container(_literal(b"ABCD"), b"ABCD")
    source, destination = _paths(tmp_path, data)
    task = Task(lambda _: None)
    task.cancelled.set()
    with pytest.raises(Cancelled):
        ricoh.extract(source, destination, task)
    with pytest.raises(Cancelled):
        ricoh.identify(source, task)
    wrong_model = bytearray(data)
    wrong_model[8:20] = b"PENTAX K-3\0\0"
    source.write_bytes(wrong_model)
    assert ricoh.identify(source, Task(lambda _: None)) is None
    with pytest.raises(ValueError, match="Unsupported Ricoh"):
        ricoh.extract(source, destination, Task(lambda _: None))


def test_window_wrap_retains_previous_frame_history(tmp_path):
    literal = bytes(range(256)) * 40
    compressed = b"\x80\x00\x08\x00Z"  # distance 256, three copied bytes then Z.
    frames = _literal(literal) + struct.pack(">H", len(compressed)) + compressed
    data, decoded = _container(frames, literal + b"\x00\x01\x02Z")
    source, destination = _paths(tmp_path, data)
    ricoh.extract(source, destination, Task(lambda _: None))
    assert (destination / "payload.bin").read_bytes() == decoded


def _kernel_romfs():
    """Original tiny ROMFS containing /boot/Image-nolpae, without vendor data."""
    kernel = bytearray(1024)
    struct.pack_into("<III", kernel, 36, 0x016F2818, 0, len(kernel))
    image = bytearray(128) + kernel
    image[:8] = b"-rom1fs-"
    struct.pack_into(">I", image, 8, len(image))
    image[16:21] = b"test\0"
    for offset, following, special, size, name in [
        (32, 64 | 1, 32, 0, b"."),
        (64, 1, 96, 0, b"boot"),
        (96, 2, 0, len(kernel), b"Image-nolpae"),
    ]:
        struct.pack_into(">IIII", image, offset, following, special, size, 0)
        image[offset + 16:offset + 16 + len(name)] = name
        checksum = -sum(word[0] for word in struct.iter_unpack(">I", image[offset:offset + 32])) & 0xFFFFFFFF
        struct.pack_into(">I", image, offset + 12, checksum)
    checksum = -sum(word[0] for word in struct.iter_unpack(">I", image[:512])) & 0xFFFFFFFF
    struct.pack_into(">I", image, 12, checksum)
    return image, kernel


def test_boot_component_uses_checked_romfs_inode_not_embedded_magic(tmp_path):
    image, kernel = _kernel_romfs()
    data, decoded = _container(_literal(image), image)
    source, destination = _paths(tmp_path, data)
    report = ricoh.extract(source, destination, Task(lambda _: None))
    assert (destination / "payload.bin").read_bytes() == decoded
    assert (destination / "kernel.zImage").read_bytes() == kernel
    assert report["components"][0]["romfs_path"] == "/boot/Image-nolpae"
    assert report["components"][0]["size"] == len(kernel)
    assert report["components"][0]["payload_offset"] == 128


def test_magic_and_broken_inode_metadata_do_not_create_boot_components(tmp_path):
    image, _ = _kernel_romfs()
    image[96 + 16] = ord("Q")
    # Repair only the filesystem header sum; leave the inode sum damaged.
    struct.pack_into(">I", image, 12, 0)
    checksum = -sum(word[0] for word in struct.iter_unpack(">I", image[:512])) & 0xFFFFFFFF
    struct.pack_into(">I", image, 12, checksum)
    data, _ = _container(_literal(image), image)
    source, destination = _paths(tmp_path, data)
    report = ricoh.extract(source, destination, Task(lambda _: None))
    assert report["components"] == []
    assert [path.name for path in destination.iterdir()] == ["payload.bin"]


def test_components_count_toward_total_output_limit(tmp_path, monkeypatch):
    image, _ = _kernel_romfs()
    data, decoded = _container(_literal(image), image)
    source, destination = _paths(tmp_path, data)
    monkeypatch.setattr(ricoh, "MAX_OUTPUT", len(decoded))
    with pytest.raises(ValueError, match="total 2 GiB"):
        ricoh.extract(source, destination, Task(lambda _: None))
    assert not (destination / "kernel.zImage").exists()
