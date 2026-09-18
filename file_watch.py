"""Debounced local filesystem notifications. Never follows links or uploads data."""
import bootstrap
import threading,time
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from core import GENERATED_DIRS

class FileWatch(FileSystemEventHandler):
    def __init__(self,roots,callback,delay=1.5):
        self.roots=[Path(r).resolve() for r in roots]; self.callback=callback; self.delay=delay
        self.lock=threading.Lock(); self.pending=set(); self.changed=0; self.first_changed=0; self.stop_event=threading.Event()
        self.observer=Observer()
        for root in self.roots:
            if root.is_dir(): self.observer.schedule(self,str(root),recursive=True)
        self.observer.start()
        self.thread=threading.Thread(target=self.flush,daemon=True); self.thread.start()
    def allowed(self,path):
        p=Path(path).resolve()
        for root in self.roots:
            if p.is_relative_to(root):
                parts=p.relative_to(root).parts
                if any(n.lower() in GENERATED_DIRS or n.startswith(('.', '~','$')) for n in parts): return False
                try:
                    for item in [p,*list(p.parents)[:len(parts)-1]]:
                        if item.exists() and (item.is_symlink() or getattr(item.stat(),'st_file_attributes',0)&0x406): return False
                except OSError: return False
                return True
        return False
    def on_any_event(self,event):
        if event.event_type not in ('created','modified','deleted','moved'): return
        candidates=[event.src_path,getattr(event,'dest_path','')]
        with self.lock:
            added=False
            for path in candidates:
                if path and self.allowed(path):
                    if event.is_directory:
                        if event.event_type!='modified': self.pending.add(None); added=True
                    else: self.pending.add(str(Path(path).resolve())); added=True
            if added:
                self.changed=time.monotonic()
                if not self.first_changed: self.first_changed=self.changed
    def flush(self):
        while not self.stop_event.wait(.25):
            with self.lock:
                if not self.pending or (time.monotonic()-self.changed<self.delay and time.monotonic()-self.first_changed<5): continue
                batch=self.pending; self.pending=set(); self.first_changed=0
            self.callback(batch)
    def close(self):
        self.stop_event.set(); self.observer.stop(); self.observer.join(timeout=3)
