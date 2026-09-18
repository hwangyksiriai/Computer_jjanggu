# 집과 회사에서 Git으로 이어 작업하기

Git은 코드의 변경을 기록하고 주고받는 도구입니다. 두 컴퓨터 사이에 공유할 원격 저장소가 필요합니다. 이 프로젝트는 비공개 저장소 사용을 전제로 준비합니다.

## 처음 한 번: 집 PC

원격 저장소 주소를 연결하고 첫 커밋을 올립니다. 원격 저장소 생성·로그인이 완료되기 전에는 회사 PC에서 clone할 수 없습니다.

```powershell
git remote add origin <비공개 저장소 주소>
git push -u origin master
```

현재 초기 브랜치는 master입니다. 실제 브랜치가 달라졌다면 `git branch --show-current`로 확인하세요. origin이 이미 있으면 중복 추가하지 않습니다.

## 처음 한 번: 회사 PC

GitHub 연결 전에 USB로 가져갈 경우 `배포/작은주머니-개발이력.bundle`을 회사 PC로 복사한 뒤 `git clone 작은주머니-개발이력.bundle shinchan-pocket`으로 소스와 커밋 이력을 복원할 수 있습니다. 이 파일은 생성 시점의 스냅샷이므로 이후 작업분은 다시 생성해야 합니다. 나중에 GitHub를 쓰면 origin을 해당 원격 주소로 변경합니다.

1. Git과 Codex를 설치하고 저장소 계정에 로그인합니다.
2. 작업할 폴더에서 아래 명령을 실행합니다.

```powershell
git clone <비공개 저장소 주소> shinchan-pocket
cd shinchan-pocket
```

3. Codex에서 `shinchan-pocket` 폴더를 로컬 프로젝트로 엽니다.
4. “작업인계.md를 읽고 회사 PC 환경 설정부터 이어서 진행해”라고 입력합니다.
5. Python·Node.js와 모델을 회사 PC에 준비합니다. Git clone만으로 실행 환경이 설치되지는 않습니다.

## 매일 작업 순서

작업을 시작하기 전에:

```powershell
git status
git pull --ff-only
```

작업을 마치고 다른 PC로 이동하기 전에:

```powershell
git status
git add .
git commit -m "Describe today's changes"
git push
```

한 PC에서 push가 끝난 뒤 다른 PC에서 pull하세요. `pull --ff-only`가 실패하면 양쪽 변경을 확인해야 하므로 Codex에 오류 내용을 알려주세요. 강제 덮어쓰기 대신 변경을 합칩니다.

## Git으로 전달되는 것

앱 코드, 캐릭터 이미지, 샘플 문서, 설치 스크립트, 테스트, 사용법, 작업인계 문서입니다.

## 별도로 준비할 것

- **AI 모델과 라이브러리**: 크고 PC별 경로가 달라 Git에 넣지 않습니다. 회사 PC에서 설치하거나 외장 저장장치로 가져와 경로를 맞춥니다.
- **음성 작업 자료**: `.local/voice-reference`, `.local/voice-clips`. 사용자 제공 음성을 옮겨야 같은 목소리 작업을 재현할 수 있습니다. 아직 별도 자료 ZIP은 생성하지 않았습니다.
- **개인 문서와 검색 DB**: 실제 파일과 위치에 연결된 PC별 데이터입니다. 회사 PC에서 연결한 폴더를 다시 분석할 수 있습니다. 실제 문서를 원격 저장소에 넣을 범위는 별도 확인 중입니다.
- **설정**: `.local/runtime.json`, `.local/settings.json`은 PC별 절대 경로가 있으므로 회사 PC에 맞춰 구성합니다.

배포 ZIP은 다른 사람의 체험용이고 Git 저장소는 개발용입니다. 오래된 배포 ZIP으로 개발 파일을 덮어쓰지 마세요.
