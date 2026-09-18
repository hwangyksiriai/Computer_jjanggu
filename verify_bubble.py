"""Hidden-main-window bubble flow, using only isolated synthetic documents."""
import bootstrap
import tkinter as tk
import time,uuid,shutil
from pathlib import Path
from unittest.mock import patch
from enhancements import EnhancedApp
from monitors import enable_dpi_awareness

base=(Path(__file__).parent/'.local/tests').resolve(); tmp=base/uuid.uuid4().hex; tmp.mkdir(parents=True)
enable_dpi_awareness(); root=tk.Tk(); errors=[]
root.report_callback_exception=lambda *args:errors.append(str(args[1]))
app=EnhancedApp(root,tmp); app.settings['sound']=False; app.ai.ready=lambda:False
def idle():
    deadline=time.monotonic()+40
    for _ in range(5): root.update(); time.sleep(.1)
    while (app.busy or app.indexing) and time.monotonic()<deadline: root.update(); time.sleep(.05)
    assert not app.busy and not app.indexing
    assert not errors,errors
try:
    idle(); app.hide(); root.update()
    app.pet.moved=False; app.pet.release(None); root.update(); b=app.bubble
    assert b.alive() and root.state()=='withdrawn'
    search=app.library.smart_search
    def delayed(*args,**kwargs): time.sleep(1.2); return search(*args,**kwargs)
    with patch.object(app.library,'smart_search',delayed):
        b.submit('인보이스 찾아줘')
        for _ in range(5): root.update(); time.sleep(.08)
        assert app.search_fx['active'] and b.pending
        assert b.text.get()==''
        assert any('보낸 요청' in str(w.cget('text')) for w in b.content.winfo_children() if isinstance(w,tk.Label))
        assert b.send_button.cget('state')=='disabled'
        assert app.pet.canvas.find_withtag('search-fx')
        assert app.pet.canvas.find_withtag('held-magnifier')
        assert '초' in b.message.get()
        idle()
    assert not app.search_fx['active'] and app.search_fx['outcome']=='found'
    assert b.send_button.cget('state')=='normal'
    print('persistent searching animation / elapsed time / completion: PASS',flush=True)
    assert len(b.rows)>=2 and root.state()=='withdrawn'
    print('pet click -> bubble -> local results, main stays hidden: PASS',flush=True)
    opened=[]; app.open_file=lambda p:opened.append(p)
    first=b.result_buttons[0]; first.invoke()
    assert opened and Path(opened[0]).exists()
    original_paths={r['path'] for r in b.rows}
    b.refine('currency','USD'); root.update()
    assert b.rows and all('USD' in r['fields']['currencies'] for r in b.rows)
    b.refine('text','no-such-company-xyz'); root.update(); assert not b.rows
    b.refine('back'); root.update(); assert b.rows
    b.refine('reset'); root.update(); assert {r['path'] for r in b.rows}==original_paths
    assert b.within_entry.winfo_ismapped() and b.result_buttons[0].winfo_ismapped()
    assert b.content.winfo_height()>250
    print('result facets / text narrowing / empty undo / reset / visible controls: PASS',flush=True)
    from result_browser import ResultBrowser
    browser=ResultBrowser(app,b.rows); root.update()
    assert len(browser.tree.get_children())==len(b.rows)
    browser.tree.selection_set('0'); root.update(); assert browser.selected()
    assert browser.body.get('1.0','end').strip()
    browser.query.set('no-such-file-zz'); browser.refresh(); root.update(); assert not browser.visible
    browser.reset(); root.update(); assert browser.visible
    browser.win.destroy()
    print('wide results / selection preview / filter / reset: PASS',flush=True)
    b.submit('그중 달러로 된 것만'); idle()
    assert b.rows and all('USD' in r['body'] for r in b.rows)
    b.submit('춤춰줘'); idle(); assert app.pet.action==2 and root.state()=='withdrawn'
    print('open file / follow-up / dance without main window: PASS',flush=True)
    for screen in app.pet.space.screens():
        app.pet.space.move(screen[0]+40,screen[1]+50)
        for _ in range(5): root.update(); time.sleep(.1)
        x,y=b.space.position()
        assert screen[0]<=x and x+b.WIDTH<=screen[2] and screen[1]<=y and y+b.HEIGHT<=screen[3],(screen,x,y)
        _,pet_y=app.pet.space.position()
        assert y<pet_y and y+b.HEIGHT<=pet_y+56,(y,b.HEIGHT,pet_y)
    print('bubble kept inside both monitors: PASS',flush=True)
    with patch.object(app.library,'smart_search',return_value=([],'none')):
        b.submit('없는 파일 찾아줘'); idle(); assert app.search_fx['outcome']=='empty'
    with patch.object(app.library,'smart_search',side_effect=RuntimeError('test error')),patch('app.messagebox.showerror'):
        b.submit('오류 파일 찾아줘'); idle(); assert not app.search_fx['active']
        assert b.send_button.cget('state')=='normal'
    print('empty results / failure stop animation and restore controls: PASS',flush=True)
    with patch.object(app.voice,'say') as voice,patch.object(app,'plan_clean'):
        app.settings['focus_manual']=True
        for query,phrase in [('꾸며줘','꾸미기'),('춤춰줘','훌라'),('정리해줘','확인'),('작업 트레이','트레이'),('우리 방','우리 방'),('앱 목록','앱 목록'),('안녕','말은 잘 들었어')]:
            app.settings['sound']=True; voice.reset_mock()
            b.submit(query); idle()
            assert phrase in app.last_reply,(query,app.last_reply)
            assert voice.call_count>=2,(query,voice.call_count)
            assert not app.search_fx['active']
        voice.reset_mock(); b.submit('조용히 해줘'); idle()
        assert not app.settings['sound'] and voice.call_count==0
    print('all supported requests: acknowledgement + completion, quiet stays silent: PASS',flush=True)
    b.close(); b.reply('late reply'); b.results([])
    app.pet.moved=True; app.pet.release(None); assert not b.alive()
    app.pet.moved=False; app.pet.release(None); root.update(); assert app.bubble.alive()
    app.pet.release(None); assert not app.bubble.alive()
    assert not errors,errors
    print('drag vs click / close / late callback safety: PASS',flush=True)
finally:
    if app.bubble: app.bubble.close()
    if app.watcher: app.watcher.close()
    if app.hotkey: app.hotkey.close()
    app.index_cancel.set(); app.index_pool.shutdown(wait=True,cancel_futures=True)
    app.ai.close(); app.voice.stop(); app.pool.shutdown(wait=True); root.destroy()
    assert base in tmp.resolve().parents; shutil.rmtree(tmp)
