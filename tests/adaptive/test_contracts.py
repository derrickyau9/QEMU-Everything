from pathlib import Path
import json
import sqlite3
import struct

import pytest

from devkit.adaptive.contracts import SemanticGap
from devkit.adaptive.frontier import Frontier
from devkit.adaptive.inventory import Firmware
from devkit.adaptive.model import validate_model


def elf_image(entry=0x40010000):
    ident = b'\x7fELF\x02\x01\x01' + bytes(9)
    header = struct.pack('<HHIQQQIHHHHHH', 2, 183, 1, entry, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    program = struct.pack('<IIQQQQQQ', 1, 5, 120, entry, entry, 4, 4, 4)
    return ident + header + program + b'\x1f\x20\x03\xd5'


def test_unknown_hash_enters_structural_loader_and_relocated_identity(tmp_path):
    for index, entry in enumerate((0x40010000, 0x40301234)):
        binary = tmp_path / f'unknown-{index}'
        binary.write_bytes(elf_image(entry))
        firmware = Firmware(binary)
        assert firmware.entry == entry
        assert not firmware.manifest()['profile_matched']
        assert firmware.read(entry, 4) == b'\x1f\x20\x03\xd5'
        assert firmware.is_code(entry) and not firmware.is_code(entry + 4)


def test_missing_data_and_non_ram_loading_are_explicit(tmp_path):
    path = tmp_path / 'unrecognized'
    path.write_bytes(b'\xff' * 64)
    with pytest.raises(SemanticGap, match='ELF64'):
        Firmware(path)
    path.write_bytes(elf_image(0x0b110000))
    with pytest.raises(SemanticGap, match='RAM envelope'):
        Firmware(path)


def test_model_ast_cannot_target_pixels_or_include_host_code():
    model = dict(kind='xor_latch', write_address='0xb000004', read_address='0xb000008',
                 mask='0x3f', xor_value='0x23', value='0x0')
    assert validate_model(model) == model
    with pytest.raises(ValueError, match='AST'):
        validate_model(dict(model, script='anything'))
    with pytest.raises(ValueError, match='aperture'):
        validate_model(dict(model, write_address='0xb110000'))


def test_unfinished_tasks_are_durable_and_timeout_is_not_exclusion(tmp_path):
    path = tmp_path / 'frontier.sqlite'
    frontier = Frontier(path)
    task = frontier.begin('SOLVE', {'attempt': 1, 'remaining_candidates': [1, 2]})
    frontier.close()
    with sqlite3.connect(path) as database:
        status, continuation = database.execute('SELECT status,continuation FROM tasks WHERE id=?', (task,)).fetchone()
        assert status == 'pending'
        assert json.loads(continuation)['remaining_candidates'] == [1, 2]
    resumed = Frontier(path)
    assert resumed.begin('SOLVE', {'remaining_candidates': [1, 2], 'attempt': 1}) == task
    run_task, directory = resumed.next_directory(tmp_path, 'retry', {'inputs': [1], 'model_hash': 'abc'})
    directory.mkdir(parents=True)
    same_task, next_attempt = resumed.next_directory(tmp_path, 'retry', {'inputs': [1], 'model_hash': 'abc'})
    assert same_task == run_task and next_attempt != directory
    resumed.close()
