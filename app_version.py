"""User-visible build label and local source change detection."""
import hashlib
from pathlib import Path
import sys

VERSION='2026.09.22.8'

def source_stamp():
    if getattr(sys,'frozen',False):return VERSION
    root=Path(__file__).resolve().parent
    digest=hashlib.sha256()
    for path in sorted(root.glob('*.py')):
        if path.name.startswith(('test_','verify_','build_')):continue
        digest.update(path.name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
