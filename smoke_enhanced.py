import bootstrap
from pathlib import Path
import shutil
import uuid
import tkinter as tk
from enhancements import EnhancedApp
from monitors import enable_dpi_awareness

base=(Path(__file__).parent/'.local/tests').resolve(); tmp=base/uuid.uuid4().hex; tmp.mkdir(parents=True)
enable_dpi_awareness(); root=tk.Tk(); app=EnhancedApp(root,tmp)
app.settings['sound']=False; app.ai.ready=lambda:False
try:
    for page in ('home','organize','closet','history','tray','room','collection','tools'):
        app.show(page); root.update_idletasks(); print(page,'OK',flush=True)
    assert app.dnd_available,'DnD must initialize'
    for index,target in ((0,'room'),(1,'home'),(2,'tray'),(3,'closet')):
        app.show('room'); root.update()
        x1,y1,x2,y2,_=app.room_regions[index]
        prior=app.settings.get('focus_manual',False)
        app.room_canvas.event_generate('<Button-1>',x=int((x1+x2)/2),y=int((y1+y2-24)/2))
        root.update()
        assert app.page==target,(index,app.page)
        if index==0: assert app.settings['focus_manual']!=prior
    print('room furniture center clicks OK',flush=True)
    app.show('closet'); app.change('shirt','민트'); app.change('hat','노란 모자'); root.update_idletasks()
    app.play_action(2,'test',False); root.update_idletasks()
    print('customization / DnD / animation OK',flush=True)
finally:
    if app.hotkey: app.hotkey.close()
    app.index_cancel.set(); app.index_pool.shutdown(wait=True,cancel_futures=True)
    app.ai.close(); app.voice.stop(); app.pool.shutdown(wait=True); root.destroy()
    assert base in tmp.resolve().parents; shutil.rmtree(tmp)
