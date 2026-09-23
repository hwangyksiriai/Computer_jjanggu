"""Bounded, read-only text differences. Extraction limits are never hidden."""
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import codecs
from pathlib import Path
import re
import stat
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_CHARACTERS = 60_000
MAX_SEGMENTS = 1200
MAX_SEGMENT_LENGTH = 400
MAX_PDF_PAGES = 80
MAX_DISPLAY_ROWS = 300
MAX_DISPLAY_CHARACTERS = 45_000
TEXT_EXTENSIONS = {'.txt', '.md', '.csv', '.tsv', '.log', '.json', '.yaml', '.yml', '.ini'}


@dataclass
class DocumentText:
    text: str = ''
    readable: bool = False
    complete: bool = False
    notice: str = ''


@dataclass
class DocumentDiff:
    state: str = 'unavailable'
    summary: str = ''
    rows: list[dict] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    partial: bool = False
    notes: list[str] = field(default_factory=list)


class _Cancelled(Exception):
    pass


def _check(cancelled):
    if cancelled():
        raise _Cancelled()


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _bounded(text, *, complete=True, notice=''):
    truncated = len(text) > MAX_CHARACTERS
    text = text[:MAX_CHARACTERS]
    if truncated:
        notice = (notice + ' ' if notice else '') + '긴 문서의 앞부분만 읽었어요.'
    return DocumentText(text, bool(text.strip()), complete and not truncated, notice)


def _text_file(path, cancelled):
    with path.open('rb') as stream:
        data = stream.read(MAX_TEXT_BYTES + 1)
    _check(cancelled)
    truncated = len(data) > MAX_TEXT_BYTES
    data = data[:MAX_TEXT_BYTES]
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    elif b'\0' in data:
        return DocumentText(notice='글자로 읽을 수 없는 파일이에요.')
    else:
        encoding = 'utf-8-sig'
    try:
        text = codecs.getincrementaldecoder(encoding)().decode(data, final=not truncated)
    except UnicodeDecodeError:
        try:
            text = codecs.getincrementaldecoder('cp949')().decode(data, final=not truncated) if encoding == 'utf-8-sig' else data.decode(encoding, errors='replace')
        except UnicodeDecodeError:
            return DocumentText(notice='글자 인코딩을 정확히 읽지 못했어요.')
    uncertain = '\ufffd' in text
    return _bounded(text, complete=not truncated and not uncertain,
                    notice='앞부분만 읽었거나 일부 글자를 읽지 못했어요.' if truncated or uncertain else '')


def _pdf_file(path, cancelled):
    import fitz
    from pdf_runtime import PDF_LOCK
    if not PDF_LOCK.acquire(timeout=5):
        return DocumentText(notice='다른 PDF를 처리 중이에요. 잠시 뒤 다시 비교해 주세요.')
    try:
        _check(cancelled)
        with fitz.open(path) as document:
            if document.needs_pass:
                return DocumentText(notice='암호가 있는 PDF는 글자를 읽을 수 없어요.')
            texts, characters, uncertain_pages = [], 0, 0
            total = document.page_count
            for index in range(min(total, MAX_PDF_PAGES)):
                _check(cancelled)
                page = document.load_page(index)
                text = page.get_text('text')
                meaningful = sum(character.isalnum() for character in text)
                if meaningful < 15 or (page.get_images() and meaningful < 80):
                    uncertain_pages += 1
                texts.append(text)
                characters += len(text)
                if characters > MAX_CHARACTERS:
                    break
            complete = len(texts) == total and not uncertain_pages and characters <= MAX_CHARACTERS
            notice = 'PDF에서 읽은 글자만 비교해요. 그림·서식의 차이는 원본에서 확인해 주세요.'
            if uncertain_pages:
                notice += ' 글자가 적거나 스캔일 수 있는 페이지가 있어 전체 내용을 확인하지 못했어요.'
            if len(texts) < total:
                notice += f' 전체 {total}쪽 중 앞 {len(texts)}쪽만 읽었어요.'
            return _bounded('\n'.join(texts), complete=complete, notice=notice)
    finally:
        PDF_LOCK.release()


def _docx_file(path, cancelled):
    with zipfile.ZipFile(path) as document:
        info = document.getinfo('word/document.xml')
        if info.file_size > MAX_XML_BYTES:
            return DocumentText(notice='본문이 너무 커서 안전한 범위에서 읽지 못했어요. 원본으로 비교해 주세요.')
        with document.open(info) as source:
            xml = source.read(MAX_XML_BYTES + 1)
        if len(xml) > MAX_XML_BYTES:
            return DocumentText(notice='본문이 너무 커서 읽기를 멈췄어요.')
    _check(cancelled)
    tree = ET.fromstring(xml)
    namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    texts, length = [], 0
    complete = True
    for paragraph in tree.iter(namespace + 'p'):
        _check(cancelled)
        text = ''.join(node.text or '' for node in paragraph.iter(namespace + 't'))
        texts.append(text)
        length += len(text) + 1
        if length > MAX_CHARACTERS:
            complete = False
            break
    return _bounded('\n'.join(texts), complete=complete,
                    notice='문서 본문의 글자만 비교해요. 그림·서식·머리글·주석은 원본에서 확인해 주세요.')


def read_document_text(row, cancelled=lambda: False):
    """Read current originals only. Stored OCR/body snippets cannot prove equality."""
    _check(cancelled)
    try:
        path = Path(row['path'] if isinstance(row, dict) else row)
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            return DocumentText(notice='일반 파일만 비교할 수 있어요.')
        if getattr(before, 'st_file_attributes', 0) & (0x1000 | 0x400000):
            return DocumentText(notice='먼저 PC에 내려받아야 글자를 비교할 수 있어요.')
        if before.st_size > MAX_FILE_BYTES:
            return DocumentText(notice='큰 파일이라 글자 비교를 멈췄어요. 원본을 나란히 확인해 주세요.')
        extension = path.suffix.lower()
        if extension in TEXT_EXTENSIONS:
            result = _text_file(path, cancelled)
        elif extension == '.pdf':
            result = _pdf_file(path, cancelled)
        elif extension == '.docx':
            result = _docx_file(path, cancelled)
        else:
            return DocumentText(notice='이 형식은 글자를 충분히 읽을 수 없어요. 원본을 나란히 확인해 주세요.')
        _check(cancelled)
        if _identity(path.stat(follow_symlinks=False)) != _identity(before):
            return DocumentText(notice='읽는 동안 파일이 바뀌었어요. 다시 비교해 주세요.')
        return result
    except _Cancelled:
        raise
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return DocumentText(notice='파일을 읽지 못했어요. 원본을 열어 확인해 주세요.')
    except Exception:
        return DocumentText(notice='문서의 글자를 읽지 못했어요. 원본을 열어 확인해 주세요.')


def _segments(text):
    """Normalize Unicode/spacing while retaining punctuation, case and numbers."""
    result = []
    truncated = False
    for line in text.splitlines():
        line = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', line)).strip()
        if not line:
            continue
        for sentence in re.split(r'(?<=[.!?。！？])\s+', line):
            for start in range(0, len(sentence), MAX_SEGMENT_LENGTH):
                if len(result) >= MAX_SEGMENTS:
                    return result, True
                result.append(sentence[start:start + MAX_SEGMENT_LENGTH])
    return result, truncated


def compare_documents(left, right, cancelled=lambda: False):
    """Return aligned context/deleted/added rows, with explicit partial coverage."""
    try:
        documents = [read_document_text(row, cancelled) for row in (left, right)]
        notes = [f'{label}: {document.notice}' for label, document in zip(('왼쪽', '오른쪽'), documents) if document.notice]
        if not all(document.readable for document in documents):
            return DocumentDiff(summary='글자를 충분히 읽지 못해 변경 내용을 판단할 수 없어요.', notes=notes)
        sequences = [_segments(document.text) for document in documents]
        first, second = (item[0] for item in sequences)
        partial = any(not document.complete for document in documents) or any(item[1] for item in sequences)
        if any(item[1] for item in sequences):
            notes.append(f'문장이 많아 앞 {MAX_SEGMENTS:,}개 단위만 비교했어요.')
        _check(cancelled)
        matcher = SequenceMatcher(None, first, second, autojunk=True)
        operations = matcher.get_opcodes()  # Input size is strictly bounded above.
        _check(cancelled)
        result = DocumentDiff(state='ready', partial=partial, notes=notes)
        changed = [opcode for opcode in operations if opcode[0] != 'equal']
        if not changed:
            result.summary = ('읽은 일부 글자는 같아요. 확인하지 못한 부분은 원본에서 봐 주세요.' if partial else
                              '읽은 글자에서 차이를 찾지 못했어요. 그림과 서식은 원본에서 확인해 주세요.')
            return result
        output_characters = 0
        overflow = False
        for kind, a, b, c, d in operations:
            _check(cancelled)
            result.removed += b - a if kind in ('replace', 'delete') else 0
            result.added += d - c if kind in ('replace', 'insert') else 0
            if kind == 'equal':
                indices = list(range(a, min(b, a + 2)))
                if b - a > 4:
                    indices += [None] + list(range(b - 2, b))
                else:
                    indices = list(range(a, b))
                pairs = [('context', first[index], second[c + index - a]) if index is not None else
                         ('context', '⋯ 같은 내용 생략 ⋯', '⋯ 같은 내용 생략 ⋯') for index in indices]
            else:
                pairs = [(kind, first[a + offset] if a + offset < b else '',
                          second[c + offset] if c + offset < d else '') for offset in range(max(b - a, d - c))]
            for row_kind, old, new in pairs:
                if len(result.rows) >= MAX_DISPLAY_ROWS or output_characters + len(old) + len(new) > MAX_DISPLAY_CHARACTERS:
                    overflow = True
                    continue
                result.rows.append(dict(kind=row_kind, left=old, right=new))
                output_characters += len(old) + len(new)
        if overflow:
            result.partial = True
            result.notes.append('변경 내용이 많아 화면에는 앞부분만 표시했어요.')
        result.summary = f'왼쪽에서 빠진 내용 {result.removed}개 · 오른쪽에 추가된 내용 {result.added}개'
        if result.partial:
            result.summary += ' · 일부 범위만 비교/표시'
        result.notes.append('색과 −/+ 기호로 표시했어요. 공백과 줄바꿈은 간단히 정리해 비교해요.')
        return result
    except _Cancelled:
        return DocumentDiff(state='cancelled', summary='비교를 멈췄어요.')
