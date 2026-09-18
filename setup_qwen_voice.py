"""Download pinned Qwen voice weights to the configured runtime; no user uploads."""
import hashlib,json,urllib.request
from pathlib import Path
base=Path(__file__).parent
runtime=Path(json.loads((base/'.local/runtime.json').read_text('utf-8-sig'))['runtime_root'])
repo='Qwen/Qwen3-TTS-12Hz-0.6B-Base'; revision='5d83992436eae1d760afd27aff78a71d676296fc'
target=runtime/'qwen3-tts-base'; target.mkdir(exist_ok=True)
with urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true',timeout=60) as r:metadata=json.load(r)
for item in metadata['siblings']:
    name=item['rfilename']
    if name.startswith('.'):continue
    path=target/name; path.parent.mkdir(exist_ok=True,parents=True)
    expected=item.get('lfs',{}).get('sha256')
    if path.exists() and path.stat().st_size==item['size']:
        if not expected or hashlib.file_digest(path.open('rb'),'sha256').hexdigest()==expected:
            print(name+' verified',flush=True);continue
    partial=path.with_suffix(path.suffix+'.part'); count=0; next_report=128*1024**2; digest=hashlib.sha256()
    print('Downloading '+name,flush=True)
    with urllib.request.urlopen(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',timeout=180) as response,partial.open('wb') as output:
        while chunk:=response.read(1024**2):
            output.write(chunk);digest.update(chunk);count+=len(chunk)
            if count>=next_report:print(f'{name}: {count//1024**2} MB',flush=True);next_report+=128*1024**2
    assert count==item['size'],name
    if expected:assert digest.hexdigest()==expected,name
    partial.replace(path)
(target/'provenance.json').write_text(json.dumps({'repository':repo,'revision':revision,'license':'Apache-2.0'},indent=2),encoding='utf-8')
print('VOICE_MODEL_READY',flush=True)
