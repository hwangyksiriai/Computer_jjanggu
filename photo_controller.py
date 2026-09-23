"""Photo discovery lifecycle, independent of desktop file organization."""
import threading
import time
from pathlib import Path
from contextlib import ExitStack
from tkinter import filedialog
from app_paths import BASE,runtime_root
from local_ai import LocalAI
from photo_library import PhotoLibrary,photo_roots
from photo_query import is_photo_followup,resolve_photo_query
from visual_query import visual_intent


class PhotoController:
    def initialize_photo_search(self):
        self.photo_ai=LocalAI()
        self.photo_library=PhotoLibrary(self.data/'photos',self.photo_ai,exclude=(BASE,self.data,runtime_root()))
        self.photo_cancel=threading.Event()
        self.photo_ai.cancelled=self.photo_cancel.is_set
        self.photo_indexing=False;self.photo_searching=False;self.photo_epoch=0
        self.photo_history=[];self.photo_resolved='';self.photo_similar=None
        self.photo_last_refresh=0;self.photo_gallery=None;self.saved_photo_gallery=None
        self.photo_scan_roots=[];self.photo_pending_refresh=False
        self.photo_reused=False
        self.photo_deleting=set();self.photo_removed=set();self.photo_removal_generation=0

    def photo_search_roots(self):
        return photo_roots(self.settings.get('source'),self.vault,self.settings.get('photo_roots',()))

    def photo_summary(self):
        coverage=self.photo_library.search_coverage
        total=coverage.get('images',0);analyzed=coverage.get('detected',0)
        state=coverage.get('photo_scan',{});phase=state.get('phase')
        count=len(self.result_cache)
        text=f'현재 찾은 사진 {count}장 · 등록 {total}장 / 사물 분석 {analyzed}장'
        if self.photo_indexing:text+='\n사진을 더 확인 중이에요. 준비되는 대로 결과가 늘어날 수 있어요.'
        elif phase=='paused':text+='\n분석을 멈췄어요. 지금까지 준비된 사진은 볼 수 있어요.'
        elif not self.photo_ai.ready_for('photo'):text+='\n사진 모습으로 찾으려면 “분석 메뉴 → 사진 찾기 준비”를 눌러 주세요.'
        elif phase=='error':text+='\n사진 분석이 중단됐어요. “분석 이어하기”로 다시 시도할 수 있어요.'
        elif count==0:text+='\n원하는 사진이 없으면 기억나는 조건을 줄이거나 찾을 폴더를 추가해 보세요.'
        if coverage.get('unreadable'):text+=f"\n읽지 못한 사진 {coverage['unreadable']}장은 모습 검색에서 빠져 있어요."
        if coverage.get('offline'):text+=f"\n클라우드에만 있는 사진 {coverage['offline']}장은 먼저 PC에 내려받아 주세요."
        notice=getattr(self.photo_library,'search_notice','')
        if notice:text+='\n'+notice
        return text

    def start_photo_scan(self,force=False):
        if self.photo_indexing:return
        roots=self.photo_search_roots()
        if not force and roots==self.photo_scan_roots and time.monotonic()-self.photo_library._last_scan<60:return
        self.photo_scan_roots=roots;self.photo_cancel.clear();self.photo_indexing=True
        def progress(state):
            self.events.put(lambda s=state:self.photo_progress(s))
        def work():
            try:self.photo_library.scan(roots,progress,self.photo_cancel.is_set,lambda:self.photo_searching,reuse_from=self.library)
            except Exception as error:
                self.photo_library.ai_error=str(error)
                self.photo_library.scan_state.update(phase='error')
            finally:self.events.put(self.photo_scan_finished)
        threading.Thread(target=work,name='photo-discovery',daemon=True).start()

    def photo_progress(self,state):
        if not getattr(self,'is_photo_search',False):return
        # Coverage counters are read on the worker during a search; progress uses
        # cached counters and therefore does not scan the filesystem on Tk.
        coverage=dict(getattr(self.photo_library,'search_coverage',{}))
        coverage['photo_scan']=state;coverage['images']=state.get('registered',coverage.get('images',0))
        self.photo_library.search_coverage=coverage
        if self.photo_gallery and self.photo_gallery.win.winfo_exists():self.photo_gallery.update_coverage(coverage)
        if time.monotonic()-self.photo_last_refresh>5:
            self.photo_pending_refresh=True;self.refresh_photo_results()

    def photo_scan_finished(self):
        self.photo_indexing=False
        if not self.photo_cancel.is_set():self.photo_pending_refresh=True
        self.refresh_photo_results()

    def refresh_photo_results(self):
        if self.restarting or not getattr(self,'is_photo_search',False):return
        if self.busy:
            if self.photo_pending_refresh:self.root.after(700,self.refresh_photo_results)
            return
        if self.photo_pending_refresh and self.query.get().strip()==self.last_submitted:
            self.photo_pending_refresh=False;self.do_search(speak=False,refresh=True)

    def handle_photo_search(self,query,speak=True,reply_surface=None,refresh=False,similar=None,restored=False):
        if self.busy:
            self.status.set('찾고 있어요. 분석 메뉴에서 멈춘 뒤 조건을 바꿀 수 있어요.');return
        if not refresh:
            if self.photo_gallery and self.photo_gallery.win.winfo_exists():
                self.photo_gallery.saved_only.set(False)
            previous=self.photo_resolved if getattr(self,'is_photo_search',False) else ''
            following=bool(previous and is_photo_followup(query,previous))
            resolved=resolve_photo_query(query,previous) if not restored else query
            if not restored:
                if following and resolved!=previous:self.photo_history.append(previous)
                else:self.photo_history=[]
            if self.photo_gallery and self.photo_gallery.win.winfo_exists() and resolved!=self.photo_resolved:
                self.photo_gallery.query.set('');self.photo_gallery.mode.set('모든 사진')
                self.photo_gallery_reset_filters=True
            self.photo_resolved=resolved;self.photo_similar=similar;self.search_offset=0
            plans=getattr(self.photo_library,'_photo_query_plans',{})
            if resolved in plans and plans[resolved][-1]:plans.pop(resolved)
            self.photo_epoch+=1;self.photo_cancel.clear()
        epoch=self.photo_epoch
        removal_generation=self.photo_removal_generation
        self.is_photo_search=True;self.active_library=self.photo_library
        self.photo_searching=True;self.last_submitted=query
        if self.photo_gallery and self.photo_gallery.win.winfo_exists():
            self.photo_gallery.set_searching(True)
        roots=self.photo_search_roots();resolved=self.photo_resolved
        self.photo_last_refresh=time.monotonic()
        self.search_fx={'active':not refresh,'interactive':bool(reply_surface or speak),'started':time.monotonic(),
                        'phase':'사진 속 모습과 색을 확인하고 있어','activity':'search','outcome':'','until':0}
        if reply_surface is not None:reply_surface.start_search()
        if speak:self.pet_event('사진들을 펼쳐서 찾아볼게!',2,speak=True,explicit=True)
        # The first result uses whatever is already indexed. Discovery runs
        # separately and updates the same gallery without blocking the input.
        if not refresh:self.start_photo_scan()
        def work():
            try:
                if epoch!=self.photo_epoch or self.photo_cancel.is_set():return [],resolved,None
                if not self.photo_reused:
                    self.photo_library.reuse_analysis(self.library,roots);self.photo_reused=True
                rows,context=self.photo_library.smart_search(resolved,roots,similar=self.photo_similar,allow_model=refresh)
                coverage=self.photo_library.coverage(roots)
                self.photo_library.search_coverage.update(coverage)
                return rows,context,None
            except Exception as error:return [],resolved,str(error)
        def finish(result):
            self.photo_searching=False
            if self.photo_gallery and self.photo_gallery.win.winfo_exists():
                self.photo_gallery.set_searching(False)
            if epoch!=self.photo_epoch:return
            rows,context,error=result
            if not refresh and removal_generation==self.photo_removal_generation:
                # A new search may discover a photo restored from the recycle
                # bin. Older in-flight results cannot clear deletion markers.
                returned={row['path'] for row in rows}
                self.photo_removed.difference_update(returned)
                if self.photo_gallery:
                    self.photo_gallery._removed_paths.difference_update(returned)
            rows=[row for row in rows if row['path'] not in self.photo_removed]
            if error:self.photo_library.ai_error=error;self.photo_library.search_notice='검색이 중단됐어요. 분석 이어하기를 눌러 다시 시도해 주세요.'
            self.result_cache=rows;self.query_context=context
            self.last_reply=self.photo_summary();self.status.set(self.last_reply)
            self.save_search_state('photos',self.photo_library.search_coverage,len(rows),error)
            self.search_fx.update(active=False,outcome='found' if rows else 'empty',until=time.monotonic()+2)
            if reply_surface is not None and reply_surface.alive():reply_surface.results(rows)
            elif self.bubble and self.bubble.alive() and self.bubble.rows is not None:self.bubble.results(rows)
            if not refresh:self.open_photo_gallery()
            elif self.photo_gallery and self.photo_gallery.win.winfo_exists():self.photo_gallery.update_results(rows,query)
            if self.page=='home':self.render_results();self.reply_label.configure(text=self.last_reply)
            if not refresh and not visual_intent(resolved)['broad']:
                self.photo_pending_refresh=True;self.root.after(200,self.refresh_photo_results)
        self.run_job(work,finish,'사진을 확인하고 있어요…')

    def open_photo_gallery(self):
        from photo_gallery import PhotoGallery
        if self.photo_gallery and self.photo_gallery.win.winfo_exists():
            self.photo_gallery.search_query=self.last_submitted
            self.photo_gallery.update_results(self.result_cache,self.last_submitted)
            self.photo_gallery.win.deiconify();self.photo_gallery.win.lift()
        else:self.photo_gallery=PhotoGallery(self,self.result_cache,self.last_submitted)
        if getattr(self,'photo_gallery_reset_filters',False):
            self.photo_gallery.refresh();self.photo_gallery_reset_filters=False
        self.photo_gallery.request.configure(text='현재 조건: '+self.photo_resolved)

    def open_saved_photos(self):
        """Open saved references without changing any active search session."""
        from photo_gallery import PhotoGallery
        gallery=getattr(self,'saved_photo_gallery',None)
        if gallery and not gallery._closed and gallery.win.winfo_exists():
            gallery.reload_saved();gallery.show_saved(True)
            gallery.win.deiconify();gallery.win.lift()
        else:
            gallery=self.saved_photo_gallery=PhotoGallery(self,[],query='',saved_context=True)
        return gallery

    def open_result_browser(self):
        if getattr(self,'is_photo_search',False):return self.open_photo_gallery()
        return super().open_result_browser()

    def refine_photo(self,text):
        self.query.set(text);self.do_search()

    def undo_photo_query(self):
        if not self.photo_history:return self.status.set('처음 찾은 조건이에요.')
        if self.busy:return self.status.set('현재 찾기가 끝나면 한 단계 뒤로 갈 수 있어요.')
        query=self.photo_history.pop();self.query.set(query);self.handle_photo_search(query,restored=True)

    def find_similar_photo(self,row):
        self.query.set('이 사진과 비슷한 사진');self.handle_photo_search(self.query.get(),similar=row['path'])

    def choose_photo_folder(self):
        path=filedialog.askdirectory(parent=self.root,title='사진을 더 찾아볼 폴더 (정리할 폴더는 바뀌지 않아요)')
        if not path:return
        roots=list(self.settings.get('photo_roots',[]))
        if path not in roots:roots.append(path)
        self.settings['photo_roots']=roots;self.save()
        if self.photo_indexing:
            self.cancel_photo_search();self.root.after(300,self.resume_photo_analysis)
        else:self.resume_photo_analysis()

    def cancel_photo_search(self):
        self.photo_cancel.set();self.photo_epoch+=1;self.photo_ai.interrupt()
        self.photo_library.scan_state.update(phase='paused')
        self.photo_pending_refresh=False
        self.search_fx.update(active=False,outcome='',until=0)
        self.status.set('사진 찾기와 분석을 멈췄어요. 지금까지 찾은 사진은 그대로 볼 수 있어요.')
        if self.bubble and self.bubble.alive():self.bubble.reply('찾기를 멈췄어. 조건을 바꿔서 다시 말해 줘!')
        if self.photo_gallery and self.photo_gallery.win.winfo_exists():
            coverage=dict(self.photo_library.search_coverage);coverage['photo_scan']={'phase':'paused'}
            self.photo_gallery.update_coverage(coverage)

    def resume_photo_analysis(self):
        if self.photo_indexing or self.photo_searching:
            if self.photo_cancel.is_set():self.root.after(300,self.resume_photo_analysis)
            return
        self.photo_cancel.clear();self.start_photo_scan(force=True)
        if not getattr(self,'is_photo_search',False):self.quick('사진 보여줘')
        elif not self.busy:self.do_search(speak=False,refresh=True)

    def setup_photo_ai(self):
        self.setup_ai(capability='photo')

    def recycle_photo(self,row,callback):
        """Recycle only a confirmed photo; dispatch all UI changes through Tk."""
        path=row['path']
        if path in self.photo_deleting:
            callback(dict(ok=False,cancelled=True,error='이미 휴지통으로 보내는 중이에요.'))
            return
        self.photo_deleting.add(path)
        self.status.set('선택한 사진을 휴지통으로 보내고 있어요…')
        def work():
            try:
                from photo_recycle import recycle_photo
                # Serialize against organizer moves and index writes without
                # making the Tk input loop wait for disk operations.
                with ExitStack() as stack:
                    for library in sorted((self.library,self.photo_library),key=lambda item:str(item.db)):
                        stack.enter_context(library.mutation_lock)
                    result=recycle_photo(dict(row))
                    if result.get('ok'):
                        warnings=[]
                        # Keep saved references consistent even if all gallery
                        # windows close before this worker finishes.
                        memory=getattr(self,'file_memory',None)
                        if memory is not None:
                            try:memory.unsave_photo(path)
                            except Exception:
                                result['warning']='휴지통으로 보냈지만 찜 목록은 바꾸지 못했어요. 찜 해제를 다시 눌러 주세요.'
                        for library in (self.library,self.photo_library):
                            try:library.forget_file(path)
                            except Exception as error:warnings.append(str(error))
                        if warnings and not result.get('warning'):result['warning']='휴지통으로 보냈어요. 목록이 남아 있으면 다시 검색해 주세요.'
                if result.get('ok'):
                    try:result['coverage']=self.photo_library.coverage(self.photo_search_roots())
                    except Exception:pass
            except Exception as error:
                result=dict(ok=False,cancelled=False,error=str(error))
            self.events.put(lambda result=result:finish(result))
        def finish(result):
            self.photo_deleting.discard(path)
            if result.get('ok'):
                self.photo_removed.add(path);self.photo_removal_generation+=1
                self.result_cache=[item for item in self.result_cache if item['path']!=path]
                if result.get('coverage'):
                    self.photo_library.search_coverage.update(result['coverage'])
                for gallery in list(getattr(self,'result_browsers',())):
                    if callable(getattr(gallery,'remove_photo',None)):
                        gallery.remove_photo(path)
                        gallery.update_coverage(self.photo_library.search_coverage)
                if self.bubble and self.bubble.alive() and self.bubble.rows is not None:
                    self.bubble.results([item for item in self.bubble.rows if item['path']!=path])
                if getattr(self,'is_photo_search',False):
                    self.last_reply=self.photo_summary()
                    self.save_search_state('photos',self.photo_library.search_coverage,len(self.result_cache))
                if self.page=='home':self.render_results()
                self.status.set(result.get('warning') or '사진을 휴지통으로 보냈어요. Windows 휴지통에서 복원할 수 있어요.')
            else:
                self.status.set('사진 삭제를 취소했어요.' if result.get('cancelled') else '사진을 삭제하지 못했어요.')
            callback(result)
        threading.Thread(target=work,name='photo-recycle',daemon=True).start()

    def quit(self):
        if getattr(self,'photo_deleting',None):
            self.status.set('사진을 휴지통으로 보낸 뒤 종료할게요.')
            self.root.after(150,self.quit);return
        if getattr(self,'photo_searching',False):
            self.cancel_photo_search();self.root.after(150,self.quit);return
        if self.busy:return super().quit()
        if hasattr(self,'photo_cancel'):
            self.photo_cancel.set();self.photo_ai.interrupt()
        return super().quit()
