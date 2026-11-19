"""Versioned outcomes and identity helpers. Unknown hashes are admissible."""
from enum import StrEnum
from pathlib import Path
import hashlib
import json
import os


class Outcome(StrEnum):
    SUCCESS_TESTED = 'SUCCESS_TESTED'
    BUDGET_EXHAUSTED = 'BUDGET_EXHAUSTED'
    MISSING_EXECUTABLE_DATA = 'MISSING_EXECUTABLE_DATA'
    UNSUPPORTED_SEMANTICS = 'UNSUPPORTED_SEMANTICS'
    REPLAY_DIVERGED = 'REPLAY_DIVERGED'
    EXHAUSTED_IN_ENVELOPE = 'EXHAUSTED_IN_ENVELOPE'
    BACKEND_FAILED = 'BACKEND_FAILED'
    VERIFICATION_FAILED = 'VERIFICATION_FAILED'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class SemanticGap(RuntimeError):
    pass


class BudgetExhausted(RuntimeError):
    """A bounded attempt is unresolved; its candidates remain admissible."""

    pass
