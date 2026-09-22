"""Read-only photo discovery and an independent visual catalogue.

This catalogue never changes the folder used for desktop organization.
"""
import json,math,os,sqlite3,time
from contextlib import closing
from pathlib import Path
from PIL import Image,ImageOps,ImageStat
from knowledge import Knowledge,image_colors
from core import IMAGE_EXTS,GENERATED_DIRS

PHOTO_EXTS=IMAGE_EXTS|{'.gif','.tif','.avif','.heic','.heif','.dng','.cr2','.nef','.arw'}
SKIP_DIRS=GENERATED_DIRS|{'appdata','windows','program files','program files (x86)','programdata','$recycle.bin','system volume information','.local','.codex','ai-runtime'}

def photo_roots(source=None,vault=None,extra=(),home=None):
    home=Path(home or Path.home())
    candidates=[home/name for name in ('Desktop','Downloads','Pictures','Documents')]
    if os.name=='nt' and home==Path.home():
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                for name in ('Desktop','Personal','My Pictures','{374DE290-123F-4565-9164-39C4925E467B}'):
                    try:candidates.append(Path(os.path.expandvars(winreg.QueryValueEx(key,name)[0])))
                    except OSError:pass
        except OSError:pass
        for name in ('OneDrive','OneDriveConsumer','OneDriveCommercial'):
            if os.environ.get(name):
                candidates.extend(Path(os.environ[name])/part for part in ('Pictures','Documents','Desktop'))
    candidates.extend(Path(p) for p in (source,vault,*extra) if p)
    result=[]
    for p in candidates:
        p=p.resolve()
        if not p.is_dir() or any(p.is_relative_to(root) for root in result):continue
        result=[root for root in result if not root.is_relative_to(p)];result.append(p)
    return result

class PhotoLibrary(Knowledge):
    photo_extensions=PHOTO_EXTS
    def __init__(self,data_dir,ai=None,exclude=()):
        super().__init__(data_dir,ai)
        self.exclude=[Path(p).resolve() for p in exclude]
        self.scan_state={};self._last_scan=0

    def reuse_analysis(self,source_library,roots,cancelled=lambda:False):
        """Reuse unchanged image analysis without copying document metadata or files.

        The document catalogue uses the same visual models but older thumbnails
        do not apply EXIF rotation. Such photos must be analysed again using this
        catalogue's correctly oriented thumbnail.
        """
        source=Path(source_library.db).resolve()
        if source==self.db.resolve() or not source.is_file():return 0
        targets={row['path']:row for row in self.rows(roots)
                 if Path(row['path']).suffix.lower() in PHOTO_EXTS}
        copied=0
        def visual_ok(value):
            return (isinstance(value,list) and bool(value)
                    and all(isinstance(n,(int,float)) and not isinstance(n,bool) and math.isfinite(n) for n in value)
                    and any(n!=0 for n in value))
        def objects_ok(fields):
            if fields.get('_objects_version')!=1 or not isinstance(fields.get('objects'),list):return False
            for obj in fields['objects']:
                if not isinstance(obj,dict) or not isinstance(obj.get('label'),str):return False
                score=obj.get('score');box=obj.get('box')
                if not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:return False
                if not isinstance(box,dict):return False
                if any(not isinstance(box.get(k),(int,float)) or not math.isfinite(box[k]) or not 0<=box[k]<=1
                       for k in ('xmin','ymin','xmax','ymax')):return False
                if box['xmin']>box['xmax'] or box['ymin']>box['ymax']:return False
            return True
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=30)) as old:
            old.row_factory=sqlite3.Row
            paths=list(targets)
            for start in range(0,len(paths),200):
                if cancelled():break
                batch=paths[start:start+200]
                candidates=old.execute('SELECT f.path,f.mtime,f.size,k.signature,k.visual,k.fields '
                    'FROM files f JOIN knowledge k ON f.path=k.path WHERE f.path IN ('+
                    ','.join('?' for _ in batch)+')',batch).fetchall()
                for candidate in candidates:
                    if cancelled():return copied
                    row=targets[candidate['path']]
                    signature=f"{row['mtime']}:{row['size']}"
                    if candidate['signature']!=signature or (candidate['mtime'],candidate['size'])!=(row['mtime'],row['size']):continue
                    try:
                        old_visual=json.loads(candidate['visual'] or '[]')
                        old_fields=json.loads(candidate['fields'] or '{}')
                        if not isinstance(old_fields,dict):old_fields={}
                        visual=visual_ok(old_visual);objects=objects_ok(old_fields)
                        if not visual and not objects:continue
                        # Avoid decoding the image or checking EXIF once both
                        # kinds of analysis are already valid in this catalogue.
                        if (not visual or visual_ok(row['visual'])) and (not objects or objects_ok(row['fields'])):continue
                        stat=Path(row['path']).stat()
                        if (stat.st_mtime,stat.st_size)!=(row['mtime'],row['size']):continue
                        if getattr(stat,'st_file_attributes',0)&(0x1000|0x400000):continue
                        with Image.open(row['path']) as image:
                            if image.getexif().get(274,1)!=1:continue
                    except (OSError,ValueError,TypeError,Image.DecompressionBombError):continue
                    # A scan may have finished this photo after targets was read.
                    # Re-read under the writer lock and fill only missing values.
                    with self.mutation_lock,self.connect() as connection:
                        current=connection.execute('SELECT f.mtime,f.size,k.signature,k.visual,k.fields,k.thumbnail '
                            'FROM files f LEFT JOIN knowledge k ON f.path=k.path WHERE f.path=?',(row['path'],)).fetchone()
                        if not current or (current['mtime'],current['size'])!=(row['mtime'],row['size']):continue
                        try:
                            stat=Path(row['path']).stat()
                            if (stat.st_mtime,stat.st_size)!=(row['mtime'],row['size']):continue
                            valid=current['signature']==signature
                            current_visual=json.loads(current['visual'] or '[]') if valid else []
                            fields=json.loads(current['fields'] or '{}') if valid else {}
                            if not isinstance(fields,dict):fields={}
                        except (OSError,ValueError,TypeError):continue
                        changed=False
                        if visual and not visual_ok(current_visual):current_visual=old_visual;changed=True
                        if objects and not objects_ok(fields):
                            fields.update(objects=old_fields['objects'],_objects_version=1);changed=True
                        if not changed:continue
                        thumbnail=(current['thumbnail'] or '') if valid else ''
                        self.store_photo(row,thumbnail,fields,current_visual,
                            '사진 모습 분석됨' if current_visual else '사진 미리보기 준비됨')
                        copied+=1
        return copied

    def scan(self,roots,progress=lambda state:None,cancelled=lambda:False,yield_to_search=lambda:False,reuse_from=None):
        """Publish names first, then local thumbnails, then expensive AI details."""
        roots=[Path(root).resolve() for root in roots]
        state=dict(phase='catalog',registered=0,completed=0,total=0,offline=0,unreadable=0,skipped_folders=0,roots=[str(p) for p in roots])
        work=[];seen=set();last_notice=0
        def report(force=False):
            nonlocal last_notice
            self.scan_state=dict(state)
            if force or time.monotonic()-last_notice>.5:progress(dict(state));last_notice=time.monotonic()
        def visit_error(error):state['skipped_folders']+=1
        for root in roots:
            if any(root.is_relative_to(p) for p in self.exclude):continue
            for folder,dirs,files in os.walk(root,followlinks=False,onerror=visit_error):
                if cancelled():state['phase']='paused';report(True);return state
                dirs[:]=[name for name in dirs if name.lower() not in SKIP_DIRS and not name.startswith(('.', '$'))
                         and not (Path(folder)/name).is_symlink() and not getattr(Path(folder)/name,'is_junction',lambda:False)()
                         and not any((Path(folder)/name).resolve().is_relative_to(p) for p in self.exclude)]
                batch=[]
                for name in files:
                    path=Path(folder)/name
                    if path.suffix.lower() not in PHOTO_EXTS or name.startswith(('.', '~', '$')):continue
                    try:
                        if path.is_symlink():continue
                        stat=path.stat();key=str(path.resolve())
                        if key in seen:continue
                        seen.add(key)
                        offline=bool(getattr(stat,'st_file_attributes',0)&(0x1000|0x400000))
                        state['offline']+=int(offline)
                        batch.append((key,name,stat,str(root),offline));work.append((key,offline))
                    except OSError:state['unreadable']+=1
                with self.mutation_lock,self.connect() as c:
                    for key,name,stat,scope,offline in batch:
                        old=c.execute('SELECT mtime,size FROM files WHERE path=?',(key,)).fetchone()
                        if not old or old['mtime']!=stat.st_mtime or old['size']!=stat.st_size:
                            c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)',
                                (key,name,'','사진','PC에 내려받지 않은 사진' if offline else '사진 모습 분석 대기',stat.st_mtime,stat.st_size,scope))
                        else:c.execute('UPDATE files SET scope=? WHERE path=?',(scope,key))
                state['registered']=len(work);report()
        state.update(total=len(work),phase='thumbnails');report(True)
        if reuse_from is not None:
            state['reused_analysis']=self.reuse_analysis(reuse_from,roots,cancelled=cancelled)
            report(True)
        current={r['path']:r for r in self.rows(roots)}
        # Local thumbnails and colors are available even before optional models.
        for number,(key,offline) in enumerate(work,1):
            if cancelled():state['phase']='paused';report(True);return state
            row=current.get(key)
            if row and not offline:
                fields=row['fields']
                if not row['thumbnail'] or not Path(row['thumbnail']).is_file() or 'brightness' not in fields:
                    try:
                        if row['size']>80*1024**2:raise ValueError('80MB보다 큰 사진')
                        with Image.open(key) as im:
                            taken=im.getexif().get(36867) or im.getexif().get(306)
                            im=ImageOps.exif_transpose(im).convert('RGB');width,height=im.size;im.thumbnail((640,640))
                            import hashlib
                            thumb=self.thumbs/(hashlib.sha256((key+str(row['mtime'])+str(row['size'])).encode()).hexdigest()+'.jpg')
                            im.save(thumb,quality=82)
                            fields.update(brightness=round(ImageStat.Stat(im.convert('L')).mean[0]/255,3),photo_width=width,photo_height=height)
                        if taken:
                            try:fields['taken_date']=time.strftime('%Y-%m-%d',time.strptime(str(taken),'%Y:%m:%d %H:%M:%S'))
                            except ValueError:pass
                        fields['visual_colors']=image_colors(thumb);fields.pop('photo_error',None)
                        self.store_photo(row,str(thumb),fields,row['visual'],'사진 미리보기 준비됨')
                        row.update(thumbnail=str(thumb),fields=fields)
                    except (OSError,ValueError,Image.DecompressionBombError) as error:
                        fields['photo_error']='사진 형식을 읽지 못했어요' if not isinstance(error,ValueError) else str(error)
                        self.store_photo(row,'',fields,[],'읽지 못한 사진');state['unreadable']+=1
            state['completed']=number;report()
        state.update(phase='analysis',completed=0);report(True)
        failures=0
        for number,(key,offline) in enumerate(work,1):
            while yield_to_search() and not cancelled():time.sleep(.05)
            if cancelled():state['phase']='paused';report(True);return state
            row=current.get(key)
            if row and row['thumbnail'] and not offline and not row['fields'].get('photo_error'):
                try:
                    if self.ai and self.ai.ready_for('vision') and not row['visual']:
                        row['visual']=self.ai.call('image',path=row['thumbnail'])
                    fields=row['fields']
                    if self.ai and self.ai.detector_ready() and fields.get('_objects_version')!=1:
                        detections=self.ai.call('objects',path=row['thumbnail'])
                        for detection in detections:detection['colors']=image_colors(row['thumbnail'],detection['box'])
                        fields.update(objects=detections,_objects_version=1)
                    self.store_photo(row,row['thumbnail'],fields,row['visual'],'사진 모습 분석됨' if row['visual'] else '사진 미리보기 준비됨')
                    failures=0;self.ai_error=''
                except Exception as error:
                    self.ai_error=str(error);failures+=1
                    if failures>=3:state['phase']='error';report(True);return state
            state['completed']=number;report()
        state['phase']='complete';self._last_scan=time.monotonic();report(True)
        return state

    def store_photo(self,row,thumbnail,fields,visual,status):
        with self.mutation_lock,self.connect() as c:
            current=c.execute('SELECT mtime,size FROM files WHERE path=?',(row['path'],)).fetchone()
            if not current or (current['mtime'],current['size'])!=(row['mtime'],row['size']):return
            try:
                stat=Path(row['path']).stat()
                if (stat.st_mtime,stat.st_size)!=(row['mtime'],row['size']):return
            except OSError:return
            c.execute("INSERT INTO knowledge(path,signature,vectors,visual,thumbnail,fields) VALUES(?,?,'[]',?,?,?) ON CONFLICT(path) DO UPDATE SET signature=excluded.signature,visual=excluded.visual,thumbnail=excluded.thumbnail,fields=excluded.fields",
                      (row['path'],f"{row['mtime']}:{row['size']}",json.dumps(visual),thumbnail,json.dumps(fields,ensure_ascii=False)))
            c.execute('UPDATE files SET status=? WHERE path=?',(status,row['path']))

    def coverage(self,roots):
        rows=self.rows(roots)
        return dict(registered=len(rows),images=len(rows),visual=sum(bool(r['visual']) for r in rows),
                    detected=sum(r['fields'].get('_objects_version')==1 for r in rows),
                    thumbnails=sum(bool(r['thumbnail']) for r in rows),unreadable=sum(bool(r['fields'].get('photo_error')) for r in rows),
                    offline=sum(r['status']=='PC에 내려받지 않은 사진' for r in rows),roots=[str(p) for p in roots],
                    photo_scan=dict(self.scan_state))
