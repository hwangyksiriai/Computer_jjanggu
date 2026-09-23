"""Read-only, local comparison of a supplied search-result snapshot.

Content groups require equal SHA-256 digests of stable regular files. File names
and timestamps alone never establish equal content. Nothing is moved or deleted.
"""
from collections import defaultdict
from dataclasses import dataclass,field
import hashlib
import os
from pathlib import Path
import re
import stat
import unicodedata


_CHUNK_SIZE=1024*1024


@dataclass
class DuplicateAnalysis:
    groups:list[list[dict]]=field(default_factory=list)
    unverified:list[str]=field(default_factory=list)
    hash_status:dict[str,str]=field(default_factory=dict)
    cancelled:bool=False


class _Cancelled(Exception):pass
class _Unverified(Exception):pass


def _check_cancelled(cancelled):
    if cancelled():raise _Cancelled()


def _path_key(value):
    return os.path.normcase(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _signature(value):
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def _file_stat(path):
    value=path.stat(follow_symlinks=False)
    # Do not open directories, links, FIFOs, devices, or other special files.
    if not stat.S_ISREG(value.st_mode):raise _Unverified()
    return value


def _hash_file(path,before,cancelled):
    _check_cancelled(cancelled)
    if _signature(_file_stat(path))!=_signature(before):raise _Unverified()
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        # Some Windows/Python combinations expose creation time in path.stat's
        # ctime and change time in fstat's ctime. Compare ctime only between
        # observations from the same source; identity/size/mtime must all match.
        if _signature(opened)[:-1]!=_signature(before)[:-1]:raise _Unverified()
        remaining=before.st_size
        # Stop at the original size even if another process keeps appending.
        while remaining:
            _check_cancelled(cancelled)
            chunk=stream.read(min(_CHUNK_SIZE,remaining))
            if not chunk:raise _Unverified()
            digest.update(chunk);remaining-=len(chunk)
        _check_cancelled(cancelled)
        if stream.read(1):raise _Unverified()
        if _signature(os.fstat(stream.fileno()))!=_signature(opened):raise _Unverified()
    if _signature(_file_stat(path))!=_signature(before):raise _Unverified()
    return digest.hexdigest()


def group_identical_files(rows,cancelled=lambda:False)->DuplicateAnalysis:
    """Group stable equal bytes, hashing only files with a possible size match.

    ``unique`` means no match among the successfully checked files in this run;
    any ``unverified`` files remain unknown and must not be called different.
    Cancelled runs publish no groups. There is deliberately no persistent hash
    cache: replacing content while preserving size/mtime cannot reuse old hashes.
    Rows are never modified, and repeated references to one path count once.
    """
    result=DuplicateAnalysis();entries=[];seen=set();buckets=defaultdict(list)
    for row in rows:
        if not isinstance(row,dict):continue
        value=row.get('path')
        try:
            display=os.fspath(value)
            key=_path_key(display)
            if not display or not Path(display).is_absolute():raise ValueError()
        except (TypeError,ValueError,OSError):
            display=str(value or row.get('name') or '파일 위치 없음')
            result.hash_status[display]='unverified'
            if display not in result.unverified:result.unverified.append(display)
            continue
        if key in seen:continue
        seen.add(key);entries.append((display,Path(display),row))
    try:
        for display,path,row in entries:
            _check_cancelled(cancelled)
            try:
                before=_file_stat(path)
                buckets[before.st_size].append((display,path,row,before))
            except (OSError,ValueError,_Unverified):
                result.hash_status[display]='unverified';result.unverified.append(display)
        hashes={}
        stable=[]
        for candidates in buckets.values():
            for display,path,row,before in candidates:
                _check_cancelled(cancelled)
                try:
                    if len(candidates)>1:hashes[display]=_hash_file(path,before,cancelled)
                    stable.append((display,path,row,before))
                    result.hash_status[display]='unique'
                except (OSError,ValueError,_Unverified):
                    result.hash_status[display]='unverified';result.unverified.append(display)
        groups=defaultdict(list)
        for display,path,row,before in stable:
            _check_cancelled(cancelled)
            try:
                # A previously hashed file may change while other files are read.
                if _signature(_file_stat(path))!=_signature(before):raise _Unverified()
                if display in hashes:groups[(before.st_size,hashes[display])].append((display,row))
            except (OSError,ValueError,_Unverified):
                result.hash_status[display]='unverified';result.unverified.append(display)
        for group in groups.values():
            if len(group)<2:continue
            result.groups.append([row for display,row in group])
            for display,row in group:result.hash_status[display]='identical'
    except _Cancelled:
        result.cancelled=True;result.groups=[]
        for display,path,row in entries:
            if result.hash_status.get(display)!='unverified':result.hash_status[display]='cancelled'
            if display not in result.unverified:result.unverified.append(display)
    return result


_NUMBER=r'[0-9]{1,3}(?:\.[0-9]{1,3})*'
_VERSION=r'(?:version|ver\.?|revision|rev\.?|v)\s*'+_NUMBER
_KOREAN=r'(?:최종본|최종|수정본|복사본)(?:[ _-]*[1-9][0-9]{0,2})?'
_TAILS=(
    re.compile(r'\s*[\[(]\s*(?:'+_VERSION+'|'+_KOREAN+r'|[1-9][0-9]{0,2})\s*[\])]\s*$',re.I),
    re.compile(r'[\s_.-]+'+_VERSION+r'\s*$',re.I),
    re.compile(r'[\s_.-]*'+_KOREAN+r'\s*$'),
)


def version_family(path)->str:
    """Remove explicit trailing version/copy tags, preserving dates and purpose.

    Plain trailing numbers, years, dates, month labels, customer names, document
    types and file extensions remain significant. This is a filename heuristic,
    never a claim about content or which version is latest.
    """
    try:
        name=Path(os.fspath(path)).name
    except (TypeError,ValueError):return ''
    normalized=unicodedata.normalize('NFKC',name).casefold().strip()
    suffix=Path(normalized).suffix
    stem=normalized[:-len(suffix)] if suffix else normalized
    stem=re.sub(r'\s+',' ',stem).strip()
    if not stem:return normalized
    while True:
        candidate=stem
        for pattern in _TAILS:
            candidate=pattern.sub('',candidate).rstrip(' _-.')
        if not candidate or candidate==stem:break
        stem=candidate
    return stem+suffix


def version_candidates(selected,rows)->list[dict]:
    """Return explicitly labelled similar-name candidates in input order."""
    selected_path=selected.get('path') if isinstance(selected,dict) else selected
    family=version_family(selected_path)
    if not family:return []
    try:selected_key=_path_key(selected_path)
    except (TypeError,ValueError,OSError):return []
    result=[];seen={selected_key}
    for row in rows:
        if not isinstance(row,dict):continue
        value=row.get('path')
        try:key=_path_key(value)
        except (TypeError,ValueError,OSError):continue
        if key in seen or version_family(value)!=family:continue
        seen.add(key)
        result.append(dict(row,comparison_kind='similar_version',
                           comparison_note='이름이 비슷한 버전 후보예요. 내용은 다를 수 있어요.'))
    return result
