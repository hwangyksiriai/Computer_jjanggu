"""Local indexing and reversible file operations. No cloud calls."""
from __future__ import annotations
import bootstrap
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
import threading
import unicodedata
from functools import wraps
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from xml.etree import ElementTree

from app_paths import BASE, DATA
EXTRACT_VERSION = 2
PENDING_STATUS = '분석 대기 · 이름만 검색'
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp'}
TEXT_EXTS = {'.txt', '.md', '.csv', '.tsv', '.json', '.log'}
SKIP_EXTS = {'.lnk', '.url', '.exe', '.msi', '.dll', '.sys', '.tmp', '.part', '.crdownload'}
GENERATED_DIRS={'node_modules','__pycache__','venv','.venv','site-packages','.git','.cache','.next','dist','build'}
KINDS = {
    '급여명세서': ['급여명세서', '급여 명세서', '임금명세서', '임금 명세서', '급여 내역', '급여내역', 'payslip', 'pay slip', 'paystub', 'pay stub', 'earnings statement'],
    '인보이스': ['invoice', '인보이스', '청구서', 'amount due', 'payment due', 'bill to'],
    '영수증': ['receipt', '영수증', '결제완료', '결제 완료'],
    '계약서': ['contract', 'agreement', '계약서', '계약기간', '계약 기간'],
    '제안서': ['proposal', '제안서', '제안 배경'],
}

def desktop_path():
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as k:
                return Path(os.path.expandvars(winreg.QueryValueEx(k, 'Desktop')[0]))
        except OSError:
            pass
    return Path.home() / 'Desktop'

def fingerprint(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def eligible(path, root):
    p, r = Path(path), Path(root).resolve()
    try:
        return (p.parent.resolve() == r and p.is_file() and not p.is_symlink()
                and not p.name.startswith(('.', '~', '$')) and p.suffix.lower() not in SKIP_EXTS
                and p.name.lower() not in {'desktop.ini', 'thumbs.db'}
                and not (getattr(p.stat(), 'st_file_attributes', 0) & 0x406))
    except OSError:
        return False

def extract(path):
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if p.stat().st_size > 40 * 1024 * 1024:
            return '', '큰 파일 · 이름만 검색'
        if ext in TEXT_EXTS:
            data = p.read_bytes()[:2_000_000]
            for encoding in ('utf-8-sig', 'utf-16', 'cp949'):
                try:
                    return data.decode(encoding)[:100_000], '본문 분석 완료'
                except UnicodeError:
                    continue
            return '', '문자 인코딩 확인 필요'
        if ext == '.docx':
            with zipfile.ZipFile(p) as z:
                if z.getinfo('word/document.xml').file_size > 10_000_000:
                    return '', '큰 문서 · 이름만 검색'
                root = ElementTree.fromstring(z.read('word/document.xml'))
                paragraphs=[''.join(p.itertext()) for p in root.iter() if p.tag.endswith('}p')]
                return '\n'.join(paragraphs)[:100_000], '본문 분석 완료'
        if ext == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(p)
            if reader.is_encrypted:
                return '', '암호 문서 · 이름만 검색'
            pages = [page.extract_text() or '' for page in reader.pages[:60]]
            missing = [i for i,text in enumerate(pages[:12]) if len(re.sub(r'\s+','',text)) < 40]
            if not missing:
                return '\n'.join(pages)[:100_000], ('본문 분석 완료' if len(reader.pages)<=60 else '앞 60쪽 본문 분석 완료')
            import pymupdf
            cache=DATA/'ocr-pages'; cache.mkdir(parents=True,exist_ok=True)
            recognized=0
            with pymupdf.open(p) as doc:
                for number in missing:
                    image=cache/(uuid.uuid4().hex+'.png')
                    try:
                        doc[number].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5),alpha=False).save(image)
                        page_text,_=extract(image)
                        if page_text:
                            pages[number]+='\n'+page_text; recognized+=1
                    finally:
                        if image.exists(): image.unlink()
                text='\n'.join(pages)[:100_000]
                return text, ('본문·이미지 글자 분석 완료' if recognized and len(reader.pages)<=12 else
                              ('본문 분석·OCR 일부 범위' if text.strip() else '스캔 PDF · 인식된 글자 없음'))
        if ext in {'.pptx','.xlsx'}:
            with zipfile.ZipFile(p) as z:
                names=[n for n in z.namelist() if (n.startswith('ppt/slides/slide') or n=='xl/sharedStrings.xml' or n.startswith('xl/worksheets/sheet')) and n.endswith('.xml')]
                texts=[]
                for name in sorted(names)[:100]:
                    if z.getinfo(name).file_size>5_000_000: continue
                    root=ElementTree.fromstring(z.read(name))
                    texts.append(' '.join(el.text or '' for el in root.iter() if el.tag.endswith('}t') or (el.tag.endswith('}v') and name.startswith('xl/worksheets/'))))
                return '\n'.join(texts)[:100_000], '본문 분석 완료' if texts else '본문 미추출'
        if ext in IMAGE_EXTS and os.name == 'nt':
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(BASE / 'ocr.ps1'), '-ImagePath', str(p.resolve())],
                capture_output=True, timeout=35, creationflags=0x08000000)
            if result.returncode:
                return '', '이미지 글자 분석 불가'
            text = result.stdout.decode('utf-8-sig', errors='replace').strip()
            return text[:100_000], ('이미지 글자 분석 완료' if text else '인식된 글자 없음')
        return '', '이름만 검색'
    except Exception as e:
        return '', '분석 불가 · ' + type(e).__name__

def payroll_evidence(text):
    """Independent payroll fields; one salary mention alone is not a payslip."""
    compact=re.sub(r'\s+','',unicodedata.normalize('NFKC',text)).lower()
    groups=[('기본급·수당',r'기본급|기본임금|본봉|연장근로수당|직책수당|시간외수당|연장수당|급여액|총급여|지급내역|지급항목|basesalary|basicpay|regularpay|grosspay|grossearnings|earnings'),
            ('공제·보험',r'공제합계|공제총액|공제액|공제내역|공제항목|국민연금|건강보험|고용보험|소득세|장기요양|deductions|withholding|incometax'),
            ('실수령·차인지급',r'실수령|차인지급|차감지급|지급총액|실지급|세후급여|입금액|netpay|takehomepay|netamount|netearnings'),
            ('직원·급여기간',r'사번|직원명|성명[:：]|근로자명|급여기간|귀속년월|귀속월|지급년월|급여지급일|employeeid|employeename|payperiod|paydate')]
    return [label for label,pattern in groups if re.search(pattern,compact)]

def payroll_title(text):
    compact=re.sub(r'\s+','',unicodedata.normalize('NFKC',text)).lower()
    return any(re.sub(r'\s+','',word) in compact for word in KINDS['급여명세서'])

def classify(name, text):
    full = (name + '\n' + text).lower()
    if payroll_title(full): return '급여명세서'
    for kind, words in KINDS.items():
        if any(w in full for w in words):
            return kind
    if len(payroll_evidence(text))>=3: return '급여명세서'
    # Multiple independent billing fields are required; a single amount is not an invoice.
    signals = [bool(re.search(p, full)) for p in
               [r'공급가액|subtotal', r'지급기한|납부기한|due date', r'청구금액|총 청구|total due']]
    if sum(signals) >= 2:
        return '인보이스'
    if Path(name).suffix.lower() in IMAGE_EXTS:
        return '이미지'
    return '기타 문서'

def serialized(method):
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        with self.mutation_lock: return method(self,*args,**kwargs)
    return wrapped

class Library:
    def __init__(self, data_dir):
        self.mutation_lock=threading.RLock()
        self.data = Path(data_dir).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = self.data / 'library.sqlite3'
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, name TEXT, body TEXT,
                  category TEXT, status TEXT, mtime REAL, size INTEGER, scope TEXT);
                CREATE TABLE IF NOT EXISTS moves(id TEXT PRIMARY KEY, batch TEXT, source TEXT,
                  dest TEXT, digest TEXT, state TEXT, created REAL, error TEXT);
                CREATE TABLE IF NOT EXISTS index_versions(path TEXT PRIMARY KEY, version INTEGER);
            ''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def index(self, roots, progress=None, on_file=None, only_paths=None):
        # Publish the complete file list before opening slow documents or running OCR.
        # A fresh install can search names immediately, including files in later roots.
        work = []
        self.index_state = dict(phase='catalog', completed=0, total=0)
        for root in roots:
            root = Path(root).resolve()
            if not root.is_dir():
                continue
            with self.connect() as c:
                snapshot={r['path'] for r in c.execute('SELECT path FROM files WHERE scope=?',(str(root),))}
            if only_paths is not None:
                paths=[Path(p).resolve() for p in only_paths if Path(p).resolve().is_relative_to(root) and Path(p).is_file()]
            else:
                paths = []
                for folder, dirs, files in os.walk(root, followlinks=False):
                    if progress: progress(len(work), '')
                    safe_dirs = []
                    for name in dirs:
                        try:
                            d = Path(folder) / name
                            if name.lower() not in GENERATED_DIRS and not name.startswith('.') and not (getattr(d.stat(), 'st_file_attributes', 0) & 0x406):
                                safe_dirs.append(name)
                        except OSError:
                            pass
                    dirs[:] = safe_dirs
                    for name in files:
                        p = Path(folder) / name
                        try:
                            if not name.startswith(('.', '~', '$')) and not p.is_symlink() and not (getattr(p.stat(), 'st_file_attributes', 0) & 0x406):
                                paths.append(p)
                        except OSError:
                            pass
            priority={'.pdf','.docx','.txt','.pptx','.xlsx','.png','.jpg','.jpeg'}
            paths.sort(key=lambda p:(len(p.relative_to(root).parts),p.suffix.lower() not in priority,p.name.lower()))
            seen = set()
            for start in range(0, len(paths), 100):
                if progress: progress(len(work), '')
                with self.mutation_lock, self.connect() as c:
                    for p in paths[start:start+100]:
                        try:
                            key=str(p.resolve()); stat=p.stat(); seen.add(key)
                            old=c.execute('SELECT f.*,COALESCE(v.version,0) AS extract_version FROM files f LEFT JOIN index_versions v ON f.path=v.path WHERE f.path=?',(key,)).fetchone()
                            if not old or old['mtime']!=stat.st_mtime or old['size']!=stat.st_size or old['extract_version']!=EXTRACT_VERSION:
                                c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)',
                                          (key,p.name,'',classify(p.name,''),PENDING_STATUS,stat.st_mtime,stat.st_size,str(root)))
                                c.execute('INSERT OR REPLACE INTO index_versions VALUES(?,0)',(key,))
                            work.append((p,root))
                        except OSError:
                            continue
                self.index_state = dict(phase='catalog', completed=len(work), total=0)
            with self.mutation_lock,self.connect() as c:
                for row in c.execute('SELECT path FROM files WHERE scope=?', (str(root),)).fetchall():
                    excluded=any(part.lower() in GENERATED_DIRS for part in Path(row['path']).relative_to(root).parts[:-1])
                    if ((only_paths is None and row['path'] in snapshot and row['path'] not in seen and (excluded or not Path(row['path']).exists())) or
                        (only_paths is not None and row['path'] in {str(Path(p).resolve()) for p in only_paths} and not Path(row['path']).exists())):
                        c.execute('DELETE FROM files WHERE path=?', (row['path'],))
                        c.execute('DELETE FROM index_versions WHERE path=?', (row['path'],))
        count = 0
        phase='details' if on_file else 'content'
        for p,root in work:
            try:
                self.index_state = dict(phase=phase, completed=count, total=len(work))
                if progress: progress(count,p.name)
                key = str(p.resolve())
                stat = p.stat()
                with self.connect() as c:
                    old = c.execute('SELECT f.*,COALESCE(v.version,0) AS extract_version FROM files f LEFT JOIN index_versions v ON f.path=v.path WHERE f.path=?', (key,)).fetchone()
                if not old or old['mtime'] != stat.st_mtime or old['size'] != stat.st_size or old['extract_version']!=EXTRACT_VERSION:
                    text, status = extract(p)
                    category = classify(p.name, text)
                    with self.mutation_lock,self.connect() as c:
                        current=p.stat()
                        if (current.st_mtime_ns,current.st_size)!=(stat.st_mtime_ns,stat.st_size):
                            # Do not retain text for a version that changed during extraction.
                            c.execute('DELETE FROM files WHERE path=? AND mtime=? AND size=? AND status=?',
                                      (key,stat.st_mtime,stat.st_size,PENDING_STATUS))
                            continue
                        c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)',
                                  (key, p.name, text, category, status, stat.st_mtime, stat.st_size, str(root)))
                        c.execute('INSERT OR REPLACE INTO index_versions VALUES(?,?)',(key,EXTRACT_VERSION))
                    row=dict(path=key,name=p.name,body=text,category=category,status=status,mtime=stat.st_mtime,size=stat.st_size,scope=str(root))
                else: row=dict(old)
                if on_file: on_file(row)
                count += 1
            except OSError:
                continue
        self.index_state = dict(phase=phase, completed=count, total=len(work))
        return count

    def search(self, query='', previous='', category=None, roots=None):
        root_paths=tuple(Path(root).resolve() for root in roots) if roots else ()
        q = query.strip().lower()
        if any(x in q for x in ('그중', '그 중', '거기서')):
            q = previous + ' ' + q
        selected_kind = next((k for k, ws in KINDS.items() if any(w in q for w in [k]+ws)), None)
        terms = re.sub(r'찾아\s*줘|찾아주세요|보여\s*줘|보여주세요|파일|문서|관련|그중|그 중|거기서|것만|것들|보이는|처럼|같은|지난달|지난주|최근|달러|usd|전체|모두', ' ', q)
        if selected_kind:
            for w in [selected_kind] + KINDS[selected_kind]:
                terms = terms.replace(w, ' ')
        terms = [t for t in re.findall(r'[\w가-힣]+', terms) if len(t) > 1 and t not in {'으로','에서','있는','받은','저장한','해줘','좀','부탁해','중에서'}]
        now = datetime.now()
        date_start = date_end = None
        if '지난달' in q:
            date_end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            date_start = (date_end - timedelta(days=1)).replace(day=1)
        elif '지난주' in q:
            date_end = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            date_start = date_end - timedelta(days=7)
        elif '최근' in q:
            date_start = now - timedelta(days=7)
        with self.connect() as c:
            rows = c.execute('SELECT * FROM files ORDER BY mtime DESC').fetchall()
        results = []
        for row in rows:
            r = dict(row)
            if root_paths and not any(Path(r['path']).is_relative_to(root) for root in root_paths):
                continue
            if not Path(r['path']).exists():
                continue
            if category and category != '전체' and r['category'] != category:
                continue
            if selected_kind and r['category'] != selected_kind:
                continue
            if date_start and r['mtime'] < date_start.timestamp():
                continue
            if date_end and r['mtime'] >= date_end.timestamp():
                continue
            full = (r['name'] + '\n' + r['body']).lower()
            if any(w in q for w in ('달러', 'usd')) and not re.search(r'\busd\b|\$|달러', full):
                continue
            if terms and not all(t in full for t in terms):
                continue
            r['reason'] = ('내용에서 ' + r['category'] + ' 특징 발견' if r['body'] and selected_kind and selected_kind not in r['name'] else r['status'])
            r['score'] = sum(3 if t in r['name'].lower() else 1 for t in terms)
            results.append(r)
        return sorted(results, key=lambda r: r['score'], reverse=True), q

    def plan(self, source, vault):
        source, vault = Path(source).resolve(), Path(vault).resolve()
        if source == vault or source in vault.parents or vault in source.parents:
            raise ValueError('원본 폴더와 보관함은 서로 겹치지 않아야 해요.')
        result = []
        for p in sorted(source.iterdir()):
            if eligible(p, source):
                stat = p.stat()
                if time.time() - stat.st_mtime < 10:
                    continue
                with self.connect() as c:
                    row = c.execute('SELECT category FROM files WHERE path=?', (str(p.resolve()),)).fetchone()
                category = row['category'] if row else '기타 문서'
                result.append({'source': str(p.resolve()), 'dest': str(vault / category / p.name),
                               'mtime': stat.st_mtime, 'size': stat.st_size})
        return result

    @serialized
    def move(self, plan, source_root, vault):
        source_root, vault = Path(source_root).resolve(), Path(vault).resolve()
        if source_root == vault or source_root in vault.parents or vault in source_root.parents:
            raise ValueError('원본과 보관함이 겹칩니다.')
        batch = uuid.uuid4().hex
        done, errors = [], []
        for item in plan:
            src, dst = Path(item['source']), Path(item['dest'])
            ident = uuid.uuid4().hex
            try:
                if not eligible(src, source_root) or vault not in dst.resolve().parents:
                    raise ValueError('허용 범위 밖의 파일입니다.')
                stat = src.stat()
                if stat.st_mtime != item['mtime'] or stat.st_size != item['size']:
                    raise ValueError('미리보기 이후 변경되어 건너뛰었어요.')
                dst.parent.mkdir(parents=True, exist_ok=True)
                i = 1
                while dst.exists():
                    dst = Path(item['dest']).with_stem(Path(item['dest']).stem + f' ({i})'); i += 1
                digest = fingerprint(src)
                with self.connect() as c:
                    c.execute('INSERT INTO moves VALUES(?,?,?,?,?,?,?,?)',
                              (ident, batch, str(src), str(dst), digest, 'pending', time.time(), ''))
                safe_transfer(src, dst, digest)
                with self.connect() as c:
                    c.execute("UPDATE moves SET state='done' WHERE id=?", (ident,))
                    c.execute('UPDATE files SET path=?,scope=? WHERE path=?', (str(dst), str(vault), str(src)))
                done.append(str(dst))
            except Exception as e:
                errors.append(f'{src.name}: {e}')
                with self.connect() as c:
                    c.execute("UPDATE moves SET state='error',error=? WHERE id=?", (str(e), ident))
        return done, errors

    def history(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM moves ORDER BY created DESC')]

    def recover(self):
        # Recover the journal after interruption between filesystem and DB commits.
        with self.connect() as c:
            rows = c.execute("SELECT * FROM moves WHERE state='pending'").fetchall()
            for r in rows:
                src, dst = Path(r['source']), Path(r['dest'])
                if not src.exists() and dst.is_file() and fingerprint(dst) == r['digest']:
                    c.execute("UPDATE moves SET state='done' WHERE id=?", (r['id'],))
                else:
                    c.execute("UPDATE moves SET state='error',error='중단된 작업 · 원본과 보관함 확인 필요' WHERE id=?", (r['id'],))

    @serialized
    def undo(self):
        with self.connect() as c:
            last = c.execute("SELECT batch FROM moves WHERE state='done' ORDER BY created DESC LIMIT 1").fetchone()
            rows = c.execute("SELECT * FROM moves WHERE batch=? AND state='done'", (last['batch'],)).fetchall() if last else []
        done, errors = [], []
        for r in rows:
            src, dst = Path(r['source']), Path(r['dest'])
            try:
                if src.exists():
                    raise ValueError('원래 위치에 같은 이름이 있어 덮어쓰지 않았어요.')
                if not dst.is_file() or dst.is_symlink() or fingerprint(dst) != r['digest']:
                    raise ValueError('보관 후 변경되거나 없어진 파일은 자동 복원하지 않아요.')
                if not src.parent.is_dir():
                    raise ValueError('원래 폴더가 없어 복원하지 않았어요.')
                safe_transfer(dst, src, r['digest'])
                with self.connect() as c:
                    c.execute("UPDATE moves SET state='undone' WHERE id=?", (r['id'],))
                    c.execute('UPDATE files SET path=?,scope=? WHERE path=?', (str(src), str(src.parent), str(dst)))
                done.append(str(src))
            except Exception as e:
                errors.append(f'{src.name}: {e}')
        return done, errors

def safe_transfer(src, dst, digest):
    """Windows atomic move; do not copy/delete across volumes automatically."""
    if os.name == 'nt':
        if fingerprint(src) != digest:
            raise ValueError('이동 직전에 파일이 변경되어 건너뛰었어요.')
        # Windows rename never overwrites. Cross-volume moves fail with source intact.
        os.rename(src, dst)
        return
    created = False
    try:
        with src.open('rb') as inp, dst.open('xb') as out:
            created = True
            shutil.copyfileobj(inp, out)
            out.flush(); os.fsync(out.fileno())
        if fingerprint(dst) != digest or fingerprint(src) != digest:
            raise ValueError('복사 중 파일이 변경되어 이동을 취소했어요.')
        shutil.copystat(src, dst)
        src.unlink()
    except Exception:
        if created and dst.exists() and src.exists():
            dst.unlink()
        raise

def set_desktop_icons(visible):
    """Set Explorer's actual view state; never alter registry or restart Explorer."""
    if os.name != 'nt':
        return False
    u = ctypes.windll.user32
    u.FindWindowW.restype = ctypes.c_void_p
    u.FindWindowExW.restype = ctypes.c_void_p
    u.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    u.SendMessageW.restype = ctypes.c_ssize_t
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    prog = u.FindWindowW('Progman', None)
    view = u.FindWindowExW(prog, None, 'SHELLDLL_DefView', None)
    if not view:
        found = []
        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
        @callback
        def scan(hwnd, _):
            v = u.FindWindowExW(hwnd, None, 'SHELLDLL_DefView', None)
            if v: found.append(v)
            return True
        u.EnumWindows(scan, 0)
        view = found[0] if found else None
    if not view:
        return False
    icons = u.FindWindowExW(view, None, 'SysListView32', None)
    if not icons:
        return False
    if bool(u.IsWindowVisible(icons)) != visible:
        u.SendMessageW(view, 0x0111, 0x7402, 0)
    return True
