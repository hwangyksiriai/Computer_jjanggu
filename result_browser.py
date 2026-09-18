"""Resizable results list with a persistent side preview."""
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from PIL import Image, ImageTk
from result_refinement import ResultRefinement


class ResultBrowser:
    def __init__(self,app,rows):
        self.app=app; self.rows=list(rows); self.visible=[]; self.photo=None
        self.win=tk.Toplevel(app.root); self.win.title('찾은 파일 · 목록과 미리보기')
        self.win.geometry('1050x720'); self.win.minsize(720,460)
        top=ttk.Frame(self.win,padding=12); top.pack(fill='x')
        self.query=tk.StringVar(); self.mode=tk.StringVar(value='전체 결과')
        ttk.Label(top,text='결과 안에서 찾기').pack(side='left')
        entry=ttk.Entry(top,textvariable=self.query,width=25); entry.pack(side='left',padx=8)
        entry.bind('<Return>',lambda e:self.refresh())
        combo=ttk.Combobox(top,textvariable=self.mode,state='readonly',width=14,values=['전체 결과','일치하는 파일','관련 후보','PDF만'])
        combo.pack(side='left'); combo.bind('<<ComboboxSelected>>',lambda e:self.refresh())
        ttk.Button(top,text='적용',command=self.refresh).pack(side='left',padx=5)
        ttk.Button(top,text='초기화',command=self.reset).pack(side='left')
        self.count=ttk.Label(self.win,padding=(12,0)); self.count.pack(anchor='w')
        pane=ttk.Panedwindow(self.win,orient='horizontal'); pane.pack(fill='both',expand=True,padx=12,pady=12)
        left=ttk.Frame(pane); right=ttk.Frame(pane,padding=10); pane.add(left,weight=3); pane.add(right,weight=2)
        self.tree=ttk.Treeview(left,columns=('kind','amount'),selectmode='browse')
        self.tree.heading('#0',text='파일명'); self.tree.column('#0',width=250,minwidth=150)
        for key,title,width in [('kind','분류',105),('amount','금액',100)]:
            self.tree.heading(key,text=title); self.tree.column(key,width=width,minwidth=65)
        bar=ttk.Scrollbar(left,command=self.tree.yview); bar.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=bar.set); self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.preview())
        self.tree.bind('<Double-1>',lambda e:self.open())
        self.title=ttk.Label(right,text='파일을 고르면 여기에 표시돼요',wraplength=340); self.title.pack(fill='x')
        self.image=ttk.Label(right); self.image.pack(pady=5)
        self.body=tk.Text(right,wrap='word',font=('맑은 고딕',10),relief='flat',height=10)
        bodybar=ttk.Scrollbar(right,command=self.body.yview); bodybar.pack(side='right',fill='y')
        self.body.configure(yscrollcommand=bodybar.set); self.body.pack(fill='both',expand=True)
        actions=ttk.Frame(self.win,padding=10); actions.pack(fill='x')
        ttk.Label(actions,text='한 번 클릭: 옆에서 보기 · 두 번 클릭: 원본 열기').pack(side='left')
        ttk.Button(actions,text='원본 열기',command=self.open).pack(side='right')
        ttk.Button(actions,text='전체 미리보기',command=lambda:self.open(True)).pack(side='right',padx=6)
        self.refresh()

    def reset(self):
        self.query.set(''); self.mode.set('전체 결과'); self.refresh()

    def refresh(self):
        model=ResultRefinement(self.rows); model.facet('text',self.query.get())
        mode=self.mode.get()
        if mode=='PDF만': model.facet('pdf')
        elif mode!='전체 결과': model.select(mode,lambda r:r.get('group')==mode)
        self.visible=model.rows
        children=self.tree.get_children()
        if children:self.tree.delete(*children)
        for i,row in enumerate(self.visible):
            self.tree.insert('', 'end',iid=str(i),text=row['name'],values=(row.get('group',''),(row.get('fields') or {}).get('amount','—')))
        matched=sum(r.get('group')=='일치하는 파일' for r in self.rows)
        self.count.configure(text=f'검색 결과 {len(self.rows)}개 · 일치 {matched} / 관련 후보 {len(self.rows)-matched} · 현재 표시 {len(self.visible)}개')
        if self.visible:self.tree.selection_set('0')
        else:self.preview()

    def selected(self):
        selection=self.tree.selection()
        return self.visible[int(selection[0])] if selection else None

    def preview(self):
        row=self.selected(); self.image.configure(image=''); self.photo=None
        self.body.configure(state='normal'); self.body.delete('1.0','end')
        if row:
            self.title.configure(text=row['name'])
            thumb=row.get('thumbnail')
            if thumb and Path(thumb).is_file():
                try:
                    with Image.open(thumb) as im:
                        im.thumbnail((320,240)); self.photo=ImageTk.PhotoImage(im.copy())
                    self.image.configure(image=self.photo)
                except OSError: pass
            self.body.insert('1.0',row.get('reason','')+'\n\n위치: '+row['path']+'\n\n'+(row.get('body','')[:16000] or '추출된 본문이 없어요. 원본을 열어 확인해 주세요.'))
        else:self.title.configure(text='조건에 맞는 파일이 없어요. 초기화를 눌러 보세요.')
        self.body.configure(state='disabled')

    def open(self,preview=False):
        row=self.selected()
        if row:
            if preview:self.app.preview(row)
            else:self.app.open_file(row['path'])
