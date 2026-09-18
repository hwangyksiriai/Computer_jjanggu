"""End-to-end app callbacks with temporary files and real local AI, no user Desktop."""
import bootstrap
from pathlib import Path
import shutil
import time
import tkinter as tk
import uuid
from enhancements import EnhancedApp
from monitors import enable_dpi_awareness

base=(Path(__file__).parent/'.local/tests').resolve(); tmp=base/uuid.uuid4().hex; tmp.mkdir(parents=True)
enable_dpi_awareness(); root=tk.Tk(); failures=[]
root.report_callback_exception=lambda *args:failures.append(str(args[1]))
app=EnhancedApp(root,tmp/'state'); app.settings['sound']=False
source=tmp/'files'; source.mkdir()
(source/'unknown.txt').write_text('INVOICE\nAmount due: USD 450\nPayment due: 2026-10-01',encoding='utf-8')
(source/'notes.txt').write_text('주말 산책과 여행 계획',encoding='utf-8')
app.settings['source']=str(source); app.settings['vault']=str(tmp/'vault')
def wait_for_idle():
    deadline=time.monotonic()+150
    # Allow scheduled initialization to run before checking busy.
    for _ in range(8): root.update(); time.sleep(.1)
    while (app.busy or app.indexing) and time.monotonic()<deadline:
        root.update(); time.sleep(.1)
    assert not app.busy and not app.indexing,'App operation timed out'
    root.update(); assert not failures,failures
try:
    wait_for_idle(); print('startup + real indexing OK',flush=True)
    app.query.set('인보이스 찾아줘'); app.do_search(); wait_for_idle()
    assert app.result_cache and app.result_cache[0]['name']=='unknown.txt'
    print('async semantic search + rendered results OK',flush=True)
    app.selected={str(source/'unknown.txt')}; app.add_selected(); root.update()
    assert app.library.tray_files()==[str(source/'unknown.txt')]
    print('multi-selection to tray OK',flush=True)
    app.show('closet'); app.change('shirt','라벤더'); app.change('hat','노란 모자'); root.update()
    app.play_action(2,'춤 테스트',False)
    for _ in range(12): root.update(); time.sleep(.1)
    app.show('room'); root.update()
    assert not failures,failures
    print('costume + animated frames + room OK',flush=True)
finally:
    if app.hotkey: app.hotkey.close()
    app.index_cancel.set(); app.index_pool.shutdown(wait=True,cancel_futures=True)
    app.ai.close(); app.voice.stop(); app.pool.shutdown(wait=True); root.destroy()
    assert base in tmp.resolve().parents; shutil.rmtree(tmp)
