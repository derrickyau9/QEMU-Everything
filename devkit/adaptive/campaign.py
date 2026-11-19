"""First end-to-end MOLTEN chain: observe, solve, refute, synthesize, replay."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
from .backend import Backend, BackendFailure
from .contracts import BudgetExhausted, Outcome, SemanticGap, digest, identity, save_json
from .frontier import Frontier
from .inventory import Firmware

TRAINING = [1, 2, 3]
HELD_OUT = [[2, 1, 3, 1], [1, 1, 2, 3, 2, 3], [2, 3, 2, 1, 1, 3]]


def validate_probe(root, baseline):
    probe_log = (Path(baseline['directory']) / 'qemu.log').read_text()
    probe_lines = [line.partition('MOLTEN_PROBE ')[2] for line in probe_log.splitlines()
                   if line.startswith('MOLTEN_PROBE ')]
    if len(probe_lines) != 1:
        raise SemanticGap('Runtime plugin capability probe did not report exactly once')
    probe = json.loads(probe_lines[0])
    required = ('register_read', 'register_write_same_value', 'virtual_read', 'virtual_write',
                'translate', 'physical_verified', 'virtual_restore_verified', 'physical_restore_verified')
    if probe['api'] != 5 or not all(probe[k] for k in required) or any(probe[k] != 0 for k in
            ('physical_read_result', 'physical_write_result', 'physical_verify_result')):
        raise SemanticGap('Pinned QEMU plugin capability probe failed')
    save_json(root / 'plugin-capability-probe.json', probe)


def explore(source, campaign, backend_manifest, *, resume=False, log=print):
    # This backend is Linux-only; flock isolates a campaign without reusing
    # GuestLock(device) or any Sony worker paths.
    if os.name != 'posix':
        raise ValueError('MOLTEN v0.1 runs in Linux/WSL; see docs/molten-minimal-chain.md')
    import fcntl
    from .solve import analyze_read, synthesize
    from .replay import assert_run

    source, root = Path(source).resolve(), Path(campaign).resolve()
    if root.exists() and not resume:
        raise ValueError('Campaign already exists; use --resume or a fresh directory')
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'campaign.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Campaign is owned by another worker') from error
        state_path = root / 'campaign.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else dict(schema_version=1, samples=[])
        source_hash = digest(source)
        if state.get('source_sha256', source_hash) != source_hash:
            raise ValueError('Resume source identity mismatch')
        state['source_sha256'] = source_hash
        save_json(state_path, state)
        frontier = Frontier(root / 'frontier.sqlite')
        try:
            save_json(root / 'input-manifest.json', dict(source=str(source), source_sha256=source_hash,
                                                        input_level='F0', profile_matched=False))
            frozen = root / 'inputs' / f'{source_hash}.elf'
            frozen.parent.mkdir(exist_ok=True)
            if not frozen.exists():
                shutil.copyfile(source, frozen)
            if digest(frozen) != source_hash:
                raise ValueError('Campaign input copy identity changed')
            firmware = Firmware(frozen)
            save_json(root / 'input-manifest.json', firmware.manifest())
            backend = Backend(backend_manifest)
            if state.get('backend_binary_sha256', backend.manifest['binary_sha256']) != backend.manifest['binary_sha256']:
                raise ValueError('Resume backend identity mismatch')
            state['backend_binary_sha256'] = backend.manifest['binary_sha256']
            save_json(root / 'backend-capabilities.json', backend.capabilities())
            # A failed probe cannot be bypassed through a persisted baseline.
            # Check the evidence on every resume, including completed runs.
            if 'baseline' in state:
                validate_probe(root, state['baseline'])
            if state.get('outcome') == Outcome.SUCCESS_TESTED and (root / 'report.json').exists():
                report = json.loads((root / 'report.json').read_text())
                recipe = json.loads(Path(report['recipe']).read_text())
                if (identity(recipe['model']) != recipe['model_sha256'] or recipe['model'] != state.get('model')
                        or recipe['model'] != report['model'] or digest(Path(report['recipe']).parent / recipe['firmware']) != source_hash
                        or digest(Path(report['recipe'])) != state.get('recipe_sha256')
                        or state.get('recipe_sha256') != report.get('recipe_sha256')):
                    raise ValueError('Completed campaign artifact identity changed')
                return report

            def run(label, inputs=(), **kwargs):
                continuation = dict(inputs=list(inputs), model_hash=identity(kwargs.get('model')),
                                    firmware_sha256=firmware.sha256)
                task, path = frontier.next_directory(root, label, continuation)
                result = backend.run(firmware, path, inputs, **kwargs)
                frontier.finish(task, dict(result=str(path / 'result.json'), reason=result['reason']))
                return result

            if 'baseline' not in state:
                log('Observe: execute unknown ELF with zero MMIO responses')
                baseline = run('baseline', probe=True)
                if not baseline['blockers']:
                    if baseline['reason'] in ('wall_budget', 'event_budget', 'budget'):
                        raise BudgetExhausted('Baseline reached its execution budget; pending execution remains')
                    raise SemanticGap('No supported unresolved MMIO polling blocker found within budget')
                validate_probe(root, baseline)
                state['baseline'] = baseline
                save_json(state_path, state)
            if not state['samples']:
                log('Solve: derive a local bit-vector condition from the captured load boundary')
                task = frontier.begin('SOLVE', dict(boundary=state['baseline']['blockers'][-1]))
                sample = analyze_read(firmware, state['baseline']['blockers'][-1])
                state['samples'].append(sample)
                save_json(root / 'constraints' / (identity(sample) + '.json'), sample)
                save_json(state_path, state)
                frontier.finish(task, dict(candidate=sample['value'], mask=sample['mask']))
            if 'constant_trial' not in state:
                constant = synthesize(state['samples'])
                log('Counterexample: replay the smallest model with a second, different input')
                trial = run('constant-trial', TRAINING, model=constant)
                if trial['reason'] in ('wall_budget', 'event_budget', 'budget'):
                    raise BudgetExhausted('Constant model trial reached its execution budget')
                state.update(constant_model=constant, constant_trial=trial,
                             constant_model_rejected=trial['reason'] == 'unresolved_poll')
                save_json(state_path, state)
            if 'refinement_run' not in state:
                log('Refine: solve new observations and synthesize one shared write/read state model')
                def respond(event):
                    sample = analyze_read(firmware, event)
                    if identity(sample) not in {identity(s) for s in state['samples']}:
                        state['samples'].append(sample)
                        save_json(root / 'constraints' / (identity(sample) + '.json'), sample)
                        save_json(state_path, state)
                    return sample['value']
                concrete = run('constraint-replay', TRAINING, responder=respond)
                if concrete['reason'] in ('wall_budget', 'event_budget', 'budget'):
                    raise BudgetExhausted('Constraint replay reached its execution budget')
                assert_run(concrete, TRAINING)
                state['refinement_run'] = concrete
                save_json(state_path, state)
            task = frontier.begin('SYNTHESIZE', dict(samples=len(state['samples']), grammar=['constant', 'xor_latch']))
            model = synthesize(state['samples'])
            state['model'] = model
            save_json(root / 'models' / identity(model) / 'model.json', model)
            save_json(state_path, state)
            frontier.finish(task, dict(model_hash=identity(model)))
            log('Verify: freeze the native model, compare no-input/A/B, and restart without the solver')
            controls = [run('control-' + label, sequence, model=model)
                        for label, sequence in [('none', []), ('next', [1]), ('enter', [2]), ('roundtrip', [2, 3])]]
            for result, sequence in zip(controls, ([], [1], [2], [2, 3])):
                assert_run(result, sequence)
            initial = controls[0]['frames'][0]['pixels_sha256']
            if any(result['frames'][0]['pixels_sha256'] != initial for result in controls):
                raise ValueError('REPLAY_DIVERGED: initial original surface differs')
            if len({result['frames'][-1]['pixels_sha256'] for result in controls[:3]}) != 3:
                raise ValueError('Input A/B/no-input did not cause distinct original redraws')
            if controls[3]['frames'][-1]['pixels_sha256'] != initial:
                raise ValueError('Enter/back did not restore the initial surface')
            recipe_dir = root / 'recipe'
            recipe_dir.mkdir(exist_ok=True)
            shutil.copyfile(frozen, recipe_dir / 'firmware.elf')
            recipe = dict(schema_version=1, contract='molten-micro-menu-v1', scope='unclassified',
                          firmware='firmware.elf', firmware_sha256=source_hash, input_level='F0',
                          backend_manifest=str(Path(backend_manifest).resolve()),
                          backend_binary_sha256=backend.manifest['binary_sha256'],
                          model=model, model_sha256=identity(model), pressure=2, interventions=[],
                          checkpoint='unsupported', replay='clean-restart',
                          verification_sequences=HELD_OUT,
                          control_sequences=[[], [1], [2], [2, 3]],
                          control_signatures=[result['replay_signature'] for result in controls],
                          verification_contract=dict(clean_restarts_per_sequence=5, sequences=3,
                                                     control_comparison=True, enter_back=True,
                                                     required_soak_seconds=0,
                                                     scope='finite microfirmware suite; Sony acceptance is separate'))
            recipe_path = recipe_dir / 'recipe.json'
            save_json(recipe_path, recipe)
            signatures, replays = [], []
            for sequence_index, sequence in enumerate(HELD_OUT):
                expected = None
                for repeat in range(5):
                    replay_task, output = frontier.next_directory(root, f'independent-{sequence_index}-{repeat}',
                                                                  dict(inputs=sequence, model_hash=identity(model)))
                    command = [sys.executable, '-m', 'devkit.adaptive.replay', str(recipe_path),
                               '--output', str(output), '--inputs', json.dumps(sequence)]
                    if expected:
                        command += ['--expected-signature', expected]
                    completed = subprocess.run(command, text=True, capture_output=True, timeout=35)
                    if completed.returncode:
                        raise ValueError('Independent replay failed: ' + completed.stderr[-2000:])
                    evidence = json.loads(completed.stdout)
                    if evidence['solver_modules_imported']:
                        raise ValueError('Independent replay unexpectedly imported a solver/disassembler')
                    expected = evidence['signature']
                    replays.append(dict(sequence=sequence_index, repeat=repeat, signature=expected, directory=str(output)))
                    frontier.finish(replay_task, evidence)
                signatures.append(expected)
            recipe['expected_signatures'] = signatures
            save_json(recipe_path, recipe)
            recipe_sha256 = digest(recipe_path)
            report = dict(schema_version=1, outcome=Outcome.SUCCESS_TESTED, scope='unclassified',
                          evaluation_domain='AArch64 research microfirmware', input_level='F0',
                          profile_matched=False, pressure_max=2, firmware_instructions_modified=False,
                          checkpoints_supported=False, original_ui_levels=['G0', 'G1', 'G2', 'G3', 'G4', 'G5'],
                          verification_contract=recipe['verification_contract'],
                          constant_model_rejected=state['constant_model_rejected'],
                          automatic_constraints=len(state['samples']), model=model,
                          recipe=str(recipe_path), recipe_sha256=recipe_sha256,
                          controls=[r['directory'] for r in controls], replays=replays,
                          incomplete_obligations=['Sony ordinary menu integration', '10 minute Sony acceptance soak',
                                                  'general UI target discovery', 'full checkpoints', 'DMA/IRQ/IPC models',
                                                  'fair multi-world scheduler', 'Triton semantic differential gate'],
                          completeness='bounded grammar and finite tests; no universal claim')
            state.update(outcome=Outcome.SUCCESS_TESTED, report=str(root / 'report.json'), recipe_sha256=recipe_sha256)
            save_json(state_path, state)
            save_json(root / 'report.json', report)
            return report
        except (SemanticGap, OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            if isinstance(error, (BudgetExhausted, subprocess.TimeoutExpired)):
                outcome = Outcome.BUDGET_EXHAUSTED
            elif isinstance(error, SemanticGap):
                outcome = Outcome.UNSUPPORTED_SEMANTICS
            elif isinstance(error, (BackendFailure, OSError)):
                outcome = Outcome.BACKEND_FAILED
            else:
                outcome = Outcome.VERIFICATION_FAILED
            if 'REPLAY_DIVERGED' in str(error):
                outcome = Outcome.REPLAY_DIVERGED
            report = dict(schema_version=1, outcome=outcome, reason=str(error), scope='unclassified',
                          frontier=str(root / 'frontier.sqlite'), resumable=True, completeness='incomplete')
            state.update(outcome=outcome, last_error=str(error))
            save_json(state_path, state)
            save_json(root / 'report.json', report)
            return report
        finally:
            frontier.close()
