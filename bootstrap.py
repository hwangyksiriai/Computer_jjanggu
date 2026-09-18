from pathlib import Path
import sys
import json
base=Path(__file__).resolve().parent
runtime_file=base/'.local'/'runtime.json'
runtime=Path(json.loads(runtime_file.read_text('utf-8-sig'))['runtime_root']) if runtime_file.exists() else base/'.local'
vendor=runtime/'vendor'
if vendor.exists(): sys.path.insert(0,str(vendor))
