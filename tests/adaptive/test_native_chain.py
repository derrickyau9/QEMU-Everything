"""Real QEMU integration and negative oracles, never a mock execution VM."""
from pathlib import Path
import importlib.util
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys

import pytest

from devkit.adaptive.backend import Backend
from devkit.adaptive.campaign import explore
from devkit.adaptive.contracts import Outcome, identity
from devkit.adaptive.inventory import Firmware
from devkit.adaptive.replay import replay_once
from devkit.adaptive.solve import analyze_read

HERE = Path(__file__).resolve().parents[2]
MANIFEST = os.environ.get('MOLTEN_BACKEND_MANIFEST')
pytestmark = [pytest.mark.molten_native, pytest.mark.skipif(
    os.name != 'posix' or not MANIFEST or not shutil.which('aarch64-linux-gnu-gcc'),
    reason='Set MOLTEN_BACKEND_MANIFEST and run in Linux with the cross compiler')]


def fixture_binary(root, seed, variant='menu'):
    spec = importlib.util.spec_from_file_location('fixture_builder', HERE / 'research/fixtures/aarch64_menu/build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    output = root / f'source-{seed}-{variant}'
    builder.build(output, seed, variant)
    truth = json.loads((output / 'truth.json').read_text())
    # Isolate the algorithm from source, symbols, file name hints, and truth.
    opaque = root / 'opaque'
    opaque.mkdir(exist_ok=True)
    path = opaque / f'{seed}-{variant}.unknown'
    shutil.copyfile(output / 'firmware.elf', path)
    return path, truth


@pytest.mark.parametrize('seed', [731, 104729, 8675309])
def test_random_layout_real_search_and_solver_free_replay(tmp_path, seed):
    binary, truth = fixture_binary(tmp_path, seed)
    root = tmp_path / 'campaign'
    report = explore(binary, root, MANIFEST, log=lambda _: None)
    assert report['outcome'] == Outcome.SUCCESS_TESTED, report
    assert report['constant_model_rejected']
    assert not report['profile_matched']
    assert report['scope'] == 'unclassified'
    assert report['model']['kind'] == 'xor_latch'
    model = report['model']
    assert int(model['read_address'], 0) == truth['status_address']
    assert int(model['write_address'], 0) == truth['config_address']
    assert int(model['mask'], 0) == truth['mask']
    # Evaluator-only check against independent generator truth for unseen data.
    rng = random.Random(seed + 1)
    for _ in range(64):
        value = rng.getrandbits(32)
        assert ((value ^ int(model['xor_value'], 0)) & int(model['mask'], 0)) == ((value ^ truth['xor_value']) & truth['mask'])
    assert len(report['replays']) == 15
    for sequence in range(3):
        assert len({r['signature'] for r in report['replays'] if r['sequence'] == sequence}) == 1
    for replay in report['replays']:
        result = json.loads((Path(replay['directory']) / 'result.json').read_text())
        assert not result['solver_modules_imported']
        events = [json.loads(line) for line in (Path(replay['directory']) / 'events.jsonl').read_text().splitlines()]
        assert all(e.get('native_model') for e in events if e['kind'] == 'mmio_read')
    probe = json.loads((root / 'plugin-capability-probe.json').read_text())
    assert probe['api'] == 5 and probe['physical_verified'] and probe['register_write_same_value']
    workers = sorted((root / 'workers/worker-0').iterdir())
    resumed = explore(binary, root, MANIFEST, resume=True, log=lambda _: None)
    assert resumed['model'] == report['model']
    assert workers == sorted((root / 'workers/worker-0').iterdir())


def test_completion_without_pixels_and_static_logo_rejected(tmp_path):
    for variant, expected in [('completion-only', 'Present without CPU pixel writes'),
                              ('static-logo', 'distinct original redraws')]:
        location = tmp_path / variant
        location.mkdir()
        binary, _ = fixture_binary(location, 42, variant)
        report = explore(binary, location / 'campaign', MANIFEST, log=lambda _: None)
        assert report['outcome'] != Outcome.SUCCESS_TESTED
        assert expected in report['reason']


def test_missing_local_semantics_preserves_frontier(tmp_path):
    binary, _ = fixture_binary(tmp_path, 53, 'unsupported-window')
    root = tmp_path / 'campaign'
    report = explore(binary, root, MANIFEST, log=lambda _: None)
    assert report['outcome'] == Outcome.UNSUPPORTED_SEMANTICS
    assert 'rev' in report['reason']
    with sqlite3.connect(root / 'frontier.sqlite') as database:
        assert database.execute("SELECT count(*) FROM tasks WHERE status='pending'").fetchone()[0] >= 1
    assert (root / 'campaign.json').is_file()


def test_idle_wait_is_budget_exhaustion_not_deadlock_proof(tmp_path, monkeypatch):
    binary, _ = fixture_binary(tmp_path, 59, 'idle-loop')
    result = Backend(MANIFEST).run(Firmware(binary), tmp_path / 'idle', wall_seconds=0.5)
    assert result['reason'] == 'wall_budget'
    assert not result['blockers']
    assert not result['frames']
    original = Backend.run
    def bounded(self, firmware, directory, inputs=(), **kwargs):
        return original(self, firmware, directory, inputs, wall_seconds=0.5, **kwargs)
    monkeypatch.setattr(Backend, 'run', bounded)
    report = explore(binary, tmp_path / 'campaign', MANIFEST, log=lambda _: None)
    assert report['outcome'] == Outcome.BUDGET_EXHAUSTED


def test_recipe_identity_and_replay_divergence(tmp_path):
    binary, _ = fixture_binary(tmp_path, 61)
    report = explore(binary, tmp_path / 'campaign', MANIFEST, log=lambda _: None)
    assert report['outcome'] == Outcome.SUCCESS_TESTED, report
    recipe = Path(report['recipe'])
    with pytest.raises(ValueError, match='REPLAY_DIVERGED'):
        replay_once(recipe, tmp_path / 'wrong-signature', expected_signature='0' * 64)
    bundle = tmp_path / 'changed-recipe'
    shutil.copytree(recipe.parent, bundle)
    (bundle / 'firmware.elf').write_bytes(b'changed identity')
    with pytest.raises(ValueError, match='firmware identity mismatch'):
        replay_once(bundle / 'recipe.json', tmp_path / 'must-not-launch')
    assert not (tmp_path / 'must-not-launch').exists()
    edited = json.loads(recipe.read_text())
    edited['model']['xor_value'] = hex(int(edited['model']['xor_value'], 0) ^ 1)
    edited['model_sha256'] = identity(edited['model'])
    recipe.write_text(json.dumps(edited))
    resumed = explore(binary, tmp_path / 'campaign', MANIFEST, resume=True, log=lambda _: None)
    assert resumed['outcome'] == Outcome.VERIFICATION_FAILED
    assert 'identity changed' in resumed['reason']


def test_resume_cannot_skip_failed_capability_probe(tmp_path):
    binary, _ = fixture_binary(tmp_path, 73)
    root = tmp_path / 'campaign'
    report = explore(binary, root, MANIFEST, log=lambda _: None)
    assert report['outcome'] == Outcome.SUCCESS_TESTED, report
    state = json.loads((root / 'campaign.json').read_text())
    log_path = Path(state['baseline']['directory']) / 'qemu.log'
    log_path.write_text(log_path.read_text().replace('"register_read":true', '"register_read":false'))
    workers = sorted((root / 'workers/worker-0').iterdir())
    resumed = explore(binary, root, MANIFEST, resume=True, log=lambda _: None)
    assert resumed['outcome'] == Outcome.UNSUPPORTED_SEMANTICS
    assert 'capability probe failed' in resumed['reason']
    assert workers == sorted((root / 'workers/worker-0').iterdir())


def test_replay_import_guard_refuses_any_solver_access(tmp_path):
    binary, _ = fixture_binary(tmp_path, 67)
    report = explore(binary, tmp_path / 'campaign', MANIFEST, log=lambda _: None)
    assert report['outcome'] == Outcome.SUCCESS_TESTED, report
    script = '''import sys, importlib.abc
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('z3', 'triton', 'capstone'):
            raise RuntimeError('Solver/disassembler import forbidden: ' + fullname)
sys.meta_path.insert(0, Deny())
from devkit.adaptive.replay import replay_once
result = replay_once(sys.argv[1], sys.argv[2])
assert not result['solver_modules_imported']
'''
    completed = subprocess.run([sys.executable, '-c', script, report['recipe'], str(tmp_path / 'guarded')],
                               cwd=HERE, text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
