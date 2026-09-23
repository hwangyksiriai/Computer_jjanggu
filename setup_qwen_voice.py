"""Download pinned Qwen voice weights; no user uploads."""
import hashlib,json,urllib.request
from pathlib import Path

def check_cancelled(cancelled):
    if cancelled():raise RuntimeError('음성 준비를 중단했어요.')

def _file_hash(path,cancelled):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        while chunk:=source.read(1024**2):
            check_cancelled(cancelled);digest.update(chunk)
    return digest.hexdigest()

def download_file(url,path,cancelled=lambda:False,*,size=None,sha256=None,progress=lambda text:None):
    """Publish only complete downloads; cancellation also covers large model files."""
    check_cancelled(cancelled)
    path=Path(path);partial=path.with_suffix(path.suffix+'.part')
    count=0;next_report=128*1024**2;digest=hashlib.sha256()
    try:
        with urllib.request.urlopen(url,timeout=30) as response,partial.open('wb') as output:
            expected=size
            if expected is None:
                length=response.headers.get('Content-Length')
                if length is not None:expected=int(length)
            while chunk:=response.read(1024**2):
                check_cancelled(cancelled)
                output.write(chunk);digest.update(chunk);count+=len(chunk)
                if count>=next_report:
                    progress(f'{path.name}: {count//1024**2} MB');next_report+=128*1024**2
        check_cancelled(cancelled)
        if expected is not None and count!=expected:raise RuntimeError('다운로드 크기가 맞지 않아요: '+path.name)
        if sha256 and digest.hexdigest()!=sha256:raise RuntimeError('다운로드 파일 확인에 실패했어요: '+path.name)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)

def _model_info(quality):
    return ('Qwen/Qwen3-TTS-12Hz-1.7B-Base','fd4b254389122332181a7c3db7f27e918eec64e3','qwen3-tts-base-1.7b') if quality else (
        'Qwen/Qwen3-TTS-12Hz-0.6B-Base','5d83992436eae1d760afd27aff78a71d676296fc','qwen3-tts-base')

def _model_path(target,name):
    path=target/name
    if not path.resolve().is_relative_to(target.resolve()):raise RuntimeError('모델 다운로드 경로 오류')
    return path

def weights_ready(runtime,quality=False):
    """Check every downloaded file without hashing gigabytes on the UI thread."""
    repo,revision,directory=_model_info(quality);target=Path(runtime)/directory
    try:
        receipt=json.loads((target/'provenance.json').read_text('utf-8'))
        if receipt.get('repository')!=repo or receipt.get('revision')!=revision:return False
        files=receipt.get('files')
        if files:
            names={item['name'] for item in files}
            if not {'config.json','model.safetensors','speech_tokenizer/config.json','speech_tokenizer/model.safetensors'}<=names:return False
            return all(_model_path(target,item['name']).is_file() and _model_path(target,item['name']).stat().st_size==item['size']>0 for item in files)
        # Older home-PC installations have a provenance receipt without a manifest.
        for name in ('config.json','generation_config.json','tokenizer_config.json','vocab.json','merges.txt','speech_tokenizer/config.json'):
            if not (target/name).is_file() or not (target/name).stat().st_size:return False
        return all((target/name).is_file() and (target/name).stat().st_size>1024**2 for name in ('model.safetensors','speech_tokenizer/model.safetensors'))
    except (OSError,ValueError,TypeError,KeyError,RuntimeError):return False

def install_weights(runtime,progress=print,quality=True,cancelled=lambda:False):
    check_cancelled(cancelled)
    runtime=Path(runtime);repo,revision,directory=_model_info(quality)
    target=runtime/directory;target.mkdir(exist_ok=True,parents=True)
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true',timeout=30) as r:metadata=json.load(r)
    manifest=[]
    for item in metadata['siblings']:
        check_cancelled(cancelled)
        name=item['rfilename']
        if name.startswith('.'):continue
        path=_model_path(target,name);path.parent.mkdir(exist_ok=True,parents=True)
        expected=item.get('lfs',{}).get('sha256')
        manifest.append({'name':name,'size':item['size']})
        if path.is_file() and path.stat().st_size==item['size']:
            if not expected or _file_hash(path,cancelled)==expected:
                progress(name+' 확인 완료');continue
        progress('음성 모델 다운로드 · '+name)
        download_file(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',path,cancelled,
                      size=item['size'],sha256=expected,progress=progress)
    check_cancelled(cancelled)
    receipt=target/'provenance.json';temporary=receipt.with_suffix('.json.part')
    temporary.write_text(json.dumps({'repository':repo,'revision':revision,'license':'Apache-2.0','files':manifest},indent=2),encoding='utf-8')
    temporary.replace(receipt)
    progress('음성 모델 준비 완료')

if __name__=='__main__':
    from app_paths import runtime_root
    install_weights(runtime_root())
