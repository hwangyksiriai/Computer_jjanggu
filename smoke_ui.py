"""Programmatic widget/layout smoke test; never touches the user's Desktop."""
from pathlib import Path
import uuid
import shutil
import time
import tkinter as tk
from app import App

test_base=(Path(__file__).parent/'.local'/'tests').resolve()
tmp=test_base/uuid.uuid4().hex
tmp.mkdir(parents=True)
try:
    root=tk.Tk(); app=App(root,tmp); app.settings['sound']=False
    for page in ('home','organize','closet','history'):
        app.show(page); root.update()
        assert app.content.winfo_width()>600, page
        if page=='closet':
            for i in range(4):
                app.change('outfit',i); app.change('accessory','별 핀'); root.update()
            app.change('size',220); root.update()
        print(page,'OK',flush=True)
    app.library.index([app.demo]); app.show('home'); app.query.set('인보이스 찾아줘'); app.do_search()
    assert app.last_count==2
    app.query.set('그중 달러로 된 것만'); app.do_search(); assert app.last_count==1
    print('search UI OK',flush=True)
    app.voice.stop(); app.pool.shutdown(wait=True); root.destroy()
finally:
    assert test_base in tmp.resolve().parents
    shutil.rmtree(tmp)
