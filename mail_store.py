"""Bounded local mail-attachment cache and provenance, with no server mutations."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import unicodedata

from mail_providers import profile_values, webmail_url

MAX_CACHE_BYTES = 250 * 1024 * 1024
MAX_CACHE_FILES = 2000
ATTACHMENT_QUERY = '''SELECT a.*, m.sender,m.subject,m.date,m.message_id,m.source_eml,
    c.provider,c.address AS account FROM attachments a JOIN messages m
    ON a.account_id=m.account_id AND a.message_key=m.message_key
    JOIN accounts c ON c.id=a.account_id'''


def clean_text(value, limit=500):
    return ''.join(c for c in str(value or '') if unicodedata.category(c) not in {'Cc', 'Cf'})[:limit].strip()


def safe_filename(value):
    name = unicodedata.normalize('NFC', str(value or '첨부파일'))
    name = name.replace('\\', '/').split('/')[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = ''.join(c for c in name if unicodedata.category(c) not in {'Cc', 'Cf'}).strip(' .')
    if not name:
        name = '첨부파일'
    stem, ext = os.path.splitext(name)
    if stem.split('.')[0].strip(' .').upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        stem = '_' + stem
    return stem[:60] + ext[:12]


def _link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _safe_chain(path):
    """Reject symlinks and Windows junction/reparse points, including ancestors."""
    path = Path(os.path.abspath(path))
    for item in reversed((path, *path.parents)):
        if item.exists() or item.is_symlink():
            if _link(item):
                raise ValueError('메일 보관 위치에 연결된 폴더가 있어 사용할 수 없어요.')
    return path


class MailStore:
    def __init__(self, data, max_bytes=MAX_CACHE_BYTES, max_files=MAX_CACHE_FILES):
        self.data = _safe_chain(Path(data))
        self.data.mkdir(parents=True, exist_ok=True)
        self.cache = _safe_chain(self.data / 'mail-attachments')
        self.cache.mkdir(exist_ok=True)
        self.db = _safe_chain(self.data / 'mail.sqlite3')
        self.max_bytes, self.max_files = max_bytes, max_files
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, address TEXT NOT NULL,
                    host TEXT NOT NULL, port INTEGER NOT NULL, enabled INTEGER NOT NULL,
                    secret BLOB, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    account_id TEXT NOT NULL, message_key TEXT NOT NULL,
                    sender TEXT, subject TEXT, date TEXT, message_id TEXT, source_eml TEXT,
                    PRIMARY KEY(account_id, message_key));
                CREATE TABLE IF NOT EXISTS attachments (
                    path TEXT PRIMARY KEY, account_id TEXT NOT NULL, message_key TEXT NOT NULL,
                    name TEXT NOT NULL, size INTEGER NOT NULL, digest TEXT NOT NULL, imported REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS mail_attachment_account ON attachments(account_id);
                CREATE INDEX IF NOT EXISTS mail_attachment_lookup ON attachments(path COLLATE NOCASE);
            ''')

    @contextmanager
    def _db(self):
        _safe_chain(self.db)
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def save_profile(self, provider, address='', host=''):
        values = profile_values(provider, address, host)
        key = '\0'.join(str(values[x]).casefold() for x in ('provider', 'address', 'host'))
        identity = hashlib.sha256(key.encode()).hexdigest()[:16]
        with self._db() as db:
            db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,1,NULL,?) ON CONFLICT(id) DO UPDATE SET enabled=1,updated=excluded.updated',
                       (identity, values['provider'], values['address'], values['host'], 993, time.time()))
        return self.profile(identity)

    def profiles(self, include_disabled=False):
        with self._db() as db:
            query = 'SELECT id,provider,address,host,port,enabled,secret IS NOT NULL AS has_saved_secret FROM accounts'
            return [dict(x) for x in db.execute(query + ('' if include_disabled else ' WHERE enabled=1') + ' ORDER BY updated DESC')]

    def profile(self, identity):
        return next((p for p in self.profiles(include_disabled=True) if p['id'] == identity), None)

    def store_secret(self, identity, secret=None):
        from mail_credentials import protect
        encrypted = protect(secret) if secret else None
        with self._db() as db:
            db.execute('UPDATE accounts SET secret=? WHERE id=? AND enabled=1', (encrypted, identity))

    def saved_secret(self, identity):
        from mail_credentials import unprotect
        with self._db() as db:
            row = db.execute('SELECT secret FROM accounts WHERE id=? AND enabled=1', (identity,)).fetchone()
        return unprotect(row[0]) if row and row[0] else ''

    def seen(self, identity, key):
        with self._db() as db:
            return db.execute('SELECT 1 FROM messages WHERE account_id=? AND message_key=?', (identity, key)).fetchone() is not None

    def _checked(self, path):
        path = Path(os.path.abspath(path))
        try:
            path.relative_to(self.cache)
        except ValueError as error:
            raise ValueError('메일 보관 위치를 벗어났어요.') from error
        _safe_chain(path)
        return path

    def add_message(self, identity, key, metadata, files, cancelled=lambda: False):
        """Atomically record an inspected message; payloads have already been bounded."""
        created = []
        try:
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                account = db.execute('SELECT enabled FROM accounts WHERE id=?', (identity,)).fetchone()
                if not account or not account[0]:
                    raise ValueError('연결이 해제되었어요.')
                if db.execute('SELECT 1 FROM messages WHERE account_id=? AND message_key=?', (identity, key)).fetchone():
                    return []
                current = db.execute('SELECT COALESCE(SUM(size),0), COUNT(*) FROM attachments WHERE account_id=?', (identity,)).fetchone()
                if current[0] + sum(len(data) for _, data in files) > self.max_bytes or current[1]+len(files)>self.max_files:
                    raise ValueError('메일 보관함이 찼어요. 연결 관리에서 이 컴퓨터의 첨부파일을 비운 뒤 다시 가져와 주세요.')
                folder = self._checked(self.cache / identity / hashlib.sha256(key.encode()).hexdigest()[:16])
                if files:
                    folder.mkdir(parents=True, exist_ok=True)
                    self._checked(folder)
                for index, (name, data) in enumerate(files):
                    if cancelled():
                        raise InterruptedError('가져오기를 멈췄어요.')
                    # Part-number prefix avoids Windows case/Unicode name collisions.
                    name = safe_filename(name)
                    path = self._checked(folder / f'{index+1:02d}_{name}')
                    # Never overwrite a pre-existing file (including an untracked replacement).
                    with path.open('xb') as stream:
                        created.append(path)
                        stream.write(data)
                    self._checked(path)
                    db.execute('INSERT INTO attachments VALUES (?,?,?,?,?,?,?)',
                               (str(path), identity, key, name, len(data), hashlib.sha256(data).hexdigest(), time.time()))
                if cancelled():
                    raise InterruptedError('가져오기를 멈췄어요.')
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)',
                           (identity, key, clean_text(metadata.get('sender')), clean_text(metadata.get('subject')),
                            clean_text(metadata.get('date'), 100), clean_text(metadata.get('message_id'), 400),
                            str(metadata.get('source_eml', ''))[:32768]))
                # Bound history even when thousands of inspected mails had no attachments.
                db.execute('''DELETE FROM messages WHERE rowid IN (SELECT m.rowid FROM messages m
                    WHERE m.account_id=? AND NOT EXISTS (SELECT 1 FROM attachments a
                    WHERE a.account_id=m.account_id AND a.message_key=m.message_key)
                    ORDER BY m.rowid DESC LIMIT -1 OFFSET 3000)''', (identity,))
                return [str(p) for p in created]
        except BaseException:
            for path in created:
                try:
                    self._checked(path).unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            raise

    def attachment_count(self):
        with self._db() as db:
            return db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0]

    def attachments(self, limit=None):
        with self._db() as db:
            query = ATTACHMENT_QUERY + ' ORDER BY a.imported DESC'
            rows = [dict(x) for x in db.execute(query + (' LIMIT ?' if limit is not None else ''),
                                               (max(0, int(limit)),) if limit is not None else ())]
        return [self._with_availability(row) for row in rows]

    def _with_availability(self, row):
        try:
            row['available'] = _safe_chain(row['path']).is_file()
        except (ValueError, OSError):
            row['available'] = False
        row['webmail_url'] = webmail_url(row['provider'])
        return row

    def by_attachment(self, path):
        normalized = os.path.abspath(path)
        with self._db() as db:
            row = db.execute(ATTACHMENT_QUERY + ' WHERE a.path=? COLLATE NOCASE', (normalized,)).fetchone()
        return self._with_availability(dict(row)) if row else None

    def attachment_roots(self):
        # Only roots with registered, locally available attachments are searchable.
        roots = set()
        with self._db() as db:
            rows = list(db.execute('SELECT path,account_id FROM attachments ORDER BY imported DESC'))
        for row in rows:
            root = str(self.cache / row['account_id'])
            if root in roots:
                continue
            try:
                available = self._checked(row['path']).is_file()
            except (ValueError, OSError):
                continue
            if available:
                roots.add(root)
        return sorted(roots)

    def remap_paths(self, mapping):
        """Follow only successful app moves supplied by the caller, never old journals."""
        normalized = {os.path.normcase(os.path.abspath(src)): str(Path(os.path.abspath(dst)))
                      for src, dst in mapping.items() if src and dst}
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            moved = []
            for row in db.execute('SELECT * FROM attachments'):
                target = normalized.get(os.path.normcase(row['path']))
                if target and target != row['path']:
                    moved.append((dict(row), target))
            for row, _ in moved:
                db.execute('DELETE FROM attachments WHERE path=?', (row['path'],))
            for row, target in moved:
                db.execute('INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?,?,?)',
                           (target,row['account_id'],row['message_key'],row['name'],row['size'],row['digest'],row['imported']))
            for row in list(db.execute('SELECT rowid,source_eml FROM messages WHERE source_eml != ""')):
                target = normalized.get(os.path.normcase(row['source_eml']))
                if target:
                    db.execute('UPDATE messages SET source_eml=? WHERE rowid=?',(target,row['rowid']))

    def disconnect(self, identity, remove_cache=False):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE accounts SET enabled=0,secret=NULL WHERE id=?', (identity,))
        if remove_cache:
            self.clear_cache(identity)

    def clear_cache(self, identity):
        """Remove only this app's tracked local copies, never an original .eml or mailbox."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = list(db.execute('SELECT path FROM attachments WHERE account_id=?', (identity,)))
            for row in rows:
                path = Path(os.path.abspath(row[0]))
                try:
                    path.relative_to(self.cache)
                except ValueError:
                    # User-organized copies outside our cache are retained.
                    db.execute('DELETE FROM attachments WHERE path=?', (str(path),))
                    continue
                path = self._checked(path)
                if path.exists():
                    if not path.is_file():
                        raise ValueError('메일 보관함에 예상하지 못한 폴더가 있어 중단했어요.')
                    path.unlink()
                db.execute('DELETE FROM attachments WHERE path=?', (str(path),))
            db.execute('DELETE FROM messages WHERE account_id=?', (identity,))
