"""Local original-recording clips for character reactions; no voice cloning."""
import bootstrap
import json,subprocess,uuid,wave,io
from pathlib import Path
import numpy as np
from core import BASE

EVENTS={'greeting':'클릭 인사','search':'검색 시작','empty':'검색 결과 없음','dance':'춤 반응','clean':'정리 완료','found':'파일 발견'}
def event_for(text):
    if any(s in text for s in ('뭘 찾아줄까','무슨 일을 도와','뭐 찾고 있어')): return 'greeting'
    if '정리 끝' in text: return 'clean'
    if '훌라' in text or '같이 춤' in text: return 'dance'
    if '개 찾았어' in text: return 'found'
    if '돋보기 들고 찾아볼게' in text or '파일을 찾아볼게' in text: return 'search'
    if '다른 단서로도 찾아볼까' in text: return 'empty'
    return None

def make_clip(source,start,end,destination):
    import imageio_ffmpeg
    source=Path(source).resolve(); destination=Path(destination).resolve()
    if not source.is_file(): raise ValueError('음성/영상 파일을 선택해 주세요.')
    start=float(start); end=float(end)
    if not 0<=start<end or end-start>15: raise ValueError('0초 이후에서 0~15초 길이의 구간을 선택해 주세요.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    result=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-ss',str(start),'-i',str(source),
                           '-t',str(end-start),'-vn','-ac','1','-ar','24000','-c:a','pcm_s16le',str(destination)],capture_output=True,timeout=45,creationflags=0x08000000)
    if result.returncode: raise ValueError('선택한 파일의 음성을 읽지 못했어요.')
    with wave.open(str(destination),'rb') as w:
        if w.getnframes()/w.getframerate()<.15: raise ValueError('선택한 구간에 음성이 없어요.')
    return str(destination)

def scaled_wav(path,volume):
    with wave.open(str(path),'rb') as w:
        params=w.getparams(); data=w.readframes(w.getnframes())
    if params.sampwidth!=2: raise ValueError('16비트 WAV를 사용해 주세요.')
    signal=np.frombuffer(data,dtype='<i2').astype(np.float32)*max(0,min(100,float(volume)))/100
    output=io.BytesIO()
    with wave.open(output,'wb') as w:
        w.setparams(params); w.writeframes(signal.astype('<i2').tobytes())
    return output.getvalue()

if __name__=='__main__':
    import sys,winsound
    request=json.loads(sys.stdin.buffer.read().decode('utf-8'))
    winsound.PlaySound(scaled_wav(request['path'],request['volume']),winsound.SND_MEMORY)
