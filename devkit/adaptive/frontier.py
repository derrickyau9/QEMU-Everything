"""Durable single-worker continuations for the first vertical chain.

This is not the later fair multi-world scheduler. A crash keeps the running
task pending and the next attempt replays from a clean machine.
"""
from pathlib import Path
import json
import sqlite3


class Frontier:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            continuation TEXT NOT NULL, result TEXT)''')
        self.db.commit()

    def begin(self, kind, continuation):
        encoded = json.dumps(continuation, sort_keys=True)
        pending = self.db.execute('SELECT id FROM tasks WHERE kind=? AND status=? AND continuation=? ORDER BY id LIMIT 1',
                                  (kind, 'pending', encoded)).fetchone()
        if pending:
            return pending[0]
        with self.db:
            cursor = self.db.execute('INSERT INTO tasks(kind,status,continuation) VALUES (?, ?, ?)',
                                     (kind, 'pending', encoded))
        return cursor.lastrowid

    def finish(self, task, result):
        with self.db:
            self.db.execute('UPDATE tasks SET status=?,result=? WHERE id=?',
                            ('done', json.dumps(result), task))

    def next_directory(self, root, label, continuation=None):
        task = self.begin('RUN', dict(label=label, restart='clean', checkpoint='unsupported',
                                      **(continuation or {})))
        base = Path(root) / 'workers' / 'worker-0' / f'{task:05d}-{label}'
        path = base
        attempt = 1
        while path.exists():
            attempt += 1
            path = base.with_name(base.name + f'-attempt-{attempt}')
        return task, path

    def close(self):
        self.db.close()
