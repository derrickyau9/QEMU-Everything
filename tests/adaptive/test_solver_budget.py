"""An unresolved SMT attempt must remain resumable rather than become UNSAT."""
from types import SimpleNamespace

import pytest

from devkit.adaptive import solve
from devkit.adaptive.contracts import BudgetExhausted, SemanticGap

z3 = pytest.importorskip('z3')
pytest.importorskip('capstone')


class PollWindow:
    base = 0x40010000
    # ldr w9, [x10]; and w9, w9, w11; cmp w9, w12; b.ne start
    data = bytes.fromhex('490140b929010b0a3f010c6ba1ffff54')

    def read(self, address, size):
        offset = address - self.base
        return self.data[offset:offset + size]


def read_event():
    registers = ['0x0'] * 32
    registers[10:13] = ['0xb000008', '0x3f', '0x23']
    return dict(pc=hex(PollWindow.base), address='0xb000008', width_bits=32,
                registers=registers, preceding_write=dict(address='0xb000004', value=0))


def controlled_solver(monkeypatch, *, unknown_check=None, after_check=None):
    """Keep the real bit-vector engine, controlling only its termination status."""
    constructor = z3.Solver
    checks, timeouts = [], []

    class Solver:
        def __init__(self):
            self.real = constructor()

        def __getattr__(self, name):
            return getattr(self.real, name)

        def set(self, **options):
            timeouts.append(options['timeout'])
            self.real.set(**options)

        def check(self):
            checks.append(len(checks) + 1)
            if after_check:
                after_check()
            if len(checks) == unknown_check:
                return z3.unknown
            return self.real.check()

        def reason_unknown(self):
            return 'controlled timeout'

    monkeypatch.setattr(z3, 'Solver', Solver)
    return checks, timeouts


def test_poll_window_still_solves_real_bitvector_condition():
    sample = solve.analyze_read(PollWindow(), read_event())
    assert sample['value'] == 0x23
    assert sample['mask'] == 0x3f


@pytest.mark.parametrize('unknown_check', [1, 2, 34])
def test_unknown_in_predicate_influence_or_projection_keeps_continuation(monkeypatch, unknown_check):
    checks, _ = controlled_solver(monkeypatch, unknown_check=unknown_check)
    with pytest.raises(BudgetExhausted, match='controlled timeout'):
        solve.analyze_read(PollWindow(), read_event())
    assert len(checks) == unknown_check


def test_model_parameter_unknown_is_not_grammar_rejection(monkeypatch):
    samples = []
    for written, accepted in ((0, 0x23), (1, 0x22)):
        samples.append(dict(mask=0x3f, read_address='0xb000008',
                            preceding_write=dict(address='0xb000004', value=written),
                            constraint_smt2='(declare-fun mmio_value () (_ BitVec 32))\n'
                            f'(assert (= mmio_value (_ bv{accepted} 32)))'))
    checks, _ = controlled_solver(monkeypatch, unknown_check=2)
    with pytest.raises(BudgetExhausted, match='xor_latch model parameters'):
        solve.synthesize(samples)
    # The constant grammar really is UNSAT; the unresolved stateful grammar
    # must not be conflated with that earlier exclusion.
    assert len(checks) == 2


def test_total_deadline_bounds_all_influence_queries(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(solve, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    checks, timeouts = controlled_solver(monkeypatch, after_check=lambda: now.__setitem__(0, now[0] + 0.4))
    with pytest.raises(BudgetExhausted, match='wall budget exhausted'):
        solve.analyze_read(PollWindow(), read_event(), wall_seconds=1)
    assert len(checks) == 3
    assert 0 < timeouts[-1] < timeouts[1] < timeouts[0] <= 1000


def test_unsupported_instruction_remains_a_semantic_gap():
    window = PollWindow()
    # REV is intentionally outside the first local instruction envelope.
    window.data = bytes.fromhex('490140b92909c05a29010b0a3f010c6b81ffff54')
    with pytest.raises(SemanticGap, match='rev'):
        solve.analyze_read(window, read_event())


def test_extended_cmp_is_rejected_instead_of_silently_changing_predicate():
    window = PollWindow()
    # CMP's UXTB would compare 0x23, while ignoring it would compare 0x123
    # and incorrectly label this 0x3f-masked polling condition UNSAT.
    window.data = bytes.fromhex('490140b929010b0a3f012c6ba1ffff54')
    event = read_event()
    event['registers'][12] = '0x123'
    with pytest.raises(SemanticGap, match='Extended operand'):
        solve.analyze_read(window, event)
