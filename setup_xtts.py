"""Explicit one-time download of voice model weights; never uploads recordings."""
import json
from pathlib import Path
import urllib.request

base=Path(__file__).parent
runtime=Path(json.loads((base/'.local/runtime.json').read_text('utf-8-sig'))['runtime_root'])
target=runtime/'xtts-v2'; target.mkdir(exist_ok=True)
with urllib.request.urlopen('https://huggingface.co/api/models/coqui/XTTS-v2',timeout=60) as response:
    revision=json.load(response)['sha']
for name in ('config.json','vocab.json','speakers_xtts.pth','model.pth'):
    path=target/name
    if path.exists(): print(name,'already downloaded',flush=True); continue
    url=f'https://huggingface.co/coqui/XTTS-v2/resolve/{revision}/{name}'
    print('downloading',name,flush=True)
    with urllib.request.urlopen(url,timeout=180) as response, path.with_suffix(path.suffix+'.part').open('wb') as output:
        while chunk:=response.read(1024*1024): output.write(chunk)
    path.with_suffix(path.suffix+'.part').replace(path)
(target/'provenance.json').write_text(json.dumps({'repository':'coqui/XTTS-v2','revision':revision,'license':'Coqui Public Model License'},indent=2),encoding='utf-8')
print('voice model downloaded',flush=True)
