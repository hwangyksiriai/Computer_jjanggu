"""Persistent annotations, local embeddings, visual search and non-punitive rewards."""
import bootstrap
from datetime import datetime,timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import time
from PIL import Image,ImageDraw,ImageFont
import colorsys
from core import Library,BASE,IMAGE_EXTS,KINDS,fingerprint,serialized,classify,payroll_evidence,payroll_title,EXTRACT_VERSION

def cosine(a,b):
    if not a or not b: return 0.0
    return sum(x*y for x,y in zip(a,b))/(math.sqrt(sum(x*x for x in a)*sum(y*y for y in b)) or 1)

def fields_from_text(text):
    result={}
    patterns={
      'issuer':r'(?:공급자|발행자|거래처|판매자|from|supplier|vendor)\s*[:：]\s*([^\n]{2,70})',
      'amount':r'(?:청구금액|총액|합계|amount due|total due|grand total)\s*[:：]?\s*([^\n]{1,50})',
      'issued_date':r'(?:발행일|작성일|invoice date|issue date|date issued)\s*[:：]?\s*(\d{4}[-./년 ]+\d{1,2}[-./월 ]+\d{1,2})',
      'due_date':r'(?:지급기한|납부기한|payment due|due date)\s*[:：]?\s*(\d{4}[-./년 ]+\d{1,2}[-./월 ]+\d{1,2})'}
    for key,pattern in patterns.items():
        m=re.search(pattern,text,re.I)
        if m:
            value=m.group(1).strip()
            if key.endswith('date'):
                nums=re.findall(r'\d+',value)
                try: value=datetime(*map(int,nums[:3])).strftime('%Y-%m-%d')
                except ValueError: continue
            result[key]=value
    currencies=[]
    for code,pattern in [('USD',r'\bUSD\b|\$|달러'),('KRW',r'\bKRW\b|₩|\d\s*원'),('EUR',r'\bEUR\b|€|유로'),('JPY',r'\bJPY\b|엔화')]:
        if re.search(pattern,text,re.I): currencies.append(code)
    result['currencies']=currencies
    return result

def visual_words(query):
    words={ '파란':'blue','파랑':'blue','빨간':'red','빨강':'red','초록':'green','노란':'yellow',
            '분홍':'pink','검은':'black','하얀':'white','표지':'cover','제안서':'proposal document',
            '인보이스':'invoice document','청구서':'invoice','계약서':'contract','강아지':'dog',
            '고양이':'cat','바다':'sea','산':'mountain','꽃':'flower','차트':'chart','표가':'table',
            '사진':'photo','영수증':'receipt','방':'bedroom','짱구':'cartoon Shin-chan','로고':'logo'}
    return ' '.join(v for k,v in words.items() if k in query)

def image_colors(path):
    counts={name:0 for name in ('red','orange','yellow','green','blue','purple','pink')}
    try:
        with Image.open(path) as im:
            im=im.convert('RGB').resize((64,64))
            for r,g,b in list(im.get_flattened_data()) if hasattr(im,'get_flattened_data') else list(im.getdata()):
                h,s,v=colorsys.rgb_to_hsv(r/255,g/255,b/255)
                if s<.22 or v<.20: continue
                name='red' if h<.035 or h>.96 else 'orange' if h<.10 else 'yellow' if h<.18 else 'green' if h<.48 else 'blue' if h<.72 else 'purple' if h<.84 else 'pink'
                counts[name]+=1
        return {k:round(v/4096,3) for k,v in counts.items()}
    except Exception: return counts

class Knowledge(Library):
    def __init__(self,data_dir,ai=None):
        super().__init__(data_dir); self.ai=ai; self.ai_error=''
        self.thumbs=self.data/'thumbnails'; self.thumbs.mkdir(exist_ok=True)
        with self.connect() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS knowledge(path TEXT PRIMARY KEY,signature TEXT, vectors TEXT,
                visual TEXT,thumbnail TEXT,fields TEXT,tags TEXT DEFAULT '[]',manual_category TEXT DEFAULT '',note TEXT DEFAULT '');
              CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY,keyword TEXT UNIQUE,category TEXT,tags TEXT);
              CREATE TABLE IF NOT EXISTS rewards(event TEXT PRIMARY KEY,points INTEGER,created REAL,label TEXT);
              CREATE TABLE IF NOT EXISTS tray(path TEXT PRIMARY KEY,created REAL);
              CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY,path TEXT,query TEXT,accepted INTEGER,created REAL);
              CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY,event TEXT,elapsed REAL,count INTEGER,created REAL);
            ''')
            columns={r['name'] for r in c.execute('PRAGMA table_info(knowledge)')}
            if 'manual_fields' not in columns: c.execute("ALTER TABLE knowledge ADD COLUMN manual_fields TEXT DEFAULT '{}'")

    def annotate(self,path,category,tags,fields=None,keyword='',note=''):
        path=str(Path(path).resolve())
        category=category.strip()[:50]
        # Categories become directory names. Never allow path traversal.
        if not category or any(c in category for c in '<>:"/\\|?*') or category in ('.','..'):
            raise ValueError('분류 이름에는 경로 문자나 특수문자를 사용할 수 없어요.')
        tags=list(dict.fromkeys(t.strip()[:50] for t in tags if t.strip()))[:20]
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO knowledge(path,signature,vectors,visual,thumbnail,fields) VALUES(?,'','[]','[]','','{}')",(path,))
            existing=json.loads(c.execute('SELECT fields FROM knowledge WHERE path=?',(path,)).fetchone()['fields'] or '{}')
            existing.update(fields or {})
            for key in ('received_date','issued_date','due_date'):
                if existing.get(key): datetime.strptime(existing[key],'%Y-%m-%d')
            c.execute('UPDATE knowledge SET manual_category=?,tags=?,fields=?,note=? WHERE path=?',
                      (category,json.dumps(tags,ensure_ascii=False),json.dumps(existing,ensure_ascii=False),note[:1000],path))
            c.execute('UPDATE knowledge SET manual_fields=? WHERE path=?',(json.dumps(fields or {},ensure_ascii=False),path))
            c.execute('UPDATE files SET category=? WHERE path=?',(category,path))
            if keyword.strip(): c.execute('INSERT OR REPLACE INTO rules(keyword,category,tags) VALUES(?,?,?)',
                                          (keyword.strip().lower(),category,json.dumps(tags,ensure_ascii=False)))
        self.reward('teach:'+hashlib.sha256((path+category+str(tags)).encode()).hexdigest(),15,'내 분류 알려주기')

    def rules(self):
        with self.connect() as c: return [dict(r) for r in c.execute('SELECT * FROM rules ORDER BY id DESC')]
    def remove_rule(self,ident):
        with self.connect() as c: c.execute('DELETE FROM rules WHERE id=?',(ident,))

    def thumbnail(self,row):
        p=Path(row['path']); key=hashlib.sha256((str(p)+str(row['mtime'])+str(row['size'])).encode()).hexdigest()
        out=self.thumbs/(key+'.png')
        if out.exists(): return str(out)
        try:
            if p.suffix.lower() in IMAGE_EXTS:
                with Image.open(p) as im:
                    im=im.convert('RGB'); im.thumbnail((700,900)); im.save(out)
            elif p.suffix.lower()=='.pdf':
                import pymupdf
                with pymupdf.open(p) as doc:
                    if not doc.page_count or doc.is_encrypted: return ''
                    page=doc[0]; pix=page.get_pixmap(matrix=pymupdf.Matrix(1.2,1.2),alpha=False); pix.save(out)
            elif row['body']:
                im=Image.new('RGB',(600,800),'#FFFDF7'); d=ImageDraw.Draw(im)
                f=ImageFont.truetype('C:/Windows/Fonts/malgun.ttf',18)
                d.text((32,30),p.name[:26],font=f,fill='#3F473A')
                body=row['body'][:1300]; lines=[]
                for line in body.splitlines():
                    lines.extend(line[i:i+31] for i in range(0,len(line),31))
                d.multiline_text((32,85),'\n'.join(lines[:24]),font=f,fill='#474747',spacing=9)
                im.save(out)
            else: return ''
            return str(out)
        except Exception: return ''

    def index(self,roots,progress=None,only_paths=None):
        with self.connect() as c:
            rules=[dict(r) for r in c.execute('SELECT * FROM rules')]
            taught=[dict(r) for r in c.execute("SELECT * FROM knowledge WHERE manual_category<>'' AND vectors<>'[]'")]
        def enrich(row):
            path=row['path']; signature=f"{row['mtime']}:{row['size']}"
            with self.connect() as c:
                old=c.execute('SELECT * FROM knowledge WHERE path=?',(path,)).fetchone()
            old=dict(old) if old else {}
            thumb=old.get('thumbnail',''); vectors=json.loads(old.get('vectors') or '[]'); visual=json.loads(old.get('visual') or '[]')
            fields=json.loads(old.get('fields') or '{}'); tags=json.loads(old.get('tags') or '[]')
            if old.get('signature')!=signature or fields.get('_extract_version')!=EXTRACT_VERSION:
                fields={**fields_from_text(row['body']),**json.loads(old.get('manual_fields') or '{}')}
                fields['_extract_version']=EXTRACT_VERSION
                vectors=[]; visual=[]; thumb=self.thumbnail(row)
            if thumb and 'visual_colors' not in fields: fields['visual_colors']=image_colors(thumb)
            category=old.get('manual_category','')
            for rule in rules:
                if rule['keyword'] in (row['name']+' '+row['body']).lower():
                    category=category or rule['category']; tags=list(dict.fromkeys(tags+json.loads(rule['tags'])))
            if fields.get('issuer'): tags=list(dict.fromkeys(tags+[fields['issuer']]))
            ai_eligible=bool(row['body']) or Path(path).suffix.lower() in IMAGE_EXTS
            if ai_eligible and self.ai and self.ai.ready():
                try:
                    if not vectors:
                        content=row['name']+'\n'+row['category']+'\n'+row['body']
                        chunks=[content[i:i+1500] for i in range(0,min(len(content),30000),1200)] or [row['name']]
                        vectors=self.ai.embed(chunks)
                    if thumb and not visual: visual=self.ai.call('image',path=thumb)
                    self.ai_error=''
                except Exception as e: self.ai_error=str(e)
            if not category and vectors and taught:
                matches=[]
                for example in taught:
                    if example['path']==path: continue
                    examples=json.loads(example['vectors'])
                    similarity=max((cosine(vectors[0],v) for v in examples),default=0)
                    matches.append((similarity,example))
                matches.sort(key=lambda item:item[0],reverse=True)
                if matches:
                    best,example=matches[0]
                    other=max((score for score,ex in matches if ex['manual_category']!=example['manual_category']),default=0)
                    if best>=.90 and best-other>=.025:
                        category=example['manual_category']; fields['learned_from']=Path(example['path']).name
                        issuer=json.loads(example['fields'] or '{}').get('issuer')
                        tags=list(dict.fromkeys(tags+[t for t in json.loads(example['tags']) if t!=issuer]))
            with self.mutation_lock,self.connect() as c:
                current=c.execute('SELECT mtime,size FROM files WHERE path=?',(path,)).fetchone()
                try: stat=Path(path).stat()
                except OSError: return
                if not current or f"{stat.st_mtime}:{stat.st_size}"!=signature or f"{current['mtime']}:{current['size']}"!=signature: return
                c.execute('INSERT INTO knowledge(path,signature,vectors,visual,thumbnail,fields,tags,manual_category,note) VALUES(?,?,?,?,?,?,?,?,?) '
                          'ON CONFLICT(path) DO UPDATE SET signature=excluded.signature,vectors=excluded.vectors,visual=excluded.visual,thumbnail=excluded.thumbnail,fields=excluded.fields,tags=excluded.tags',
                          (path,signature,json.dumps(vectors),json.dumps(visual),thumb,json.dumps(fields,ensure_ascii=False),json.dumps(tags,ensure_ascii=False),old.get('manual_category',''),old.get('note','')))
                c.execute('UPDATE files SET category=? WHERE path=?',(category or classify(row['name'],row['body']),path))
        # Read every supported document before expensive embedding/image inference.
        # A slow model must not leave later documents searchable by filename only.
        super().index(roots,progress,only_paths=only_paths)
        return super().index(roots,progress,on_file=enrich,only_paths=only_paths)

    def rows(self,roots=None,limit=None,offset=0):
        with self.connect() as c:
            query='SELECT f.*,k.vectors,k.visual,k.thumbnail,k.fields,k.tags,k.manual_category,k.note FROM files f LEFT JOIN knowledge k ON f.path=k.path'
            params=[]
            if roots:
                params=[str(Path(r).resolve()) for r in roots]; query+=' WHERE f.scope IN ('+','.join('?' for _ in params)+')'
            query+=' ORDER BY f.mtime DESC'
            if limit is not None: query+=' LIMIT ? OFFSET ?'; params.extend([limit,offset])
            rows=c.execute(query,params).fetchall()
        out=[]
        for r in rows:
            r=dict(r)
            if not Path(r['path']).exists(): continue
            if roots and not any(Path(r['path']).is_relative_to(Path(root).resolve()) for root in roots): continue
            for key in ('vectors','visual','tags'): r[key]=json.loads(r[key] or '[]')
            r['fields']=json.loads(r['fields'] or '{}'); r['thumbnail']=r['thumbnail'] or ''
            out.append(r)
        return out

    def stats(self,roots):
        params=[str(Path(r).resolve()) for r in roots]
        with self.connect() as c:
            row=c.execute("SELECT COUNT(*) AS total,SUM(CASE WHEN k.vectors IS NULL OR k.vectors='[]' THEN 1 ELSE 0 END) AS pending FROM files f LEFT JOIN knowledge k ON f.path=k.path WHERE f.scope IN ("+','.join('?' for _ in params)+')',params).fetchone()
        return dict(total=row['total'],pending=row['pending'] or 0)

    def smart_search(self,query,roots,previous='',similar=None):
        self.ai_error=''
        q=(previous+' '+query if any(t in query for t in ('그중','그 중','거기서')) else query).strip()
        rows=self.rows(roots); exact,_=super().search(q,roots=roots)
        self.search_coverage={'registered':len(rows),'text':sum(bool(r['body'].strip()) for r in rows),
                              'semantic':sum(bool(r['vectors']) for r in rows)}
        exact_paths={r['path'] for r in exact}; tag_names=re.findall(r'#([^\s]+)',q)
        if not q and not similar:
            for r in rows: r.update(reason=r['status'],group='전체',score=1,evidence=r['body'][:180])
            return rows,q
        qvec=None; vvec=None; source=next((r for r in rows if r['path']==similar),None)
        if source:
            qvec=source['vectors'][0] if source['vectors'] else None; vvec=source['visual']
        elif self.ai and self.ai.ready():
            try:
                semantic_query=q
                if any(w in q.lower() for w in KINDS['급여명세서']):
                    semantic_query+=' 기본급 수당 공제 국민연금 건강보험 소득세 실수령액 사번 지급일 payslip gross pay deductions net pay'
                qvec=self.ai.embed([semantic_query],query=True)[0]
                translated=visual_words(q)
                if any(t in q for t in ('색','사진','그림','표지','이미지','생긴','보이는','강아지','고양이','바다','인보이스','청구서','영수증')):
                    simple=bool(re.fullmatch(r'\s*(파란|파랑|빨간|빨강|초록|노란|분홍|보라)?\s*(색)?\s*(표지)?\s*(제안서|문서|사진|인보이스|청구서|영수증|강아지|고양이)\s*(찾아줘|보여줘)?\s*',q))
                    if not translated or not simple:
                        translated=self.ai.call('chat',messages=[{'role':'system','content':'Translate the user image search description into a short English phrase. Return only the English translation. /no_think'},
                                                                 {'role':'user','content':q+' /no_think'}],max_tokens=55)
                    vvec=self.ai.call('visual_text',text=translated)
            except Exception as e: self.ai_error=str(e)
        wanted_kind='급여명세서' if payroll_title(q) else next((k for k,words in KINDS.items() if any(t in q.lower() for t in [k]+words)),None)
        currency=next((code for code,words in [('USD',['달러','usd']),('KRW',['원화','krw']),('EUR',['유로','eur']),('JPY',['엔화','jpy'])] if any(w in q.lower() for w in words)),None)
        now=datetime.now(); start=end=None
        if '지난달' in q:
            end=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0); start=(end-timedelta(days=1)).replace(day=1)
        elif '지난주' in q:
            end=(now-timedelta(days=now.weekday())).replace(hour=0,minute=0,second=0,microsecond=0); start=end-timedelta(days=7)
        elif '최근' in q: start=now-timedelta(days=7)
        date_key='received_date' if '받은' in q or '수신' in q else ('issued_date' if '발행' in q else None)
        results=[]
        for r in rows:
            if r['path']==similar: continue
            if tag_names and not all(t in r['tags'] for t in tag_names): continue
            if currency and currency not in r['fields'].get('currencies',[]): continue
            if start:
                value=r['fields'].get(date_key) if date_key else datetime.fromtimestamp(r['mtime']).strftime('%Y-%m-%d')
                try: date=datetime.strptime(value,'%Y-%m-%d')
                except (TypeError,ValueError): continue
                if date<start or (end and date>=end): continue
            semantic=max((cosine(qvec,v) for v in r['vectors']),default=0) if qvec else 0
            visual=cosine(vvec,r['visual']) if vvec else 0
            direct=(r['path'] in exact_paths) or any(t and t.lower() in q.lower() for t in r['tags'])
            inferred=classify(r['name'],r['body'])
            type_match=wanted_kind==(r['manual_category'] or inferred) if wanted_kind else False
            payroll=payroll_evidence(r['body']) if wanted_kind=='급여명세서' else []
            payroll_candidate=len(payroll)>=2 and (len(payroll)>=3 or bool(re.search(r'\d[\d,]{2,}',r['body'])))
            # Embedding cosine is not a probability. Generic business documents
            # often score highly; require payroll evidence for this document type.
            if wanted_kind=='급여명세서' and not (type_match or payroll_candidate):
                if not (payroll and semantic>=.86): continue
            if wanted_kind and r['manual_category'] and r['manual_category']!=wanted_kind: continue
            if tag_names and all(t in r['tags'] for t in tag_names): direct=True
            if not (direct or type_match or payroll_candidate or semantic>=.79 or visual>=.24): continue
            if similar and not (semantic>=.78 or visual>=.70): continue
            color=next((v for k,v in {'파란':'blue','파랑':'blue','빨간':'red','빨강':'red','초록':'green','녹색':'green','노란':'yellow','분홍':'pink','보라':'purple'}.items() if k in q),None)
            color_score=r['fields'].get('visual_colors',{}).get(color,0) if color else 0
            score=(1.2 if direct else 0)+(0.30 if type_match else 0)+(semantic*.4+visual*2 if vvec else semantic)+color_score
            reasons=[]
            if direct: reasons.append('이름·본문·태그 일치')
            if type_match: reasons.append('문서 유형 일치')
            if payroll: reasons.append('본문 단서: '+', '.join(payroll))
            if semantic>=.79: reasons.append(f'내용 의미 유사도 {semantic:.2f}')
            if visual>=.24: reasons.append(f'이미지 특징 유사도 {visual:.2f}')
            if color and color_score>.03: reasons.append(f'요청 색상 영역 {color_score:.0%}')
            terms=[t for t in re.findall(r'[\w가-힣]+',q) if len(t)>1]
            sentences=re.split(r'[\n.!?]',r['body'])
            evidence=next((s.strip() for s in sentences if any(t.lower() in s.lower() for t in terms)),r['body'][:180])
            if payroll: evidence='급여 문서 단서: '+', '.join(payroll)+' · '+evidence
            r.update(score=score,reason=' · '.join(reasons),group='일치하는 파일' if direct or type_match else '관련 후보',evidence=evidence[:220])
            if wanted_kind=='급여명세서' and not payroll_title(r['name']+' '+r['body']) and r['manual_category']!='급여명세서':
                r['group']='관련 후보'
            results.append(r)
        return sorted(results,key=lambda r:(r['group']=='일치하는 파일',r['score']),reverse=True),q

    def relocate(self):
        with self.connect() as c:
            for move in c.execute("SELECT * FROM moves WHERE state IN ('done','undone') ORDER BY created").fetchall():
                src,dst=(move['source'],move['dest']) if move['state']=='done' else (move['dest'],move['source'])
                if not Path(dst).exists(): continue
                c.execute('UPDATE OR IGNORE knowledge SET path=? WHERE path=?',(dst,src))
                c.execute('UPDATE OR IGNORE tray SET path=? WHERE path=?',(dst,src))
    @serialized
    def move(self,*args,**kw):
        result=super().move(*args,**kw); self.relocate()
        for path in result[0]: self.reward('store:'+fingerprint(path),10,'파일을 안전하게 보관')
        return result
    @serialized
    def undo(self):
        result=super().undo(); self.relocate(); return result

    def reward(self,event,points,label):
        # Repeat actions cannot farm rewards; points never decay.
        today=datetime.now().strftime('%Y-%m-%d')
        with self.connect() as c:
            count=c.execute('SELECT COUNT(*) FROM rewards WHERE created>=?',(datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp(),)).fetchone()[0]
            if count>=30: return False
            return c.execute('INSERT OR IGNORE INTO rewards VALUES(?,?,?,?)',(event,points,time.time(),label)).rowcount>0
    def progress(self):
        with self.connect() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM rewards ORDER BY created DESC')]
        points=sum(r['points'] for r in rows)
        return dict(points=points,level=1+points//50,events=rows,unlocked=['별 배지']+(['왕관'] if points>=30 else [])+(['반짝 축하'] if points>=60 else [])+(['우주 모자'] if points>=100 else []))
    def tray_add(self,paths):
        with self.connect() as c:
            for path in paths:
                if Path(path).is_file(): c.execute('INSERT OR IGNORE INTO tray VALUES(?,?)',(str(Path(path).resolve()),time.time()))
        self.reward('tray:first',15,'첫 작업 트레이 만들기')
    def tray_remove(self,path):
        with self.connect() as c: c.execute('DELETE FROM tray WHERE path=?',(path,))
    def tray_files(self):
        with self.connect() as c: return [r['path'] for r in c.execute('SELECT path FROM tray ORDER BY created') if Path(r['path']).exists()]
    def feedback(self,path,query,accepted):
        with self.connect() as c: c.execute('INSERT INTO feedback(path,query,accepted,created) VALUES(?,?,?,?)',(path,query,int(accepted),time.time()))
        if accepted: self.reward('find:'+datetime.now().strftime('%Y-%m-%d')+':'+path,5,'기억으로 파일 찾기')
    def record_usage(self,event,elapsed,count):
        with self.connect() as c: c.execute('INSERT INTO usage(event,elapsed,count,created) VALUES(?,?,?,?)',(event,elapsed,count,time.time()))
    def usage_summary(self):
        with self.connect() as c:
            row=c.execute("SELECT COUNT(*) AS searches,AVG(elapsed) AS mean_seconds FROM usage WHERE event='search'").fetchone()
            fb=c.execute('SELECT COUNT(*) AS total,SUM(accepted) AS accepted FROM feedback').fetchone()
        return dict(searches=row['searches'],mean_seconds=row['mean_seconds'] or 0,feedback=fb['total'],accepted=fb['accepted'] or 0)
