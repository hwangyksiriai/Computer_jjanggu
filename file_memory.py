"""Local recent files, collections, pins and photo favorites; never move originals."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
import unicodedata


def _path(value):
    """Keep a display path while comparing equivalent local paths consistently."""
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError('파일 위치를 확인해 주세요.')
    display = str(Path(value).expanduser().resolve(strict=False))
    return os.path.normcase(display), display


def _name(value):
    name = str(value).strip()
    if not name or len(name) > 80:
        raise ValueError('모음 이름을 1~80자로 입력해 주세요.')
    return name, unicodedata.normalize('NFKC', name).casefold()


def _available(path):
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _require_file(path):
    try:
        value=Path(path).stat()
    except OSError as error:
        raise ValueError('파일이 없거나 읽을 수 없어요. 다시 찾아 주세요.') from error
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('폴더 대신 파일을 골라 주세요.')
    return value


_PHOTO_TEXT={'thumbnail':32768,'status':240,'reason':1000,'category':100,'group':100}
_PHOTO_IDENTITY=('saved_mtime_ns','saved_size','saved_ino','saved_dev')


def _photo_identity(value):
    return value.st_mtime_ns,value.st_size,value.st_ino,value.st_dev


def _photo_metadata(row,path,observed):
    """Keep only bounded display metadata, never document bodies or vectors."""
    result={'name':Path(path).name,'mtime':observed.st_mtime,'size':observed.st_size}
    for key,limit in _PHOTO_TEXT.items():
        value=row.get(key)
        if key=='thumbnail' and isinstance(value,os.PathLike):value=os.fspath(value)
        if isinstance(value,str):result[key]=value[:limit]
    value=row.get('mtime')
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        try:
            if math.isfinite(value):result['mtime']=value
        except OverflowError:pass
    for key in ('size','width','height'):
        value=row.get(key)
        if isinstance(value,int) and not isinstance(value,bool) and 0<=value<=2**63-1:
            result[key]=value
    fields=row.get('fields')
    taken=fields.get('taken_date') if isinstance(fields,dict) else None
    if isinstance(taken,str):result['fields']={'taken_date':taken[:64]}
    result.update(zip(_PHOTO_IDENTITY,_photo_identity(observed)))
    return json.dumps(result,ensure_ascii=False,allow_nan=False)


def _photo_json(value):
    try:
        result=json.loads(value)
        return result if isinstance(result,dict) else {}
    except (ValueError,TypeError):return {}


class FileMemory:
    RECENT_LIMIT = 100

    def __init__(self, data_directory):
        self.data = Path(data_directory)
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = self.data / 'file-memory.sqlite3'
        with self._connection(write=True) as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS recent_files (
                    path_key TEXT PRIMARY KEY, path TEXT NOT NULL,
                    last_opened REAL NOT NULL, open_order INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS recent_open_order ON recent_files(open_order DESC);
                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_files (
                    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    path_key TEXT NOT NULL, path TEXT NOT NULL, added REAL NOT NULL,
                    PRIMARY KEY (collection_id, path_key)
                );
                CREATE TABLE IF NOT EXISTS pinned_files (
                    path_key TEXT PRIMARY KEY, path TEXT NOT NULL,
                    pinned_at REAL NOT NULL, pin_order INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS pinned_file_order ON pinned_files(pin_order DESC);
                CREATE TABLE IF NOT EXISTS saved_photos (
                    path_key TEXT PRIMARY KEY, path TEXT NOT NULL, metadata TEXT NOT NULL,
                    saved_at REAL NOT NULL, saved_order INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS saved_photo_order ON saved_photos(saved_order DESC);
            ''')

    @contextmanager
    def _connection(self, write=False):
        connection = sqlite3.connect(self.db, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                connection.execute('BEGIN IMMEDIATE')
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _reference(row):
        result = dict(row)
        result.pop('path_key', None)
        result.pop('open_order', None)
        result['name'] = Path(result['path']).name
        result['available'] = _available(result['path'])
        return result

    def record_open(self, path):
        """Call after the app successfully asks Windows to open this local file."""
        key, display = _path(path)
        if not _available(display):
            return False
        with self._connection(write=True) as connection:
            order = connection.execute('SELECT COALESCE(MAX(open_order), 0)+1 FROM recent_files').fetchone()[0]
            connection.execute('''INSERT INTO recent_files VALUES(?,?,?,?)
                ON CONFLICT(path_key) DO UPDATE SET path=excluded.path,
                    last_opened=excluded.last_opened, open_order=excluded.open_order''',
                (key, display, time.time(), order))
            connection.execute('''DELETE FROM recent_files WHERE path_key NOT IN
                (SELECT path_key FROM recent_files ORDER BY open_order DESC LIMIT ?)''', (self.RECENT_LIMIT,))
        return True

    def recent(self, limit=20, include_missing=False):
        limit = max(0, min(int(limit), self.RECENT_LIMIT))
        if not limit:
            return []
        with self._connection() as connection:
            rows = connection.execute('SELECT * FROM recent_files ORDER BY open_order DESC').fetchall()
        result = []
        for row in rows:
            item = self._reference(row)
            if item['available'] or include_missing:
                result.append(item)
                if len(result) >= limit:
                    break
        return result

    def set_pinned(self,path,enabled=True):
        """Return the resulting state; removing a missing reference is allowed."""
        key,display=_path(path)
        if enabled:_require_file(display)
        with self._connection(write=True) as connection:
            if enabled:
                order=connection.execute('SELECT COALESCE(MAX(pin_order),0)+1 FROM pinned_files').fetchone()[0]
                connection.execute('''INSERT INTO pinned_files VALUES(?,?,?,?)
                    ON CONFLICT(path_key) DO UPDATE SET path=excluded.path''',
                    (key,display,time.time(),order))
            else:
                connection.execute('DELETE FROM pinned_files WHERE path_key=?',(key,))
        return bool(enabled)

    def is_pinned(self,path):
        key,_=_path(path)
        with self._connection() as connection:
            return connection.execute('SELECT 1 FROM pinned_files WHERE path_key=?',(key,)).fetchone() is not None

    def pinned_files(self,limit=None,include_missing=True):
        """Newest pins first; repeated pinning preserves their original order."""
        maximum=None if limit is None else max(0,int(limit))
        if maximum==0:return []
        with self._connection() as connection:
            rows=connection.execute('SELECT path,pinned_at FROM pinned_files ORDER BY pin_order DESC').fetchall()
        result=[]
        for row in rows:
            item=self._reference(row)
            if item['available'] or include_missing:
                result.append(item)
                if maximum is not None and len(result)>=maximum:break
        return result

    def save_photo(self,row):
        """Remember a selected original plus a small display snapshot, not pixels."""
        if not isinstance(row,dict):raise ValueError('찜할 사진을 먼저 골라 주세요.')
        key,display=_path(row.get('path'))
        metadata=_photo_metadata(row,display,_require_file(display))
        with self._connection(write=True) as connection:
            order=connection.execute('SELECT COALESCE(MAX(saved_order),0)+1 FROM saved_photos').fetchone()[0]
            connection.execute('''INSERT INTO saved_photos VALUES(?,?,?,?,?) ON CONFLICT(path_key)
                DO UPDATE SET path=excluded.path,metadata=excluded.metadata''',
                (key,display,metadata,time.time(),order))
        return True

    def unsave_photo(self,path):
        key,_=_path(path)
        with self._connection(write=True) as connection:
            return bool(connection.execute('DELETE FROM saved_photos WHERE path_key=?',(key,)).rowcount)

    def saved_photos(self,include_missing=True):
        """Changed/reused paths remain removable, but are never a verified photo."""
        with self._connection() as connection:
            rows=connection.execute('SELECT path,metadata,saved_at FROM saved_photos ORDER BY saved_order DESC').fetchall()
        result=[]
        for row in rows:
            item=_photo_json(row['metadata'])
            item.update(path=row['path'],name=Path(row['path']).name,saved_at=row['saved_at'],available=False)
            try:current=_require_file(row['path'])
            except ValueError:item['unavailable_reason']='파일이 없거나 읽을 수 없어요'
            else:
                if tuple(item.get(key) for key in _PHOTO_IDENTITY)==_photo_identity(current):
                    item['available']=True
                else:item['unavailable_reason']='원본이 바뀌었어요'
            if item['available'] or include_missing:result.append(item)
        return result

    def list_collections(self):
        with self._connection() as connection:
            rows = connection.execute('''SELECT c.id,c.name,c.created,c.updated,
                    COUNT(f.path_key) AS file_count FROM collections c
                LEFT JOIN collection_files f ON f.collection_id=c.id
                GROUP BY c.id ORDER BY c.created,c.id''').fetchall()
        return [dict(row) for row in rows]

    def create_collection(self, name):
        name, key = _name(name)
        try:
            with self._connection(write=True) as connection:
                now = time.time()
                return connection.execute('INSERT INTO collections(name,name_key,created,updated) VALUES(?,?,?,?)',
                                          (name, key, now, now)).lastrowid
        except sqlite3.IntegrityError as error:
            raise ValueError('같은 이름의 모음이 있어요. 다른 이름을 입력해 주세요.') from error

    def rename_collection(self, collection_id, name):
        name, key = _name(name)
        try:
            with self._connection(write=True) as connection:
                changed = connection.execute('UPDATE collections SET name=?,name_key=?,updated=? WHERE id=?',
                                             (name, key, time.time(), collection_id)).rowcount
                if not changed:
                    raise ValueError('이 모음이 없어졌어요. 목록을 다시 확인해 주세요.')
        except sqlite3.IntegrityError as error:
            raise ValueError('같은 이름의 모음이 있어요. 다른 이름을 입력해 주세요.') from error

    def delete_collection(self, collection_id):
        with self._connection(write=True) as connection:
            return bool(connection.execute('DELETE FROM collections WHERE id=?', (collection_id,)).rowcount)

    @staticmethod
    def _check_collection(connection, collection_id):
        if not connection.execute('SELECT 1 FROM collections WHERE id=?', (collection_id,)).fetchone():
            raise ValueError('이 모음이 없어졌어요. 목록을 다시 확인해 주세요.')

    def add_files(self, collection_id, paths):
        references = dict(_path(path) for path in paths)
        references = {key: path for key, path in references.items() if _available(path)}
        added = 0
        with self._connection(write=True) as connection:
            self._check_collection(connection, collection_id)
            now = time.time()
            for key, path in references.items():
                added += connection.execute('INSERT OR IGNORE INTO collection_files VALUES(?,?,?,?)',
                                            (collection_id, key, path, now)).rowcount
            if added:
                connection.execute('UPDATE collections SET updated=? WHERE id=?', (now, collection_id))
        return added

    def remove_files(self, collection_id, paths):
        keys = {_path(path)[0] for path in paths}
        removed = 0
        with self._connection(write=True) as connection:
            for key in keys:
                removed += connection.execute('DELETE FROM collection_files WHERE collection_id=? AND path_key=?',
                                              (collection_id, key)).rowcount
            if removed:
                connection.execute('UPDATE collections SET updated=? WHERE id=?', (time.time(), collection_id))
        return removed

    def collection_files(self, collection_id, include_missing=True, limit=None, offset=0):
        # No contents are opened: only bounded reference metadata and existence checks.
        with self._connection() as connection:
            rows = connection.execute('''SELECT path,added FROM collection_files WHERE collection_id=?
                ORDER BY added DESC,path COLLATE NOCASE LIMIT ? OFFSET ?''',
                (collection_id, -1 if limit is None else max(0, int(limit)), max(0, int(offset)))).fetchall()
        result = [self._reference(row) for row in rows]
        return result if include_missing else [row for row in result if row['available']]

    def remap_paths(self, mapping):
        """Apply confirmed app-owned moves/undo to references, in one transaction.

        Read every source before updating, so chains and swaps are applied once.
        Collisions merge references without affecting any original file.
        """
        moves = {}
        for source, destination in mapping.items():
            old_key, _ = _path(source)
            new_key, display = _path(destination)
            moves[old_key] = (new_key, display)
        if not moves:
            return
        with self._connection(write=True) as connection:
            recent = []
            references = []
            pins=[]
            photos=[]
            for key, (new_key, display) in moves.items():
                row = connection.execute('SELECT * FROM recent_files WHERE path_key=?', (key,)).fetchone()
                if row:
                    recent.append((new_key, display, row['last_opened'], row['open_order']))
                for row in connection.execute('SELECT * FROM collection_files WHERE path_key=?', (key,)):
                    references.append((row['collection_id'], new_key, display, row['added']))
                row=connection.execute('SELECT * FROM pinned_files WHERE path_key=?',(key,)).fetchone()
                if row:pins.append((new_key,display,row['pinned_at'],row['pin_order']))
                row=connection.execute('SELECT * FROM saved_photos WHERE path_key=?',(key,)).fetchone()
                if row:
                    metadata=_photo_json(row['metadata'])
                    metadata['name']=Path(display).name
                    photos.append((new_key,display,json.dumps(metadata,ensure_ascii=False),row['saved_at'],row['saved_order']))
            for key in moves:
                connection.execute('DELETE FROM recent_files WHERE path_key=?', (key,))
                connection.execute('DELETE FROM collection_files WHERE path_key=?', (key,))
                connection.execute('DELETE FROM pinned_files WHERE path_key=?',(key,))
                connection.execute('DELETE FROM saved_photos WHERE path_key=?',(key,))
            for row in recent:
                connection.execute('''INSERT INTO recent_files VALUES(?,?,?,?) ON CONFLICT(path_key)
                    DO UPDATE SET path=excluded.path,
                    last_opened=CASE WHEN excluded.open_order>recent_files.open_order
                        THEN excluded.last_opened ELSE recent_files.last_opened END,
                    open_order=MAX(recent_files.open_order,excluded.open_order)''', row)
            for row in references:
                connection.execute('''INSERT INTO collection_files VALUES(?,?,?,?)
                    ON CONFLICT(collection_id,path_key) DO UPDATE SET path=excluded.path,
                    added=MAX(collection_files.added,excluded.added)''', row)
            for row in pins:
                connection.execute('''INSERT INTO pinned_files VALUES(?,?,?,?) ON CONFLICT(path_key)
                    DO UPDATE SET path=excluded.path,
                    pinned_at=CASE WHEN excluded.pin_order>pinned_files.pin_order
                        THEN excluded.pinned_at ELSE pinned_files.pinned_at END,
                    pin_order=MAX(pinned_files.pin_order,excluded.pin_order)''',row)
            for row in photos:
                connection.execute('''INSERT INTO saved_photos VALUES(?,?,?,?,?) ON CONFLICT(path_key)
                    DO UPDATE SET path=excluded.path,
                    metadata=CASE WHEN excluded.saved_order>saved_photos.saved_order
                        THEN excluded.metadata ELSE saved_photos.metadata END,
                    saved_at=CASE WHEN excluded.saved_order>saved_photos.saved_order
                        THEN excluded.saved_at ELSE saved_photos.saved_at END,
                    saved_order=MAX(saved_photos.saved_order,excluded.saved_order)''',row)
