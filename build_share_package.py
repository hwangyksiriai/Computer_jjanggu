"""Allowlisted trial package. Never copy local settings, indexes or recordings."""
from pathlib import Path
import hashlib,html,json,re,zipfile

BASE=Path(__file__).resolve().parent
SOURCES=['app.py','app_paths.py','app_version.py','search_status.py','easy_app.py','quick_bubble.py','ime_entry.py','single_instance.py','desktop_entry.py','enhancements.py','core.py','knowledge.py','visual_query.py','bootstrap.py','local_ai.py','qwen_voice.py','qwen_voice_worker.py',
         'photo_controller.py','photo_library.py','photo_gallery.py','photo_viewer.py','photo_recycle.py','photo_query.py','accessibility.py','diagnostics.py',
         'voice_runtime.py','voice_quality.py','voice_setup_ui.py','setup_qwen_voice.py','verify_easy.py','verify_photo.py',
         'audio_input.py','desktop_room.py','file_watch.py','monitors.py','pet_bubble.py',
         'reactions.py','result_browser.py','result_refinement.py','voice_clips.py','windows_features.py',
         'setup_runtime.py','ai_worker.mjs','speech.ps1','ocr.ps1','requirements.txt','package.json','package-lock.json',
         '처음설치.bat','AI설치.bat','사용자-사용법.md','검색-사용법.md','사진-갤러리-사용법.md','처음-사용하기.txt']
ASSETS=['shinchan-sheet.png','shinchan-actions.png','shinchan-room-anime-v2.png','shinchan-room-wallpaper.png',
        'PROMPT.md','ACTIONS-PROMPT.md','ROOM-ANIME-V2-PROMPT.md','ROOM-PROMPT.md']

def render_guide():
    text=(BASE/'사용자-사용법.md').read_text(encoding='utf-8')
    blocks=[]
    for part in text.split('\n\n'):
        escaped=html.escape(part)
        escaped=re.sub(r'\*\*(.+?)\*\*',r'<strong>\1</strong>',escaped)
        escaped=re.sub(r'`([^`]+)`',r'<code>\1</code>',escaped)
        if escaped.startswith('# '):blocks.append('<h1>'+escaped[2:]+'</h1>')
        elif escaped.startswith('## '):blocks.append('<h2>'+escaped[3:]+'</h2>')
        else:blocks.append('<p>'+escaped+'</p>')
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>작은 주머니 사용법</title><style>
body{margin:0;background:#faf7ef;color:#282a25;font-family:"맑은 고딕",sans-serif;line-height:1.9}main{max-width:820px;margin:32px auto;padding:32px;background:white;border-radius:20px}h1{font-size:28px}h2{font-size:21px;margin-top:42px;border-top:1px solid #e6e6df;padding-top:24px;color:#386046}p{white-space:pre-line;overflow-wrap:anywhere}code{background:#f3f1e9;padding:2px 5px;border-radius:4px}strong{color:#315b3d}@media(max-width:600px){main{margin:0;padding:20px;border-radius:0}h1{font-size:23px}}@media print{body{background:white}main{margin:0;padding:0}h2{break-after:avoid}p{orphans:3;widows:3}}</style><main>'''+''.join(blocks)+'</main></html>'
    guide=BASE/'사용자-사용법.html'; guide.write_text(page,encoding='utf-8')
    return guide

def build():
    guide=render_guide()
    files={name:BASE/name for name in SOURCES}
    files.update({'assets/'+name:BASE/'assets'/name for name in ASSETS})
    files['실행.bat']=BASE/'배포용_실행.bat'; files['사용자-사용법.html']=guide
    for path in files.values():
        if not path.is_file():raise FileNotFoundError(path)
    output=BASE/'배포'; output.mkdir(exist_ok=True)
    archive=output/'작은주머니-Windows-체험판.zip'; manifest={}
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for name,path in files.items():
            assert not any(part in {'.local','.venv','node_modules','__pycache__'} for part in Path(name).parts)
            data=path.read_bytes()
            if name.endswith('.bat'):data=data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
            z.writestr('작은주머니/'+name,data)
            manifest[name]=hashlib.sha256(data).hexdigest()
        z.writestr('작은주머니/package-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(files)+1
        for name,digest in manifest.items():assert hashlib.sha256(z.read('작은주머니/'+name)).hexdigest()==digest
    print(json.dumps({'zip':str(archive),'bytes':archive.stat().st_size,'files':len(files),'privacy':'allowlist only; no personal settings, database, audio, runtime or models'},ensure_ascii=False))
    return archive

if __name__=='__main__':build()
