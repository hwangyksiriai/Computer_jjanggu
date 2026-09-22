"""Human-readable scope and partial-analysis information for every search surface."""
from collections import Counter
from pathlib import Path

DOCUMENT_EXTS={'.pdf','.docx','.doc','.pptx','.ppt','.xlsx','.xls','.hwp','.hwpx',
               '.txt','.md','.csv','.tsv','.json','.log'}

def coverage_for(rows, image_exts):
    documents=[r for r in rows if Path(r['path']).suffix.lower() in DOCUMENT_EXTS]
    images=[r for r in rows if Path(r['path']).suffix.lower() in image_exts]
    pending=[r for r in rows if r.get('status','').startswith('분석 대기')]
    unread=[r for r in documents if not r.get('body','').strip() and not r.get('status','').startswith('분석 대기')]
    return dict(registered=len(rows),text=sum(bool(r.get('body','').strip()) for r in rows),
                semantic=sum(bool(r.get('vectors')) for r in rows),
                documents=len(documents),unread_documents=len(unread),pending=len(pending),
                images=len(images),visual=sum(bool(r.get('visual')) for r in images),
                detected=sum((r.get('fields') or {}).get('_objects_version')==1 for r in images),
                unread_reasons=dict(Counter(r.get('status','확인 필요') for r in unread)))

def summary(count,coverage,notice='',indexing=False,sample=False):
    first=f'{count}개 찾았어요.' if count else '현재 확인한 파일에서는 찾지 못했어요.'
    if sample:first='연습용 파일에서 '+first
    lines=[first]
    pending=coverage.get('pending',0)
    if indexing:
        lines.append((f'아직 {pending}개 파일의 내용을 분석 중이에요.' if pending else '아직 추가 분석 중이에요.')+' 결과는 자동으로 갱신돼요.')
    elif pending:lines.append(f'내용 분석을 기다리는 파일이 {pending}개 있어요. 다시 분석을 눌러 주세요.')
    unread=coverage.get('unread_documents',0)
    if unread:lines.append(f'본문을 읽지 못한 문서 {unread}개는 이름으로만 찾을 수 있어요.')
    if notice:lines.append(notice)
    return '\n'.join(lines)

def coverage_text(coverage):
    return (f"등록 {coverage.get('registered',0)}개 · 본문 확인 {coverage.get('text',0)}개 · "
            f"내용 분석 대기 {coverage.get('pending',0)}개 · 사진 분석 {coverage.get('visual',0)}/{coverage.get('images',0)}개")
