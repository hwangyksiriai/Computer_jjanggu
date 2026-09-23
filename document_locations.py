"""Explicit document-search locations, separate from file-organization settings."""
import os
from pathlib import Path


def normalize_document_roots(values, *, existing_only=True):
    """Canonical directories without duplicate or overlapping descendants.

    Relative/malformed settings are ignored rather than interpreted against an
    arbitrary launch directory. Missing absolute locations can remain in saved
    settings so a disconnected drive does not silently erase a user's choice.
    """
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
            continue
        try:
            path = Path(os.path.expandvars(str(value))).expanduser()
            if not path.is_absolute():
                continue
            path = path.resolve()
            if existing_only and not path.is_dir():
                continue
            if any(path.is_relative_to(parent) for parent in result):
                continue
            result = [parent for parent in result if not parent.is_relative_to(path)]
            result.append(path)
        except (OSError, ValueError, RuntimeError):
            continue
    return result


def ensure_document_roots(settings, source, vault):
    """Migrate once, preserving the old source/demo plus vault search scope.

    An explicit list (including an empty one) is authoritative. Neither source
    nor vault is added afterward; changing search locations never changes where
    organization moves files. Callers persist settings if this returns True.
    """
    if 'document_roots' in settings:
        return False
    settings['document_roots'] = [str(path) for path in normalize_document_roots(
        [source, vault], existing_only=False)]
    return True


def suggested_document_locations(home=None):
    """Offer existing common folders; discovering suggestions never selects them."""
    home = Path(home or Path.home())
    common = {'바탕화면': home / 'Desktop', '다운로드': home / 'Downloads', '문서': home / 'Documents'}
    onedrive = [('OneDrive', home / 'OneDrive')]
    if os.name == 'nt' and home == Path.home():
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                for label, name in (('바탕화면', 'Desktop'), ('다운로드', '{374DE290-123F-4565-9164-39C4925E467B}'), ('문서', 'Personal')):
                    try:
                        candidate = Path(os.path.expandvars(winreg.QueryValueEx(key, name)[0]))
                        if candidate.is_absolute() and candidate.is_dir():
                            common[label] = candidate
                    except (OSError, TypeError, ValueError):
                        pass
        except OSError:
            pass
        for name, label in (('OneDrive', 'OneDrive'), ('OneDriveConsumer', 'OneDrive (개인)'),
                            ('OneDriveCommercial', 'OneDrive (회사)')):
            if os.environ.get(name):
                onedrive.append((label, Path(os.environ[name])))
    result = []
    seen = set()
    # Keep distinct overlapping suggestions: the user can choose one folder or
    # its whole OneDrive. Only the chosen search roots collapse descendants.
    for label, value in [*common.items(), *onedrive]:
        paths = normalize_document_roots([value])
        if paths and paths[0] not in seen:
            seen.add(paths[0])
            result.append({'label': label, 'path': str(paths[0])})
    return result
