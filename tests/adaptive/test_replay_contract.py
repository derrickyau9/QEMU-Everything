"""Contract validation/oracle tests; native execution is covered separately."""
import json

import pytest

from devkit.adaptive import replay


def recipe_file(tmp_path):
    recipe = dict(scope='unclassified', verification_sequences=[[1, 2], [2, 3], [1, 3]],
                  expected_signatures=['a' * 64, 'b' * 64, 'c' * 64],
                  control_sequences=[[], [1], [2], [2, 3]],
                  control_signatures=['d' * 64, 'e' * 64, 'f' * 64, '0' * 64],
                  verification_contract=dict(clean_restarts_per_sequence=99, sequences=99,
                                             control_comparison=True, enter_back=True,
                                             required_soak_seconds=600))
    path = tmp_path / 'recipe.json'
    path.write_text(json.dumps(recipe))
    return path, recipe


def record_replays(monkeypatch, *, defect=None):
    calls = []

    def recorded(recipe_path, output, *, inputs, backend_manifest, expected_signature):
        sequence = tuple(inputs)
        calls.append((sequence, expected_signature))
        initial = 'original-initial-surface'
        if sequence == ():
            surfaces = [initial]
        elif sequence == (1,):
            surfaces = [initial, 'original-next-surface']
        elif sequence == (2,):
            surfaces = [initial, 'original-enter-surface']
        elif sequence == (2, 3):
            surfaces = [initial, 'original-enter-surface', initial]
        else:
            surfaces = [initial] * (len(sequence) + 1)
        if defect == 'static':
            surfaces = [initial] * len(surfaces)
        elif defect == 'roundtrip' and sequence == (2, 3):
            surfaces[-1] = 'wrong-return-surface'
        elif defect == 'initial' and sequence == (1,):
            surfaces[0] = 'different-initial-surface'
        return dict(replay_signature=expected_signature,
                    frames=[dict(pixels_sha256=surface) for surface in surfaces])

    monkeypatch.setattr(replay, 'replay_once', recorded)
    return calls


@pytest.mark.parametrize('signatures', [None, [], ['a' * 64], ['invalid'] * 3])
def test_public_replay_requires_full_frozen_signatures_before_launch(tmp_path, monkeypatch, signatures):
    path, recipe = recipe_file(tmp_path)
    recipe['expected_signatures'] = signatures
    path.write_text(json.dumps(recipe))
    calls = record_replays(monkeypatch)
    with pytest.raises(ValueError, match='expected_signatures'):
        replay.replay(path, tmp_path / 'output', verify_interaction=True)
    assert not calls
    assert not (tmp_path / 'output').exists()


def test_interaction_reexecutes_controls_and_all_frozen_sequences(tmp_path, monkeypatch):
    path, recipe = recipe_file(tmp_path)
    calls = record_replays(monkeypatch)
    result = replay.replay(path, tmp_path / 'output', verify_interaction=True)
    assert calls[:4] == list(zip(map(tuple, recipe['control_sequences']), recipe['control_signatures']))
    assert len(calls) == 19
    assert len(result['results']) == 15 and len(result['controls']) == 4
    assert result['verification_contract']['clean_restarts_per_sequence'] == 5
    assert result['verification_contract']['sequences'] == 3
    assert result['verification_contract']['control_comparison']
    assert result['clean_restarts_total'] == 19


@pytest.mark.parametrize('defect, message', [('static', 'distinct original redraws'),
                                          ('roundtrip', 'restore the initial surface'),
                                          ('initial', 'REPLAY_DIVERGED')])
def test_frozen_controls_cannot_hide_noninteractive_surfaces(tmp_path, monkeypatch, defect, message):
    path, _ = recipe_file(tmp_path)
    calls = record_replays(monkeypatch, defect=defect)
    with pytest.raises(ValueError, match=message):
        replay.replay(path, tmp_path / 'output', verify_interaction=True)
    assert len(calls) == 4
    assert not (tmp_path / 'output/report.json').exists()


def test_short_replay_reports_only_checks_actually_executed(tmp_path, monkeypatch):
    path, _ = recipe_file(tmp_path)
    calls = record_replays(monkeypatch)
    result = replay.replay(path, tmp_path / 'output')
    assert len(calls) == 1
    actual = result['verification_contract']
    assert actual['clean_restarts_per_sequence'] == actual['sequences'] == 1
    assert not actual['control_comparison'] and not actual['enter_back']
    assert actual['required_soak_seconds'] == actual['executed_soak_seconds'] == 0


def test_interaction_requires_frozen_control_signatures_before_launch(tmp_path, monkeypatch):
    path, recipe = recipe_file(tmp_path)
    recipe.pop('control_signatures')
    path.write_text(json.dumps(recipe))
    calls = record_replays(monkeypatch)
    with pytest.raises(ValueError, match='control_signatures'):
        replay.replay(path, tmp_path / 'output', verify_interaction=True)
    assert not calls
