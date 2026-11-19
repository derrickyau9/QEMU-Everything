"""A deliberately narrow, checked AArch64 bitwise polling window + local SMT.

This does not claim Triton's full ISA coverage. Unsupported instructions,
memory dependencies and nonlocal branches are semantic gaps, never success.
"""
import math
import time

from .contracts import BudgetExhausted, SemanticGap


class _SolverBudget:
    """One wall deadline shared by decoding, SMT checks and projection."""

    def __init__(self, wall_seconds):
        if (not isinstance(wall_seconds, (int, float)) or
                not math.isfinite(wall_seconds) or not 0 < wall_seconds <= 3600):
            raise ValueError('Local solver wall budget must be finite and in (0, 3600] seconds')
        self.deadline = time.monotonic() + wall_seconds

    def remaining(self, query):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetExhausted(f'Local solver wall budget exhausted during {query}; continuation is unresolved')
        return remaining

    def check(self, solver, z3, query, per_query_ms):
        # Z3 timeout=0 means unbounded, so even a sub-millisecond remainder
        # must become a positive timeout rather than rounding down to zero.
        solver.set(timeout=min(per_query_ms, max(1, math.ceil(self.remaining(query) * 1000))))
        status = solver.check()
        if status == z3.unknown:
            raise BudgetExhausted(f'Local solver unresolved during {query}: {solver.reason_unknown()}; '
                                  'continuation is unresolved')
        self.remaining(query)
        return status


def dependencies():
    try:
        import capstone
        import z3
    except ImportError as error:
        raise SemanticGap('Exploration needs the molten optional dependencies (capstone and z3-solver)') from error
    return capstone, z3


def analyze_read(firmware, event, max_instructions=24, *, wall_seconds=10):
    budget = _SolverBudget(wall_seconds)
    cs, z3 = dependencies()
    decoder = cs.Cs(cs.CS_ARCH_ARM64, cs.CS_MODE_ARM)
    decoder.detail = True
    start = pc = int(event['pc'], 0)
    read = z3.BitVec('mmio_value', 32)
    registers = {f'w{i}': z3.BitVecVal(int(value, 0) & 0xffffffff, 32)
                 for i, value in enumerate(event['registers'])}
    registers['wzr'] = z3.BitVecVal(0, 32)
    concrete = {f'x{i}': int(value, 0) for i, value in enumerate(event['registers'])}
    instructions, equality = [], None

    def operand(instruction, op):
        if getattr(op, 'ext', 0):
            raise SemanticGap('Extended operand outside the first local semantics envelope')
        if getattr(op, 'shift', None) and op.shift.value:
            raise SemanticGap('Shifted operand outside the first local semantics envelope')
        if op.type == cs.arm64.ARM64_OP_IMM:
            return z3.BitVecVal(op.imm & 0xffffffff, 32)
        if op.type == cs.arm64.ARM64_OP_REG:
            name = instruction.reg_name(op.reg)
            if name in registers:
                return registers[name]
        raise SemanticGap('Local solver supports only unshifted uint32 registers/immediates')

    predicate = None
    for index in range(max_instructions):
        budget.remaining('instruction decoding')
        raw = firmware.read(pc, 4)
        instruction = next(decoder.disasm(raw, pc), None)
        if instruction is None:
            raise SemanticGap(f'Undecodable AArch64 instruction at {pc:#x}')
        mnemonic, operands = instruction.mnemonic, instruction.operands
        instructions.append(dict(offset=pc - firmware.base, bytes=raw.hex(),
                                 instruction=mnemonic + ' ' + instruction.op_str))
        if index == 0:
            if mnemonic != 'ldr' or event['width_bits'] != 32 or len(operands) != 2:
                raise SemanticGap('Synchronous boundary is not a uint32 LDR')
            target = instruction.reg_name(operands[0].reg)
            memory = operands[1].mem
            base = instruction.reg_name(memory.base)
            if target not in registers or base not in concrete or memory.index or instruction.writeback:
                raise SemanticGap('Unsupported MMIO addressing form')
            if concrete[base] + memory.disp != int(event['address'], 0):
                raise SemanticGap('Captured registers do not explain the native MMIO address')
            registers[target] = read
        elif mnemonic == 'nop':
            pass
        elif mnemonic in ('and', 'eor', 'orr') and len(operands) == 3:
            a, b = (operand(instruction, op) for op in operands[1:])
            target = instruction.reg_name(operands[0].reg)
            if target not in registers or target == 'wzr':
                raise SemanticGap('Unsupported local destination')
            registers[target] = {'and': lambda: a & b, 'eor': lambda: a ^ b, 'orr': lambda: a | b}[mnemonic]()
        elif mnemonic == 'mov' and len(operands) == 2:
            target = instruction.reg_name(operands[0].reg)
            if target not in registers or target == 'wzr':
                raise SemanticGap('Unsupported MOV destination')
            registers[target] = operand(instruction, operands[1])
        elif mnemonic == 'cmp' and len(operands) == 2:
            equality = operand(instruction, operands[0]) == operand(instruction, operands[1])
        elif mnemonic in ('b.ne', 'b.eq') and equality is not None:
            if operands[0].imm != start:
                raise SemanticGap('Conditional branch is not the bounded poll backedge')
            predicate = equality if mnemonic == 'b.ne' else z3.Not(equality)
            break
        else:
            raise SemanticGap(f'Local semantics unavailable: {mnemonic} {instruction.op_str}')
        pc += 4
    if predicate is None:
        raise SemanticGap('No supported conditional poll backedge in the local window')
    solver = z3.Solver()
    solver.add(predicate)
    smt2 = solver.to_smt2()
    if budget.check(solver, z3, 'local poll condition', 2000) != z3.sat:
        raise SemanticGap('Local poll condition is UNSAT; alternate paths remain open')
    # Identify relevant bits by Boolean influence, not enumeration of 2^32 values.
    mask = 0
    for bit in range(32):
        influence = z3.Solver()
        influence.add(predicate != z3.substitute(predicate, (read, read ^ (1 << bit))))
        status = budget.check(influence, z3, f'bit-influence query {bit}', 2000)
        if status == z3.sat:
            mask |= 1 << bit
    solver.add(read & (~mask & 0xffffffff) == 0)
    if budget.check(solver, z3, 'projected concrete candidate', 2000) != z3.sat:
        raise SemanticGap('No concrete candidate within the projected mask')
    value = solver.model().eval(read, model_completion=True).as_long()
    return dict(schema_version=1, read_address=event['address'], read_pc_offset=start - firmware.base,
                mask=mask, value=value, constraint_smt2=smt2, window=instructions,
                preceding_write=event.get('preceding_write'), concrete_replay_required=True)


def synthesize(samples, *, wall_seconds=10):
    budget = _SolverBudget(wall_seconds)
    _, z3 = dependencies()
    if not samples or any(not sample.get('preceding_write') for sample in samples):
        raise SemanticGap('No observed write/read causal candidate')
    reads = {sample['read_address'] for sample in samples}
    writes = {sample['preceding_write']['address'] for sample in samples}
    if len(reads) != 1 or len(writes) != 1:
        raise SemanticGap('First grammar supports one write latch and one read port')
    mask = 0
    for sample in samples:
        mask |= sample['mask']
    read = z3.BitVec('mmio_value', 32)
    key = z3.BitVec('model_key', 32)
    for kind in ('constant', 'xor_latch'):
        budget.remaining(f'{kind} model synthesis')
        solver = z3.Solver()
        solver.add(key & (~mask & 0xffffffff) == 0)
        for sample in samples:
            budget.remaining(f'{kind} sample constraints')
            value = key if kind == 'constant' else (sample['preceding_write']['value'] ^ key) & mask
            assertions = z3.parse_smt2_string(sample['constraint_smt2'])
            solver.add(*[z3.substitute(assertion, (read, value)) for assertion in assertions])
        status = budget.check(solver, z3, f'{kind} model parameters', 5000)
        if status == z3.sat:
            parameter = solver.model().eval(key, model_completion=True).as_long()
            return dict(kind=kind, write_address=next(iter(writes)), read_address=next(iter(reads)),
                        mask=hex(mask), xor_value=hex(parameter if kind == 'xor_latch' else 0),
                        value=hex(parameter if kind == 'constant' else 0))
    raise SemanticGap('No model in constant/xor-latch grammar fits the counterexamples')
