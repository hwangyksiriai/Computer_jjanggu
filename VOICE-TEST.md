# 첨부 영상 기반 로컬 음성 시험

2026-09-18. 사용자 제공 MP4(31.856초)에서 24kHz 모노 WAV를 로컬로 추출했습니다. 영상·음성은 외부로 업로드하지 않았습니다.

- 분리 환경: D:\ShinchanPocketRuntime\desktop-pet\voice-env (Python 3.12, torch/torchaudio 2.8 CPU, coqui-tts 0.27.5, transformers 4.57.6).
- 공식 XTTS-v2 모델 다운로드 완료. 모델 revision과 출처는 D:\ShinchanPocketRuntime\desktop-pet\xtts-v2\provenance.json.
- 첫 합성: “뭘 찾아줄까? 찾고 싶은 파일을 말해 줘.”를 입력하여 4.875초 WAV 생성. 모델 로딩 이후 합성 23.125초.
- 출력: .local/voice-reference/synthetic-preview.wav. AI가 새로 합성한 시험본이며 원본 녹음이 아님.
- 파형은 정상적으로 생성됐으나 작은 로컬 Whisper가 “잘 부탁드립니다.”로 인식해, 요청 문장의 발음과 일치한다고 검증하지 못했습니다. 원본 참고 음성 역시 인식 결과가 짧아 자동 인식만으로 원인을 단정할 수 없습니다.
- 음색 유사도·발음·배경음 영향은 청취 확인이 필요합니다. 이 시험 음성을 앱의 기본 음성으로 자동 적용하지 않았습니다. 현재 인사와 응답은 Windows 한국어 TTS입니다.
- 재현: 분리 환경의 python으로 verify_xtts.py 실행. 실행 중 모델 원격 요청은 비활성화됩니다.

XTTS 모델은 Coqui Public Model License를 사용하며, 배포 시 모델·참고 음성의 이용 범위를 별도로 확인해야 합니다.

## 요청별 대사 재시험

### 새 사용자 영상 시험

### 생성 설정 비교 재시험

- `compare_voice.py`: 새 영상 0–8초, 20–28초, 30–40초를 각각 참고 구간으로 사용. 명시적 평가 모드, 반복 패널티 2/5, 확률/결정적 생성 비교.
- 한국어 전처리 결과 `al-ass-eo. inbo-iseu chaj-abolge.` 및 22개 토큰 확인. 입력이 다른 문장으로 바뀌는 문제는 관찰되지 않음.
- 세 시험본의 로컬 Whisper 결과는 각각 “감사합니다!”, “저거...”, “하...”. 음성 인식 자체의 한계는 있지만 요청 문장 일치 검증은 세 건 모두 실패. 음색 유사성 개선도 확인하지 못함.
- 결과 `.local/voice-reference/comparison/verification.json`. 앱 음성에 미적용. 원본 추가 요청이나 같은 설정 반복보다 다른 합성 엔진 검토가 필요.

- 입력: `C:/Users/User/Downloads/ㅁㄴㅇㄻㄴㄻㄴ.mp4`, 41.911초. 원본 보존, `.local/voice-reference/new-reference.wav`에 로컬 추출.
- 새 참고 음성으로 “알았어. 인보이스 찾아볼게.” 합성. 출력 `new-search-trial.wav` 3.339초. 로컬 Whisper 인식: “진짜 진짜…”. 인식 오류 가능성은 있으나 문장 정확성 미검증으로 앱에 미적용. 원본만 원인이라고 단정할 수 없음.
- 시험 결과 `new-trial-report.json`, 원본 구간별 인식 `new-transcript.json`에 기록. 시험 도구에 reference/output/text CLI 인자 추가.

- 등록된 인사 클립을 짧은 참고 음성으로 사용하고 조건 길이 3초, 최대 참고 길이 6초로 재시험(`trial_voice_reply.py`). 입력: “알았어. 파일을 찾아볼게.”
- 출력 `.local/voice-reference/search-trial.wav`를 로컬 Whisper로 확인한 결과 “잘 부탁드립니다!”로 인식. 문장 정확성을 검증하지 못해 앱에 연결하지 않음. 음색 품질도 보장하지 않음.
- 모든 요청에 인사 클립을 대신 재생하던 fallback 제거. 원본 전용 모드에서 요청에 맞는 등록 클립이 없으면 텍스트 답변과 음성 미준비 안내만 표시하며 Windows TTS로 전환하지 않음.
- 검색 시작/결과 없음 이벤트를 원본 음성 연결 설정에 추가. 검색 완료는 기존 파일 발견 이벤트 사용. 기존 인사 클립은 클릭 인사에만 사용.
