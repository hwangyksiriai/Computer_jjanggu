"""User-chosen final versions. Original files are never modified."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from file_comparison import version_candidates


def _path(value):
    if isinstance(value, dict):
        value = value.get('path')
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip() or '\0' in str(value):
        raise ValueError('파일 위치를 확인해 주세요.')
    path = str(Path(value).expanduser().resolve(strict=False))
    return os.path.normcase(path), path


def _identity(path):
    current = Path(path).stat()
    if not stat.S_ISREG(current.st_mode):
        raise ValueError('파일을 골라 주세요.')
    return (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)


class FinalVersions:
    """One explicit final mark among supplied, conservatively named versions.

    describe() reports an identity snapshot, not an inference from dates or file
    names. File metadata checks may access storage; callers with many paths
    should run them off the UI thread.
    """
    def __init__(self, data_directory):
        self.data = Path(data_directory)
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = self.data / 'final-versions.sqlite3'
        with self._connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS final_versions (
                path_key TEXT PRIMARY KEY, path TEXT NOT NULL,
                identity TEXT NOT NULL, marked_at REAL NOT NULL
            )''')

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def describe(self, path):
        key, path = _path(path)
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM final_versions WHERE path_key=?', (key,)).fetchone()
        result = dict(path=path, state='none', label='', marked_at=None)
        if row is None:
            return result
        result['marked_at'] = row['marked_at']
        try:
            identity = _identity(path)
        except (OSError, ValueError):
            result.update(state='missing', label='최종본 원본 없음')
        else:
            if list(identity) == json.loads(row['identity']):
                result.update(state='final', label='내가 정한 최종본')
            else:
                result.update(state='changed', label='표시 후 변경됨 · 원본을 확인해 주세요')
        return result

    def mark(self, path, candidates=None):
        key, path = _path(path)
        try:
            identity = _identity(path)
        except (OSError, ValueError) as error:
            raise ValueError('원본 파일을 확인한 뒤 최종본으로 표시해 주세요.') from error
        supplied = []
        for value in candidates or ():
            try:
                _, candidate = _path(value)
            except (OSError, ValueError, TypeError):
                continue
            supplied.append({'path': candidate})
        # A user-picked unrelated comparison document must keep its own mark.
        peers = version_candidates({'path': path}, supplied)
        keys = {key, *(_path(row)[0] for row in peers)}
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for peer in keys:
                connection.execute('DELETE FROM final_versions WHERE path_key=?', (peer,))
            connection.execute('INSERT INTO final_versions VALUES(?,?,?,?)',
                               (key, path, json.dumps(identity), time.time()))
        return self.describe(path)

    def clear(self, path):
        key, _ = _path(path)
        with self._connection() as connection:
            return bool(connection.execute('DELETE FROM final_versions WHERE path_key=?', (key,)).rowcount)

    def remap_paths(self, mapping):
        """Follow confirmed app moves/undo, without treating new content as final.

        Keep the original identity. A move that changes it is marked as needing
        confirmation; this never silently approves an unrelated replacement.
        """
        moves = {_path(source)[0]: _path(destination) for source, destination in mapping.items()}
        if not moves:
            return
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            selected = []
            for source, (key, path) in moves.items():
                row = connection.execute('SELECT identity,marked_at FROM final_versions WHERE path_key=?', (source,)).fetchone()
                if row:
                    selected.append((key, path, row['identity'], row['marked_at']))
            for source in moves:
                connection.execute('DELETE FROM final_versions WHERE path_key=?', (source,))
            for row in selected:
                connection.execute('''INSERT INTO final_versions VALUES(?,?,?,?) ON CONFLICT(path_key)
                    DO UPDATE SET path=excluded.path, identity=excluded.identity,
                    marked_at=excluded.marked_at WHERE excluded.marked_at>=final_versions.marked_at''', row)
