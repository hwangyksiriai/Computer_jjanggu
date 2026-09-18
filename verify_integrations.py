"""Checks real Windows OCR and Korean speech synthesis on generated fixtures."""
import json
from pathlib import Path
import subprocess
import wave
from PIL import Image, ImageDraw, ImageFont
from core import BASE, extract, classify

folder=BASE/'.local'/'verification'; folder.mkdir(parents=True,exist_ok=True)
im=Image.new('RGB',(1000,500),'white')
draw=ImageDraw.Draw(im)
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',40)
draw.multiline_text((40,40),'INVOICE\nBill to: Pocket Studio\nAmount due: USD 1320\nPayment due: October 1',font=font,fill='black',spacing=24)
path=folder/'scan_0032.png'; im.save(path)
text,status=extract(path)
assert 'invoice' in text.lower(), (text,status)
assert classify(path.name,text)=='인보이스'
print('Windows OCR + invoice classification: PASS')
voice_path=folder/'voice-preview.wav'
request={'text':'안녕! 나는 짱구. 파일은 내가 챙길게!','voice':'Microsoft Heami Desktop','rate':1,'volume':70}
r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(BASE/'speech.ps1'),
                  '-OutputPath',str(voice_path)],input=json.dumps(request,ensure_ascii=False).encode('utf-8'),capture_output=True,timeout=25,creationflags=0x08000000)
assert r.returncode==0, r.stderr
with wave.open(str(voice_path),'rb') as f:
    duration=f.getnframes()/f.getframerate()
    assert 1 < duration < 15, duration
print(f'Korean speech: PASS ({duration:.2f}s WAV)')
