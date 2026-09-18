"""Device selection and local conversion to Whisper's 16 kHz input."""
import bootstrap
import numpy as np
import sounddevice as sd

def input_devices():
    return [(f"{d['name']} · {sd.query_hostapis(d['hostapi'])['name']}",i)
            for i,d in enumerate(sd.query_devices()) if d['max_input_channels']>0]

def input_config(selection=''):
    if selection:
        device=next((i for name,i in input_devices() if name==selection),None)
        if device is None: raise ValueError('선택한 마이크가 연결되어 있지 않아요. AI와 생활 설정에서 다시 선택해 주세요.')
    else:
        device=int(sd.default.device[0])
        if device<0: raise ValueError('기본 마이크가 없어요. AI와 생활 설정의 “마이크 선택”을 눌러 주세요.')
    info=sd.query_devices(device)
    for rate in dict.fromkeys((16000,int(info['default_samplerate']),48000,44100)):
        try:
            sd.check_input_settings(device=device,samplerate=rate,channels=1,dtype='float32')
            return device,rate
        except sd.PortAudioError: pass
    raise ValueError('이 마이크의 녹음 형식을 지원하지 못해요. 다른 입력 장치를 선택해 주세요.')

def to_16khz(audio,rate):
    data=np.asarray(audio,dtype=np.float32).reshape(-1)
    if rate==16000 or not len(data): return data
    if rate>16000:
        t=np.arange(-48,49,dtype=np.float64)
        cutoff=7200/rate
        kernel=2*cutoff*np.sinc(2*cutoff*t)*np.hamming(len(t))
        kernel/=kernel.sum()
        data=np.convolve(data,kernel,mode='same')
    count=round(len(data)*16000/rate)
    return np.interp(np.arange(count)*rate/16000,np.arange(len(data)),data).astype(np.float32)
