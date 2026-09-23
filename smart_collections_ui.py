"""Small saved-filter editor and cancellable, scoped result refreshes."""
import tkinter as tk
from tkinter import messagebox,ttk
import weakref

from accessibility import apply_fonts,fit_window,scroll_page
from collections_ui import BG,INK,MUTED,FONT,_label,_button
from smart_collections import (SmartCollectionStore,SmartQueryRunner,PERIODS,DOCUMENT_TYPES,
                               TEMPLATES,condition_summary)


def _store(app):
    store=getattr(app,'smart_collection_store',None)
    if store is None:store=app.smart_collection_store=SmartCollectionStore(app.data)
    return store


def show_smart_collections(app):
    view=getattr(app,'smart_collections_view',None)
    if view is not None and not view.closed:
        view.refresh();view.win.deiconify();view.win.lift()
    else:view=app.smart_collections_view=SmartCollectionsWindow(app)
    return view.win


def refresh_smart_collections(app):
    """Call on Tk after index/scope changes. Catalog queries run in workers."""
    view=getattr(app,'smart_collections_view',None)
    if view is not None and not view.closed:view.refresh()
    for source in list(getattr(app,'smart_collection_views',())):
        source.refresh()


class SmartResultSource:
    """UI-thread controller; its worker owns only plain data and queues."""
    def __init__(self,app,definition,browser):
        self.app=app;self.store=_store(app);self.ident=definition['id'];self.browser=weakref.ref(browser)
        self.runner=SmartQueryRunner(app.library.db);self.closed=False;self.timer=None
        self.scope=None;self.definition=None;self.limit=2000;self.loading=False;self.suspended=False
        self.generation=0;self.context_title='자동 모음 · '+definition['name']
        browser._smart_collection_source=self
        if not hasattr(app,'smart_collection_views'):app.smart_collection_views=weakref.WeakSet()
        app.smart_collection_views.add(self)
        self.more=_button(browser.coverage.master,'자동 모음 더 불러오기',self.show_more)
        self.more.configure(font=(FONT,10),padx=0,pady=4)
        browser.win.bind('<Destroy>',self._destroy,add='+')
        browser.win.bind('<FocusIn>',self._focus,add='+')
        self.refresh();self._schedule()

    def active(self):
        browser=self.browser()
        return (not self.closed and browser is not None and not browser.closed and
                browser.context_title==self.context_title and browser.search_query is None and not browser.pending)

    def _roots(self):return tuple(str(path) for path in self.app.document_roots())

    def refresh(self):
        if not self.active():
            if not self.closed:
                self.runner.cancelled.set();self.loading=False;self.suspended=True;self.more.pack_forget()
            return
        browser=self.browser();definition=self.store.get(self.ident);scope=self._roots()
        if definition is None:
            self.runner.cancelled.set();self.loading=False
            browser.update_results([],None);browser.coverage.configure(text='이 자동 모음은 지워졌어요. 원본 파일은 그대로예요.')
            self.more.pack_forget();return
        changed=self.scope!=scope or (self.definition is not None and self.definition['revision']!=definition['revision'])
        if changed:
            # Former roots must disappear before a slower replacement query completes.
            browser.update_results([],None);self.limit=2000
        self.scope=scope;self.definition=definition;self.suspended=False
        self.context_title='자동 모음 · '+definition['name'];browser.context_title=self.context_title
        browser.heading.configure(text=self.context_title);browser.win.title(self.context_title)
        self.more.pack_forget();self.loading=True
        browser.coverage.configure(text='조건에 맞는 파일을 모으고 있어요…')
        self.generation=self.runner.submit(definition,scope,self.limit)

    def _schedule(self):
        if not self.closed:self.timer=self.browser().win.after(80,self._poll)

    def _poll(self):
        self.timer=None
        if self.closed:return
        browser=self.browser()
        if browser is None or browser.closed:self.close();return
        if not self.active():
            self.runner.cancelled.set();self.loading=False;self.suspended=True;self.more.pack_forget()
            if not browser.pending and browser.context_title!=self.context_title:self.close();return
        else:
            if self.suspended:self.refresh()
            response=self.runner.drain()
            if response is not None:
                self.loading=False
                current=self.store.get(self.ident)
                if self._roots()!=self.scope or current is None or current['revision']!=self.definition['revision']:
                    self.refresh()
                else:
                    _,result,error=response
                    if error:browser.coverage.configure(text=error)
                    elif not result.cancelled:
                        browser.update_results(result.rows,None)
                        summary=condition_summary(self.definition)
                        if not self.scope:summary='찾을 폴더를 연결하면 여기에 자동으로 모여요.'
                        elif result.truncated:
                            summary=f'{len(result.rows):,}개까지 먼저 모았어요. '+summary
                            if self.limit>=20000:summary+=' · 조건을 좁히면 나머지 파일도 찾을 수 있어요.'
                        elif not result.rows:summary='지금 분석된 파일 중에는 아직 없어요. '+summary
                        if result.outdated:summary+=f' · 바뀐 파일 {result.outdated}개는 다시 분석한 뒤 확인해요.'
                        browser.coverage.configure(text=summary)
                        if result.truncated and self.limit<20000:self.more.pack(anchor='w')
        self._schedule()

    def show_more(self):
        if self.active():self.limit=min(20000,self.limit+2000);self.refresh()

    def _focus(self,event):
        browser=self.browser()
        if browser is not None and event.widget is browser.win and not self.loading:self.refresh()

    def _destroy(self,event):
        browser=self.browser()
        if browser is not None and event.widget is browser.win:self.close()

    def close(self):
        if self.closed:return
        self.closed=True;self.runner.close()
        browser=self.browser()
        if self.timer is not None and browser is not None:
            try:browser.win.after_cancel(self.timer)
            except tk.TclError:pass
        self.timer=None
        if self.app is not None:getattr(self.app,'smart_collection_views',set()).discard(self)
        self.app=None


class SmartCollectionsWindow:
    def __init__(self,app):
        self.app=app;self.store=_store(app);self.closed=False;self.rows=[];self.selected=None
        self.win=tk.Toplevel(app.root);self.win.title('자동 모음');self.win.configure(bg=BG)
        fit_window(self.win,650,650,400,370)
        self.win.bind('<Escape>',lambda event:self.win.destroy())
        self.win.bind('<Destroy>',self._destroy,add='+')
        page=scroll_page(self.win,BG);self.page=page
        header=tk.Frame(page,bg=BG,padx=20,pady=18);header.pack(fill='x')
        _label(header,'자동으로 모아 볼까요?',19).pack(fill='x')
        note=_label(header,'조건을 한 번 정하면 새로 찾은 파일도 함께 보여요.\n파일은 원래 자리에 그대로 있어요.',11,MUTED)
        note.configure(wraplength=540);note.pack(fill='x',pady=(8,15))
        _button(header,'+ 자동 모음 만들기',self.create,True).pack(anchor='w')
        templates=tk.Frame(page,bg=BG,padx=20);templates.pack(fill='x')
        _label(templates,'처음이라면 이렇게 시작해요',11,MUTED).pack(fill='x',pady=(0,6))
        self.template=tk.StringVar(master=self.win,value=TEMPLATES[0]['name'])
        chooser=ttk.Combobox(templates,textvariable=self.template,values=[row['name'] for row in TEMPLATES],state='readonly',font=(FONT,11))
        chooser.pack(fill='x')
        _button(templates,'이 조건으로 만들기',self.from_template).pack(anchor='w',pady=(4,10))
        self.saved=tk.Frame(page,bg=BG,padx=20,pady=10);self.saved.pack(fill='both',expand=True)
        page.bind('<Configure>',self._fit,add='+')
        self.refresh()

    def _fit(self,event=None):
        width=max(200,self.page.winfo_width()-50)
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child,(tk.Label,tk.Button)):child.configure(wraplength=width)
                walk(child)
        walk(self.page)

    def refresh(self):
        if self.closed:return
        self.rows=self.store.list()
        for child in self.saved.winfo_children():child.destroy()
        _label(self.saved,'내 자동 모음',14).pack(fill='x',pady=(0,8))
        if not self.rows:_label(self.saved,'아직 없어요. 위에서 하나 만들어 보세요.',11,MUTED).pack(fill='x',pady=15)
        for row in self.rows:
            box=tk.Frame(self.saved,bg=BG,pady=8);box.pack(fill='x')
            button=_button(box,row['name']+'  →',lambda ident=row['id']:self.open(ident))
            button.configure(anchor='w',justify='left');button.pack(fill='x')
            note=_label(box,condition_summary(row),10,MUTED);note.pack(fill='x',padx=12)
            tools=tk.Frame(box,bg=BG);tools.pack(fill='x',padx=8)
            _button(tools,'조건 바꾸기',lambda ident=row['id']:self.edit(ident)).pack(side='left')
            remove=_button(tools,'지우기',lambda ident=row['id']:self.delete(ident))
            remove.configure(fg=MUTED);remove.pack(side='right')
        apply_fonts(self.win,getattr(self.app,'settings',{}).get('text_scale',1.0));self._fit()

    def create(self):return SmartCollectionEditor(self.app,self.win,on_saved=self._saved)

    def from_template(self):
        row=next(dict(row) for row in TEMPLATES if row['name']==self.template.get())
        return SmartCollectionEditor(self.app,self.win,initial=row,on_saved=self._saved)

    def edit(self,ident):
        row=self.store.get(ident)
        if row:return SmartCollectionEditor(self.app,self.win,initial=row,on_saved=self._saved)

    def _saved(self,ident):
        refresh_smart_collections(self.app);self.open(ident)

    def open(self,ident):
        definition=self.store.get(ident)
        if definition is None:self.refresh();return
        for source in list(getattr(self.app,'smart_collection_views',())):
            if source.ident==ident and source.active():
                source.refresh();source.browser().win.deiconify();source.browser().win.lift();return
        from result_browser import ResultBrowser
        browser=ResultBrowser(self.app,[],context_title='자동 모음 · '+definition['name'])
        SmartResultSource(self.app,definition,browser)
        return browser

    def delete(self,ident):
        definition=self.store.get(ident)
        if definition and messagebox.askyesno('자동 모음 지우기',f'“{definition["name"]}” 조건을 지울까요?\n원본 파일은 그대로 남아요.',parent=self.win):
            self.store.delete(ident);refresh_smart_collections(self.app)

    def _destroy(self,event):
        if event.widget is self.win:self.closed=True;self.template=None


class SmartCollectionEditor:
    def __init__(self,app,parent,initial=None,on_saved=lambda ident:None):
        self.app=app;self.store=_store(app);self.initial=initial or {};self.on_saved=on_saved;self.closed=False
        self.win=tk.Toplevel(parent);self.win.title('자동 모음 조건');self.win.configure(bg=BG)
        fit_window(self.win,600,690,400,380)
        self.win.bind('<Escape>',lambda event:self.win.destroy());self.win.bind('<Destroy>',self._destroy,add='+')
        bottom=tk.Frame(self.win,bg=BG,padx=20,pady=10);bottom.pack(side='bottom',fill='x')
        self.feedback=_label(bottom,'',10,MUTED);self.feedback.configure(wraplength=520);self.feedback.pack(fill='x')
        self.win.bind('<Configure>',self._fit_feedback,add='+')
        _button(bottom,'저장하고 보기',self.submit,True).pack(fill='x',pady=(6,0))
        page=scroll_page(self.win,BG);body=tk.Frame(page,bg=BG,padx=20,pady=12);body.pack(fill='both',expand=True)
        self.values={};self.imes={}
        from ime_entry import attach
        for key,label in [('name','모음 이름'),('query','찾을 말 · 비워도 돼요')]:
            _label(body,label,12).pack(fill='x',pady=(12,5))
            variable=self.values[key]=tk.StringVar(master=self.win,value=self.initial.get(key,''))
            entry=tk.Entry(body,textvariable=variable,font=(FONT,12),relief='solid',bd=1)
            entry.pack(fill='x',ipady=7);self.imes[entry]=attach(entry)
        _label(body,'문서 종류',12).pack(fill='x',pady=(14,5))
        self.values['document_type']=tk.StringVar(master=self.win,value=self.initial.get('document_type') or '모든 종류')
        ttk.Combobox(body,textvariable=self.values['document_type'],values=('모든 종류',)+DOCUMENT_TYPES[1:],state='readonly',font=(FONT,12)).pack(fill='x')
        _label(body,'파일 수정일',12).pack(fill='x',pady=(14,5))
        self.values['modified_period']=tk.StringVar(master=self.win,value=PERIODS[self.initial.get('modified_period','any')])
        period=ttk.Combobox(body,textvariable=self.values['modified_period'],values=list(PERIODS.values()),state='readonly',font=(FONT,12))
        period.pack(fill='x');period.bind('<<ComboboxSelected>>',self._dates)
        self.dates=tk.Frame(body,bg=BG)
        for key,label in [('modified_from','시작일 (2026-09-01)'),('modified_to','마지막 날 (2026-09-30)')]:
            _label(self.dates,label,11).pack(fill='x',pady=(10,4))
            self.values[key]=tk.StringVar(master=self.win,value=self.initial.get(key,''))
            tk.Entry(self.dates,textvariable=self.values[key],font=(FONT,12)).pack(fill='x',ipady=5)
        self.notice=_label(body,'기간은 파일 수정일 기준이에요.\n문서 발행일이나 급여 지급월과 다를 수 있어요.\n찾을 말은 파일 이름과 분석된 내용에서 모두 찾아요.',10,MUTED)
        self.notice.configure(wraplength=500);self.notice.pack(fill='x',pady=(15,12))
        self._dates();apply_fonts(self.win,getattr(app,'settings',{}).get('text_scale',1.0))
        body.bind('<Configure>',lambda event:self.notice.configure(wraplength=max(200,event.width-40)))

    def _dates(self,event=None):
        if self.values['modified_period'].get()==PERIODS['custom']:self.dates.pack(fill='x',before=self.notice)
        else:self.dates.pack_forget()

    def _fit_feedback(self,event):
        if event.widget is self.win:self.feedback.configure(wraplength=max(200,event.width-40))

    def submit(self):
        if self.closed:return
        ime=self.imes.get(self.win.focus_get())
        return ime.commit_then(self.save) if ime else self.save()

    def save(self):
        if self.closed:return
        values={key:value.get() for key,value in self.values.items()}
        values['document_type']='' if values['document_type']=='모든 종류' else values['document_type']
        values['modified_period']=next(key for key,label in PERIODS.items() if label==values['modified_period'])
        try:
            ident=self.initial.get('id')
            if ident:self.store.update(ident,**values)
            else:ident=self.store.create(**values)
        except ValueError as error:self.feedback.configure(text=str(error));return
        self.win.destroy();self.on_saved(ident)

    def _destroy(self,event):
        if event.widget is self.win:self.closed=True;self.values.clear();self.imes.clear()
