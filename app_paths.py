"""Separate immutable bundled resources from persistent per-user state."""
import json
import os
from pathlib import Path
import shutil
import sys

BASE = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, 'frozen', False))
DATA = Path(os.environ['JJANGGU_DATA_DIR']) if os.environ.get('JJANGGU_DATA_DIR') else (
    Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'JjangguPocket' if FROZEN else BASE / '.local')

def runtime_root():
    config=DATA/'runtime.json'
    if config.exists():
        try:
            decoded=json.loads(config.read_text('utf-8-sig'))
            value=decoded.get('runtime_root') if isinstance(decoded,dict) else None
            if isinstance(value,str) and value.strip():
                root=Path(value)
                # A relative setting must not change meaning with the launcher's cwd.
                if root.is_absolute():return root
        except (OSError,ValueError,TypeError):pass
    return DATA/'runtime' if FROZEN else BASE

def model_cache(runtime=None):
    root=Path(runtime) if runtime is not None else runtime_root()
    return (DATA if root==BASE else root)/'models'

def runtime_modules(runtime=None):
    bundled=BASE/'ai-runtime/node_modules'
    root=Path(runtime) if runtime is not None else runtime_root()
    return bundled if bundled.is_dir() else root/'node_modules'

def node_path():
    bundled=BASE/'ai-runtime/node.exe'
    return str(bundled) if bundled.is_file() else shutil.which('node')

def ai_environment():
    env=os.environ.copy()
    env['JJANGGU_STATE_ROOT']=str(DATA)
    env['JJANGGU_RUNTIME_ROOT']=str(runtime_root())
    env['JJANGGU_NODE_MODULES']=str(runtime_modules())
    return env
