"""Verify actual app phrases through the same local speech client as the app."""
import bootstrap
import argparse,json,time
from pathlib import Path
from app_paths import DATA
from qwen_voice import QwenVoiceClient

PHRASES=['뭘 찾아줄까?','돋보기 들고 찾아볼게!',
         '후보 파일 3개를 찾았어. 미리보기로 확인해 봐!']

def verify(reference,phrases=None):
    # The client supplies the reference transcript, runtime interpreter and
    # environment, and rejects generated speech that fails its content check.
    client=QwenVoiceClient();report=[]
    try:
        for text in PHRASES if phrases is None else phrases:
            started=time.monotonic()
            print('Synthesizing: '+text,flush=True)
            path=Path(client.generate(text,str(reference)))
            quality=json.loads(path.with_suffix('.quality.json').read_text('utf-8'))
            result=dict(path=str(path),text=text,seconds_to_generate=round(time.monotonic()-started,2),**quality)
            report.append(result)
            print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:client.close()
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,default=DATA/'voice-reference/comparison/reference-0.wav')
    parser.add_argument('--output',type=Path,default=DATA/'voice-reference/qwen-verification.json')
    args=parser.parse_args()
    report=verify(args.reference)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
