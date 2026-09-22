"""Local speech-content checks; these do not measure character voice likeness."""
import re
import unicodedata
from difflib import SequenceMatcher

ONES=['','한','두','세','네','다섯','여섯','일곱','여덟','아홉']
TENS=['','열','스물','서른','마흔','쉰','예순','일흔','여든','아흔']
COUNT_WORDS={TENS[n//10]+ONES[n%10]:n for n in range(1,100)}
COUNT_WORDS['스무']=20
COUNT_PATTERN=re.compile(r'('+('|'.join(sorted(COUNT_WORDS,key=len,reverse=True)))+r'|\d[\d,]*)\s*개')

def speech_text(text):
    def replace(match):
        number=int(match.group(1).replace(',',''))
        if number==20:return '스무 개'
        if 0<number<100:return TENS[number//10]+ONES[number%10]+' 개'
        return match.group()
    return re.sub(r'(\d[\d,]*)\s*개',replace,text)

def counts(text):
    values=[]
    for match in COUNT_PATTERN.finditer(text):
        value=match.group(1)
        values.append(COUNT_WORDS.get(value) if value in COUNT_WORDS else int(value.replace(',','')))
    return values

def normalized(text):
    return re.sub(r'[^\w가-힣]','',speech_text(unicodedata.normalize('NFKC',text).lower()))

def content_check(expected,heard):
    a,b=normalized(expected),normalized(heard)
    score=SequenceMatcher(None,a,b,autojunk=False).ratio()
    repeated=bool(re.search(r'(.{1,8})\1{4,}',b))
    # Do not play a different result count just because
    # the rest of a generic sentence resembles the requested text.
    numbers_ok=counts(expected)==counts(heard)
    return dict(ok=bool(a and b and score>=.78 and not repeated and numbers_ok),
                similarity=round(score,3),heard=heard,repeated=repeated)

def transcribe_wav(ai,path):
    import wave
    import numpy as np
    with wave.open(str(path),'rb') as wav:
        if wav.getsampwidth()!=2:raise ValueError('16비트 음성 파일이 필요해요.')
        rate=wav.getframerate(); channels=wav.getnchannels()
        values=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').reshape(-1,channels).mean(axis=1).astype(np.float32)/32768
    if not len(values):raise ValueError('음성 파일이 비어 있어요.')
    duration=len(values)/rate
    if duration>35:raise ValueError('음성이 지나치게 길어 재생을 중단했어요.')
    samples=np.interp(np.arange(int(len(values)*16000/rate))*rate/16000,np.arange(len(values)),values).astype(np.float32)
    return ai.call('transcribe',audio=samples.tolist())
