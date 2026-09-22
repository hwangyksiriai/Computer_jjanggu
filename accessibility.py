"""Readable fonts and bounded windows, without changing Windows display settings."""
import tkinter as tk
from tkinter import font as tkfont

def fit_window(window,width,height,min_width=560,min_height=400):
    from monitors import DesktopSpace
    window.update_idletasks()
    space=DesktopSpace(window);x,y=space.position();screens=space.workareas()
    screen=next((s for s in screens if s[0]<=x<s[2] and s[1]<=y<s[3]),screens[0])
    available_w=max(320,screen[2]-screen[0]-24);available_h=max(280,screen[3]-screen[1]-24)
    width=min(width,available_w);height=min(height,available_h)
    window.minsize(min(min_width,width),min(min_height,height))
    window.geometry(f'{int(width)}x{int(height)}')
    return width,height

def apply_fonts(widget,factor):
    """Keep the original font, so repeated refreshes never compound the scale."""
    if not widget.winfo_exists():return
    if 'font' in widget.keys():
        try:
            if not hasattr(widget,'_readable_font'):
                widget._readable_font=tkfont.Font(root=widget,font=widget.cget('font')).actual()
            original=widget._readable_font
            size=original['size']; scaled=round(abs(size)*factor)*(1 if size>0 else -1)
            widget.configure(font=(original['family'],scaled,original['weight'],original['slant']))
        except tk.TclError:pass
    for child in widget.winfo_children():
        if not isinstance(child,tk.Toplevel):apply_fonts(child,factor)

def scroll_page(parent,bg):
    """Keep all controls reachable on a short or enlarged display."""
    from tkinter import ttk
    frame=tk.Frame(parent,bg=bg);frame.pack(fill='both',expand=True)
    canvas=tk.Canvas(frame,bg=bg,highlightthickness=0)
    bar=ttk.Scrollbar(frame,command=canvas.yview);bar.pack(side='right',fill='y')
    canvas.configure(yscrollcommand=bar.set);canvas.pack(side='left',fill='both',expand=True)
    body=tk.Frame(canvas,bg=bg);item=canvas.create_window(0,0,anchor='nw',window=body)
    body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
    def inside(widget):
        return str(widget)==str(canvas) or str(widget).startswith(str(body)+'.')
    def wheel(event):
        if not inside(event.widget) or event.widget.winfo_class() in ('Text','Treeview','Listbox','TCombobox'):return
        if canvas.yview()==(0.0,1.0):return
        canvas.yview_scroll(-int(event.delta/120),'units');return 'break'
    def reveal(event):
        if not inside(event.widget):return
        region=canvas.bbox('all')
        if not region or region[3]<=canvas.winfo_height():return
        y=event.widget.winfo_rooty()-body.winfo_rooty()
        visible=canvas.canvasy(0);bottom=y+event.widget.winfo_height()
        if y<visible:canvas.yview_moveto(max(0,y-8)/region[3])
        elif bottom>visible+canvas.winfo_height():canvas.yview_moveto((bottom-canvas.winfo_height()+8)/region[3])
    top=parent.winfo_toplevel()
    wheel_id=top.bind('<MouseWheel>',wheel,add='+');focus_id=top.bind('<FocusIn>',reveal,add='+')
    def cleanup(event):
        if event.widget is not frame:return
        try:top.unbind('<MouseWheel>',wheel_id);top.unbind('<FocusIn>',focus_id)
        except tk.TclError:pass
    frame.bind('<Destroy>',cleanup,add='+')
    return body
