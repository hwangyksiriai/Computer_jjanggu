"""Saved index filters. Definitions persist; originals are only stat'ed in workers."""
from contextlib import contextmanager
from dataclasses import dataclass,field
from datetime import date,datetime,time as daytime,timedelta
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import unicodedata

PERIODS={'any':'수정일 제한 없음','this_month':'이번 달 수정한 파일',
         'last_month':'지난달 수정한 파일','last_30_days':'최근 30일 수정한 파일',
         'custom':'수정일 직접 정하기'}
DOCUMENT_TYPES=('','급여명세서','계약서','영수증','인보이스','제안서','회의록','기타 문서')
TEMPLATES=(
    dict(name='급여명세서',document_type='급여명세서',modified_period='any'),
    dict(name='계약서',document_type='계약서',modified_period='any'),
    dict(name='이번 달 수정한 영수증',document_type='영수증',modified_period='this_month'),
)


def _day(value):
    value=str(value or '').strip()
    if not value:return ''
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('날짜는 2026-09-23처럼 입력해 주세요.')
    try:date.fromisoformat(value)
    except ValueError as error:raise ValueError('달력에 있는 날짜를 입력해 주세요.') from error
    return value


def collection_values(name,query='',document_type='',modified_period='any',modified_from='',modified_to=''):
    name=str(name or '').strip();query=str(query or '').strip();document_type=str(document_type or '').strip()
    if not name or len(name)>80:raise ValueError('모음 이름을 1~80자로 입력해 주세요.')
    if len(query)>500 or len(query.split())>30:raise ValueError('찾을 말을 500자, 30단어 안으로 줄여 주세요.')
    if len(document_type)>80:raise ValueError('문서 종류를 80자 안으로 입력해 주세요.')
    if modified_period not in PERIODS:raise ValueError('파일 수정일 조건을 골라 주세요.')
    start,end=(_day(modified_from),_day(modified_to)) if modified_period=='custom' else ('','')
    if modified_period=='custom' and not (start or end):raise ValueError('시작일이나 마지막 날을 입력해 주세요.')
    if any(value and not '1970-01-01'<=value<='9998-12-31' for value in (start,end)):
        raise ValueError('수정일은 1970년부터 9998년 사이로 입력해 주세요.')
    if start and end and start>end:raise ValueError('마지막 날은 시작일보다 빠를 수 없어요.')
    return dict(name=name,query=query,document_type=document_type,modified_period=modified_period,
                modified_from=start,modified_to=end)


class SmartCollectionStore:
    def __init__(self,data_directory):
        self.data=Path(data_directory);self.data.mkdir(parents=True,exist_ok=True)
        self.db=self.data/'smart-collections.sqlite3'
        with self._connection(write=True) as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS smart_collections(
                id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,name_key TEXT NOT NULL UNIQUE,
                query TEXT NOT NULL,document_type TEXT NOT NULL,modified_period TEXT NOT NULL,
                modified_from TEXT NOT NULL,modified_to TEXT NOT NULL,revision INTEGER NOT NULL,
                created REAL NOT NULL,updated REAL NOT NULL)''')

    @contextmanager
    def _connection(self,write=False):
        connection=sqlite3.connect(self.db,timeout=5);connection.row_factory=sqlite3.Row
        try:
            if write:connection.execute('BEGIN IMMEDIATE')
            with connection:yield connection
        finally:connection.close()

    def create(self,name,query='',document_type='',modified_period='any',modified_from='',modified_to=''):
        values=collection_values(name,query,document_type,modified_period,modified_from,modified_to)
        key=unicodedata.normalize('NFKC',values['name']).casefold();now=time.time()
        try:
            with self._connection(write=True) as connection:
                return connection.execute('''INSERT INTO smart_collections
                    (name,name_key,query,document_type,modified_period,modified_from,modified_to,revision,created,updated)
                    VALUES(?,?,?,?,?,?,?,1,?,?)''',
                    (values['name'],key,values['query'],values['document_type'],values['modified_period'],
                     values['modified_from'],values['modified_to'],now,now)).lastrowid
        except sqlite3.IntegrityError as error:raise ValueError('같은 이름의 자동 모음이 있어요.') from error

    def update(self,ident,name,query='',document_type='',modified_period='any',modified_from='',modified_to=''):
        values=collection_values(name,query,document_type,modified_period,modified_from,modified_to)
        key=unicodedata.normalize('NFKC',values['name']).casefold()
        try:
            with self._connection(write=True) as connection:
                changed=connection.execute('''UPDATE smart_collections SET name=?,name_key=?,query=?,document_type=?,
                    modified_period=?,modified_from=?,modified_to=?,revision=revision+1,updated=? WHERE id=?''',
                    (values['name'],key,values['query'],values['document_type'],values['modified_period'],
                     values['modified_from'],values['modified_to'],time.time(),ident)).rowcount
                if not changed:raise ValueError('이 자동 모음이 없어졌어요. 목록을 다시 열어 주세요.')
        except sqlite3.IntegrityError as error:raise ValueError('같은 이름의 자동 모음이 있어요.') from error

    def list(self):
        with self._connection() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM smart_collections ORDER BY created,id')]

    def get(self,ident):
        with self._connection() as connection:
            row=connection.execute('SELECT * FROM smart_collections WHERE id=?',(ident,)).fetchone()
        return dict(row) if row else None

    def delete(self,ident):
        with self._connection(write=True) as connection:
            return bool(connection.execute('DELETE FROM smart_collections WHERE id=?',(ident,)).rowcount)


def modified_bounds(definition,now=None):
    """Local calendar boundaries, with an inclusive displayed final date."""
    today=(now or datetime.now()).date()
    period=definition.get('modified_period','any');start=end=None
    if period=='this_month':
        start=today.replace(day=1);end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
    elif period=='last_month':
        end=today.replace(day=1);start=(end-timedelta(days=1)).replace(day=1)
    elif period=='last_30_days':start=today-timedelta(days=29);end=today+timedelta(days=1)
    elif period=='custom':
        if definition.get('modified_from'):start=date.fromisoformat(_day(definition['modified_from']))
        if definition.get('modified_to'):end=date.fromisoformat(_day(definition['modified_to']))+timedelta(days=1)
    elif period!='any':raise ValueError('파일 수정일 조건을 확인해 주세요.')
    timestamp=lambda value:datetime.combine(value,daytime.min).timestamp() if value else None
    return timestamp(start),timestamp(end)


def condition_summary(definition):
    parts=[]
    if definition.get('query'):parts.append('찾을 말: '+definition['query'])
    if definition.get('document_type'):parts.append('문서 종류: '+definition['document_type'])
    period=definition.get('modified_period','any')
    if period=='custom':parts.append('파일 수정일: '+(definition.get('modified_from') or '처음')+' ~ '+(definition.get('modified_to') or '계속'))
    else:parts.append(PERIODS.get(period,PERIODS['any']))
    return ' · '.join(parts)


@dataclass
class SmartCollectionResult:
    rows:list[dict]=field(default_factory=list)
    truncated:bool=False
    cancelled:bool=False
    skipped:int=0
    outdated:int=0


def query_collection(catalog_db,definition,roots,cancelled=lambda:False,limit=2000,now=None):
    """Read only a supplied root snapshot. Call this from a worker, never Tk."""
    result=SmartCollectionResult()
    if cancelled():result.cancelled=True;return result
    # None is deliberately the empty scope, never permission to query the DB.
    from document_locations import normalize_document_roots
    scope=normalize_document_roots(list(roots or ()))
    if not scope:return result
    values=collection_values(**{key:definition.get(key,'any' if key=='modified_period' else '') for key in
                               ('name','query','document_type','modified_period','modified_from','modified_to')})
    start,end=modified_bounds(values,now)
    maximum=max(1,min(int(limit),20000))
    where=['scope IN ('+','.join('?' for _ in scope)+')'];parameters=[str(path) for path in scope]
    for term in values['query'].casefold().split():
        where.append("instr(casefold(COALESCE(name,'')||' '||COALESCE(body,'')),?)>0");parameters.append(term)
    if values['document_type']:where.append('category=?');parameters.append(values['document_type'])
    if start is not None:where.append('mtime>=?');parameters.append(start)
    if end is not None:where.append('mtime<?');parameters.append(end)
    connection=None
    try:
        connection=sqlite3.connect(Path(catalog_db).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        connection.row_factory=sqlite3.Row
        connection.set_progress_handler(lambda:1 if cancelled() else 0,1000)
        connection.create_function('casefold',1,lambda value:value.casefold())
        cursor=connection.execute('''SELECT path,name,substr(body,1,30000) AS body,category,status,mtime,size
            FROM files WHERE '''+' AND '.join(where)+' ORDER BY mtime DESC,path',parameters)
        seen=set()
        while True:
            if cancelled():result.cancelled=True;result.rows=[];return result
            batch=cursor.fetchmany(64)
            if not batch:break
            for raw in batch:
                if cancelled():result.cancelled=True;result.rows=[];return result
                row=dict(raw)
                try:
                    path=Path(row['path']).resolve(strict=True)
                    if not any(path.is_relative_to(root) for root in scope) or not path.is_file():
                        result.skipped+=1;continue
                    observed=path.stat()
                except (OSError,ValueError,RuntimeError):result.skipped+=1;continue
                # A path may now contain a newer/replaced file. Old indexed text
                # must not certify that replacement as a matching document.
                if observed.st_size!=row['size'] or observed.st_mtime!=row['mtime']:
                    result.outdated+=1;continue
                # Do not show an outdated modified-date match while reindexing.
                if (start is not None and observed.st_mtime<start) or (end is not None and observed.st_mtime>=end):continue
                key=os.path.normcase(str(path))
                if key in seen:continue
                seen.add(key)
                if len(result.rows)>=maximum:result.truncated=True;return result
                row.update(path=str(path),name=path.name,available=True,group='일치하는 파일',
                           reason='자동 모음 조건과 일치해요. 기간 조건은 파일 수정일 기준이에요.')
                result.rows.append(row)
    except sqlite3.OperationalError:
        if cancelled():result.cancelled=True;result.rows=[]
        else:raise
    finally:
        if connection is not None:connection.close()
    return result


def _query_worker(database,requests,responses,stopped):
    """The worker and its arguments must never own app, widget or Tk variables."""
    while not stopped.is_set():
        request=requests.get()
        if request is None:return
        generation,definition,roots,cancelled,limit=request
        if cancelled.is_set():continue
        try:result=query_collection(database,definition,roots,cancelled.is_set,limit);error=''
        except Exception:result=None;error='모음을 불러오지 못했어요. 파일 분석이 끝난 뒤 다시 열어 주세요.'
        if not stopped.is_set() and not cancelled.is_set():responses.put((generation,result,error))


class SmartQueryRunner:
    """One worker, one newest queued request; safe to abandon on window close."""
    def __init__(self,catalog_db):
        self.requests=queue.Queue(maxsize=1);self.responses=queue.Queue();self.stopped=threading.Event()
        self.cancelled=threading.Event();self.generation=0
        self.thread=threading.Thread(target=_query_worker,args=(str(catalog_db),self.requests,self.responses,self.stopped),
                                     name='smart-collection',daemon=True)
        self.thread.start()

    def submit(self,definition,roots,limit=2000):
        self.cancelled.set();self.cancelled=threading.Event();self.generation+=1
        try:self.requests.get_nowait()
        except queue.Empty:pass
        if not self.stopped.is_set():
            self.requests.put_nowait((self.generation,dict(definition),tuple(map(str,roots)),self.cancelled,limit))
        return self.generation

    def drain(self):
        current=None
        try:
            while True:
                response=self.responses.get_nowait()
                if response[0]==self.generation and not self.stopped.is_set():current=response
        except queue.Empty:return current

    def close(self):
        if self.stopped.is_set():return
        self.stopped.set();self.cancelled.set()
        try:self.requests.get_nowait()
        except queue.Empty:pass
        self.requests.put_nowait(None)
