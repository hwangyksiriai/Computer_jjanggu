"""Regression checks for desktop surfaces, watcher delivery, and original voice clips."""
import bootstrap
import tkinter as tk
from pathlib import Path
import shutil,uuid,time,wave
from unittest.mock import patch
import numpy as np
from enhancements import EnhancedApp
from desktop_room import DesktopRoom
from voice_clips import make_clip,scaled_wav,event_for
from monitors import enable_dpi_awareness

base=(Path(__file__).parent/'.local/tests').resolve(); tmp=base/uuid.uuid4().hex; tmp.mkdir(parents=True)
enable_dpi_awareness(); root=tk.Tk(); errors=[]
root.report_callback_exception=lambda *a:errors.append(str(a[1]))
app=EnhancedApp(root,tmp/'state'); app.settings['sound']=False; app.ai.ready=lambda:False
source=tmp/'source'; source.mkdir(); app.settings['source']=str(source); app.settings['vault']=str(tmp/'vault')
def pump(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end: root.update(); time.sleep(.03)
def until(check,seconds=15):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        root.update()
        if check(): return
        time.sleep(.05)
    raise AssertionError('Timed out')
try:
    pump(1.5); until(lambda:not app.indexing)
    p=source/'새 문서.txt'; p.write_text('Invoice amount due USD 35',encoding='utf-8')
    until(lambda:any(r['name']==p.name for r in app.library.rows()))
    assert not app.settings['auto']; print('new file indexed without auto organize: PASS',flush=True)
    p.write_text('Updated invoice USD 400',encoding='utf-8')
    until(lambda:any('400' in r['body'] for r in app.library.rows()))
    p.unlink(); until(lambda:not app.library.rows()); print('modified and deleted file refresh: PASS',flush=True)
    app.hide()
    with patch('desktop_room.on_desktop',return_value=False):
        room=DesktopRoom(app)
        assert len(room.windows)==len(app.pet.space.screens())
        for win,space,regions in room.windows:
            assert len(regions)==4
        with patch('desktop_room.on_desktop',return_value=True):
            room.tick()
            assert all(space.u.IsWindowVisible(space.handle()) for _,space,_ in room.windows)
        room.tick()
        assert all(not space.u.IsWindowVisible(space.handle()) for _,space,_ in room.windows)
        room.activate('search'); root.update(); assert app.bubble.alive() and root.state()=='withdrawn'
        prior=app.settings['focus_manual']; room.activate('focus'); assert app.settings['focus_manual']!=prior
        room.activate('tray'); assert app.page=='tray'
        room.activate('closet'); assert app.page=='closet'
        room.close(); assert not room.windows
    print('desktop room per-monitor surfaces + furniture actions + close: PASS',flush=True)
    original=tmp/'original.wav'; rate=24000
    with wave.open(str(original),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((np.sin(np.arange(rate*2)*2*np.pi*440/rate)*12000).astype('<i2').tobytes())
    clip=tmp/'voice.wav'; make_clip(original,.25,1.25,clip)
    with wave.open(str(clip),'rb') as w: assert abs(w.getnframes()/w.getframerate()-1)<.02
    import io
    with wave.open(io.BytesIO(scaled_wav(clip,0)),'rb') as w: assert not np.any(np.frombuffer(w.readframes(w.getnframes()),dtype='<i2'))
    assert event_for('뭘 찾아줄까?')=='greeting'
    assert event_for('3개 정리 끝! 훌라훌라!')=='clean'
    assert event_for('훌라훌라!')=='dance'
    app.settings['voice_clips']={'greeting':str(clip)}
    with patch.object(app.voice,'play_clip') as play:
        app.voice.say('뭘 찾아줄까?',app.settings,force=True); play.assert_called_once()
    print('original clip trim / volume / reaction routing: PASS',flush=True)
    assert not errors,errors
finally:
    if app.bubble: app.bubble.close()
    if app.watcher: app.watcher.close()
    if app.hotkey: app.hotkey.close()
    app.index_cancel.set(); app.index_pool.shutdown(wait=True,cancel_futures=True)
    app.ai.close(); app.voice.stop(); app.pool.shutdown(wait=True); root.destroy()
    assert base in tmp.resolve().parents; shutil.rmtree(tmp)
