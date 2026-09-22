"""Download pinned Qwen voice weights; no user uploads."""
import hashlib,json,urllib.request
from pathlib import Path

def install_weights(runtime,progress=print,quality=True):
    runtime=Path(runtime)
    repo='Qwen/Qwen3-TTS-12Hz-1.7B-Base' if quality else 'Qwen/Qwen3-TTS-12Hz-0.6B-Base'
    revision='fd4b254389122332181a7c3db7f27e918eec64e3' if quality else '5d83992436eae1d760afd27aff78a71d676296fc'
    target=runtime/('qwen3-tts-base-1.7b' if quality else 'qwen3-tts-base'); target.mkdir(exist_ok=True)
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true',timeout=60) as r:metadata=json.load(r)
    for item in metadata['siblings']:
        name=item['rfilename']
        if name.startswith('.'):continue
        path=target/name; path.parent.mkdir(exist_ok=True,parents=True)
        if not path.resolve().is_relative_to(target.resolve()):raise RuntimeError('모델 다운로드 경로 오류')
        expected=item.get('lfs',{}).get('sha256')
        if path.exists() and path.stat().st_size==item['size']:
            with path.open('rb') as existing:valid=not expected or hashlib.file_digest(existing,'sha256').hexdigest()==expected
            if valid:
                progress(name+' 확인 완료');continue
        partial=path.with_suffix(path.suffix+'.part'); count=0; next_report=128*1024**2; digest=hashlib.sha256()
        progress('음성 모델 다운로드 · '+name)
        with urllib.request.urlopen(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',timeout=180) as response,partial.open('wb') as output:
            while chunk:=response.read(1024**2):
                output.write(chunk);digest.update(chunk);count+=len(chunk)
                if count>=next_report:progress(f'{name}: {count//1024**2} MB');next_report+=128*1024**2
        assert count==item['size'],name
        if expected:assert digest.hexdigest()==expected,name
        partial.replace(path)
    (target/'provenance.json').write_text(json.dumps({'repository':repo,'revision':revision,'license':'Apache-2.0'},indent=2),encoding='utf-8')
    progress('음성 모델 준비 완료')

if __name__=='__main__':
    from app_paths import runtime_root
    install_weights(runtime_root())
