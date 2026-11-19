"""Replay a frozen recipe. This module has NO solver/disassembler dependency."""
from pathlib import Path
import argparse
import json
import sys
from .backend import Backend
from .contracts import Outcome, digest, identity, save_json
from .inventory import Firmware
from .model import validate_model


def assert_run(result, inputs):
    delivered = [key for key in inputs if key]
    if result['reason'] != 'inputs_complete' or result['exit_code'] != 0:
        raise ValueError('Replay failed to finish: ' + result['reason'])
    if result['input_deliveries'] != delivered or len(result['frames']) != len(delivered) + 1:
        raise ValueError('Original event consumption/repaint contract failed')
    if not all(frame['pixel_writes'] and frame['writers'] for frame in result['frames']):
        raise ValueError('Frame has no original CPU pixel provenance')


def replay_once(recipe_path, output, *, inputs=None, backend_manifest=None, expected_signature=None):
    recipe_path = Path(recipe_path).resolve()
    recipe = json.loads(recipe_path.read_text())
    if recipe.get('schema_version') != 1 or recipe.get('contract') != 'molten-micro-menu-v1':
        raise ValueError('Unsupported recipe contract')
    model = validate_model(recipe['model'])
    if identity(model) != recipe['model_sha256']:
        raise ValueError('Recipe model identity mismatch')
    firmware_path = (recipe_path.parent / recipe['firmware']).resolve()
    if recipe_path.parent not in firmware_path.parents:
        raise ValueError('Recipe firmware must be bundled in its directory')
    if digest(firmware_path) != recipe['firmware_sha256']:
        raise ValueError('Recipe firmware identity mismatch')
    backend = Backend(backend_manifest or recipe['backend_manifest'])
    if backend.manifest['binary_sha256'] != recipe['backend_binary_sha256']:
        raise ValueError('Replay requires the same backend binary identity')
    firmware = Firmware(firmware_path)
    selected = list(recipe['verification_sequences'][0] if inputs is None else inputs)
    result = backend.run(firmware, output, selected, model=model)
    assert_run(result, selected)
    if expected_signature and result['replay_signature'] != expected_signature:
        raise ValueError('REPLAY_DIVERGED: CPU/register/event/surface signature differs')
    imported = [name for name in ('z3', 'triton', 'capstone') if name in sys.modules]
    result.update(solver_modules_imported=imported, independent_restart=True)
    save_json(Path(output) / 'result.json', result)
    return result


def _signature_list(value, count, label):
    if (not isinstance(value, list) or len(value) != count or
            any(not isinstance(item, str) or len(item) != 64 or
                any(character not in '0123456789abcdef' for character in item) for item in value)):
        raise ValueError(f'Recipe {label} must contain exactly {count} SHA-256 signatures')
    return value


def _assert_interaction_controls(controls):
    initial = controls[0]['frames'][0]['pixels_sha256']
    if any(result['frames'][0]['pixels_sha256'] != initial for result in controls):
        raise ValueError('REPLAY_DIVERGED: initial original surface differs between controls')
    if len({result['frames'][-1]['pixels_sha256'] for result in controls[:3]}) != 3:
        raise ValueError('Input A/B/no-input did not cause distinct original redraws')
    if controls[3]['frames'][-1]['pixels_sha256'] != initial:
        raise ValueError('Enter/back did not restore the initial surface')


def replay(recipe_path, output, *, verify_interaction=False, backend_manifest=None):
    recipe = json.loads(Path(recipe_path).read_text())
    held_out = recipe.get('verification_sequences')
    if not isinstance(held_out, list) or not held_out:
        raise ValueError('Recipe must contain nonempty held-out verification sequences')
    signatures = _signature_list(recipe.get('expected_signatures'), len(held_out), 'expected_signatures')
    control_sequences = [[], [1], [2], [2, 3]]
    control_signatures = None
    if verify_interaction:
        if recipe.get('control_sequences') != control_sequences:
            raise ValueError('Recipe interaction controls must be no-input, Next, Enter, Enter/Back')
        control_signatures = _signature_list(recipe.get('control_signatures'), 4, 'control_signatures')
    sequences = recipe['verification_sequences'] if verify_interaction else recipe['verification_sequences'][:1]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    results, controls = [], []
    if verify_interaction:
        control_runs = []
        for index, (label, sequence) in enumerate(zip(('none', 'next', 'enter', 'roundtrip'), control_sequences)):
            directory = output / ('control-' + label)
            result = replay_once(recipe_path, directory, inputs=sequence, backend_manifest=backend_manifest,
                                 expected_signature=control_signatures[index])
            control_runs.append(result)
            controls.append(dict(sequence=sequence, signature=result['replay_signature'], directory=str(directory)))
        _assert_interaction_controls(control_runs)
    repeats = 5 if verify_interaction else 1
    for sequence_index, sequence in enumerate(sequences):
        expected = signatures[sequence_index]
        for repeat in range(repeats):
            result = replay_once(recipe_path, output / f'sequence-{sequence_index}-repeat-{repeat}',
                                 inputs=sequence, backend_manifest=backend_manifest, expected_signature=expected)
            expected = result['replay_signature']
            results.append(dict(sequence=sequence, repeat=repeat, signature=expected))
    actual_contract = dict(clean_restarts_per_sequence=repeats, sequences=len(sequences),
                           control_comparison=verify_interaction, enter_back=verify_interaction,
                           required_soak_seconds=0, executed_soak_seconds=0,
                           scope='finite microfirmware replay; Sony acceptance is separate')
    report = dict(outcome=Outcome.SUCCESS_TESTED, scope=recipe['scope'], results=results, controls=controls,
                  solver_required=False, clean_restarts_total=len(results) + len(controls),
                  verification_contract=actual_contract)
    save_json(output / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recipe', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--backend-manifest', type=Path)
    parser.add_argument('--inputs', help='Internal bounded input sequence as a JSON array')
    parser.add_argument('--expected-signature')
    parser.add_argument('--verify-interaction', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.inputs is not None:
            result = replay_once(args.recipe, args.output, inputs=json.loads(args.inputs),
                                 backend_manifest=args.backend_manifest, expected_signature=args.expected_signature)
            print(json.dumps(dict(signature=result['replay_signature'], solver_modules_imported=result['solver_modules_imported'])))
        else:
            print(json.dumps(replay(args.recipe, args.output, verify_interaction=args.verify_interaction,
                                    backend_manifest=args.backend_manifest), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
