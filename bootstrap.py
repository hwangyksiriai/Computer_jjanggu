from pathlib import Path
import sys
from app_paths import BASE as base, DATA, FROZEN, runtime_root
runtime=runtime_root()
vendor=runtime/'vendor'
# Source installs made before runtime.json used .local/vendor.
if runtime==base and not vendor.is_dir():vendor=base/'.local/vendor'
if not FROZEN and vendor.exists(): sys.path.insert(0,str(vendor))
