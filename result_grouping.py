"""Optional, read-only duplicate grouping for a search results window."""
import queue
import threading
import tkinter as tk


class ResultGrouping:
    def setup_grouping(self,count_line,header):
        self.use_groups=False
        self.identical_groups={}
        self.expanded_groups=set()
        self.group_generation=0
        self.group_cancel=threading.Event()
        self.group_responses=queue.Queue()
        self.group_timer=None
        self.group_pending=False
        self.list_menu=tk.Menubutton(count_line,text='보기 ▾',font=('맑은 고딕',11),
                                    bg='#FFFDF9',fg='#65675F',relief='flat',cursor='hand2')
        menu=tk.Menu(self.list_menu,tearoff=False)
        menu.add_command(label='파일 하나씩 보기',command=lambda:self.set_grouping(False))
        menu.add_command(label='같은 파일 묶어 보기',command=lambda:self.set_grouping(True))
        self.list_menu.configure(menu=menu)
        self.list_menu.pack(side='right')
        self.group_note=self._label(header,'',10,fg='#65675F')
        self.group_note.configure(wraplength=800)

    def set_grouping(self,enabled):
        self.use_groups=enabled
        self.list_menu.configure(text='묶어 보기 ▾' if enabled else '보기 ▾')
        if enabled:self.start_grouping()
        else:
            self.group_cancel.set();self.group_generation+=1;self.group_pending=False
            self.identical_groups={};self.expanded_groups.clear()
            self.group_note.pack_forget()
            self.refresh(preserve_view=True)

    def start_grouping(self):
        if self.closed or not self.use_groups:return
        self.group_cancel.set();self.group_cancel=threading.Event()
        self.group_generation+=1;self.group_pending=True
        self.identical_groups={};self.expanded_groups.clear()
        self.group_note.configure(text='내용이 똑같은 파일을 확인하고 있어요. 파일은 그대로 있어요.')
        self.group_note.pack(anchor='w',pady=(4,0))
        self.refresh(preserve_view=True)
        threading.Thread(target=self._find_groups,args=(list(self.rows),self.group_cancel,
            self.group_responses,self.group_generation),daemon=True).start()
        if self.group_timer is None:self.group_timer=self.win.after(80,self.poll_groups)

    @staticmethod
    def _find_groups(rows,cancel,responses,generation):
        from file_comparison import group_identical_files
        try:result=group_identical_files(rows,cancelled=cancel.is_set)
        except Exception:result=None
        if not cancel.is_set():responses.put((generation,result))

    def poll_groups(self):
        self.group_timer=None
        if self.closed:return
        try:
            while True:
                generation,result=self.group_responses.get_nowait()
                if generation!=self.group_generation or not self.use_groups:continue
                self.group_pending=False
                if result is None:
                    self.group_note.configure(text='같은 파일인지 확인하지 못했어요. 파일을 하나씩 보여드릴게요.')
                    continue
                self.identical_groups={group[0]['path']:group for group in result.groups}
                for path,group in self.identical_groups.items():
                    if self.selected_path in {row['path'] for row in group[1:]}:
                        self.expanded_groups.add(path)
                count=len(result.groups)
                text=f'내용이 같은 파일 {count}묶음을 찾았어요. 펼쳐서 각각 볼 수 있어요.' if count else '지금 목록에는 내용이 같은 파일이 없어요.'
                if result.unverified:
                    text=(f'같은 파일 {count}묶음 · ' if count else '')+f'{len(result.unverified)}개는 확인하지 못해 그대로 보여요.'
                self.group_note.configure(text=text)
                self.refresh(preserve_view=True)
        except queue.Empty:pass
        if self.group_pending:self.group_timer=self.win.after(80,self.poll_groups)

    def grouping_rows(self):
        if not self.use_groups:return list(self.rows)
        members={row['path'] for group in self.identical_groups.values() for row in group[1:]}
        result=[]
        for row in self.rows:
            path=row['path']
            if path in members:continue
            result.append(row)
            if path in self.expanded_groups:
                # Keep equal copies beside their representative even if the
                # search assigned a different confidence/filename match group.
                result.extend(dict(member,group=row.get('group','일치하는 파일'))
                              for member in self.identical_groups.get(path,[])[1:])
        return result

    def add_group_action(self,parent,row):
        path=row['path'];group=self.identical_groups.get(path)
        if not self.use_groups or not group:return
        opened=path in self.expanded_groups
        action=self._button(parent,f'{"▾ 접기" if opened else "▸ 펼치기"} · 같은 파일 {len(group)}개',
                            lambda:self.toggle_group(path))
        action.configure(font=('맑은 고딕',10),padx=3,pady=5,anchor='w')
        action.pack(fill='x',pady=(0,5))

    def toggle_group(self,path):
        if path in self.expanded_groups:
            self.expanded_groups.remove(path)
            if self.selected_path in {row['path'] for row in self.identical_groups[path]}:
                self.selected_path=path
        else:
            self.expanded_groups.add(path)
            from result_browser import is_match
            group=self.identical_groups[path]
            paths={row['path'] for row in group}
            section=[row for row in self.grouping_rows() if is_match(row)==is_match(group[0])]
            self.visible_count=max(self.visible_count,max(i+1 for i,row in enumerate(section) if row['path'] in paths))
        self.refresh(preserve_view=True)

    def stop_grouping(self):
        self.group_cancel.set();self.group_pending=False
        if self.group_timer:
            self.win.after_cancel(self.group_timer);self.group_timer=None
