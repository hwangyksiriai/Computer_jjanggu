"""Portable, content-free checks that can be shared without search history."""
import os,platform,shutil,struct,tempfile
from pathlib import Path
from app_paths import DATA,BASE,runtime_root,node_path

def inspect_environment(data=DATA):
    checks=[]
    def item(ok,message):checks.append(dict(ok=bool(ok),message=message))
    item(os.name=='nt' and struct.calcsize('P')==8,'Windows 64비트 실행 환경' if os.name=='nt' and struct.calcsize('P')==8 else 'Windows 64비트 PC가 필요해요.')
    try:
        Path(data).mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=data):pass
        item(True,'설정과 검색 기록을 저장할 수 있어요.')
    except OSError:item(False,'앱 데이터 폴더에 저장할 수 없어요. 사용자 계정의 쓰기 권한을 확인해 주세요.')
    runtime=runtime_root();probe=runtime
    while not probe.exists() and probe!=probe.parent:probe=probe.parent
    try:
        free=shutil.disk_usage(probe).free
        item(free>=1024**3,f'AI 저장 공간: {free/1024**3:.1f}GB 여유'+(' · 추가 다운로드 전에 여유 있는 드라이브를 선택해 주세요.' if free<1024**3 else ''))
    except OSError:item(False,'AI 저장 위치에 접근할 수 없어요. 연결한 드라이브를 확인해 주세요.')
    item(bool(node_path()),'사진 검색 실행 환경 준비됨' if node_path() else '사진 검색 실행 환경이 없어요. 간편 실행판으로 실행해 주세요.')
    from local_ai import LocalAI
    ai=LocalAI()
    for capability,label in [('semantic','문서 의미 찾기'),('vision','사진의 모습 찾기'),('objects','사진 속 사물 찾기'),('speech','말로 찾기'),('chat','자유 대화')]:
        ready=ai.ready_for(capability)
        item(ready,label+(' 준비됨' if ready else ' 모델 미준비 · 이 기능만 따로 준비할 수 있어요.'))
    from voice_runtime import ready
    item(ready(),'새 문장을 만드는 음성 엔진 준비됨' if ready() else '새 문장 음성은 목소리 설정에서 별도로 준비해요.')
    return checks
