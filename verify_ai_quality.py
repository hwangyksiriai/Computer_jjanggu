"""Small transparent synthetic acceptance set, not a claim about real-user recall."""
import bootstrap
import json
from pathlib import Path
import time
from PIL import Image,ImageDraw,ImageFont
import pymupdf
from local_ai import LocalAI
from knowledge import Knowledge

base=Path(__file__).resolve().parent/'.local/ai-acceptance'; source=base/'fixtures'; source.mkdir(parents=True,exist_ok=True)
texts={
 'a17.txt':'INVOICE\nSupplier: Green Design Studio\nInvoice date: 2026-09-10\nAmount due: USD 1320\nPayment due: 2026-10-01\nWeb design services for September.',
 'b82.txt':'업무 위탁 계약서\n디자인 용역 업무에 관한 계약\n계약 기간: 2026년 9월부터 12월까지. 양 당사자가 지켜야 할 의무와 보수 지급 조건.',
 'c39.txt':'여름 휴가 계획\n제주도 해변 산책과 숙소 예약. 여행 기간은 8월 20일부터 23일까지. 렌터카를 예약한다.',
 'd11.txt':'영수증\n커피 결제 완료\n아메리카노 두 잔 합계 9000원. Payment received.',
 'e76.txt':'프로젝트 회의록\n브랜드 리뉴얼 일정은 다음 달로 미뤄졌다. 김 팀장이 시안 검토를 맡고 다음 주 수요일에 다시 논의한다.'}
for name,text in texts.items(): (source/name).write_text(text,encoding='utf-8')
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',42)
for name,color,title in [('picture_01.png','#387ACC','PROJECT PROPOSAL'),('picture_02.png','#D74338','PROJECT PROPOSAL')]:
    im=Image.new('RGB',(600,800),color); draw=ImageDraw.Draw(im)
    draw.rectangle((35,100,565,600),fill='white'); draw.text((50,160),title,font=font,fill='#333333'); im.save(source/name)
im=Image.new('RGB',(1000,650),'white'); draw=ImageDraw.Draw(im)
draw.multiline_text((45,45),'INVOICE\nSupplier: Blue Studio\nInvoice date: 2026-09-09\nAmount due: USD 880\nPayment due: 2026-10-01',font=font,fill='black',spacing=30)
scan=source/'scan_0032.png'; im.save(scan)
pdf=pymupdf.open(); page=pdf.new_page(width=600,height=390); page.insert_image(page.rect,filename=str(scan)); pdf.save(source/'scan_document.pdf'); pdf.close()
ai=LocalAI(); library=Knowledge(base/'state',ai)
started=time.time()
try:
    print('Indexing fixtures...',flush=True); library.index([source])
    if library.ai_error: raise RuntimeError(library.ai_error)
    cases=[
      ('인보이스 찾아줘',{'a17.txt','scan_0032.png','scan_document.pdf'}),
      ('용역 계약 기간이 적힌 문서',{'b82.txt'}),
      ('바닷가 여행 숙소 예약 계획',{'c39.txt'}),
      ('회의에서 일정 미룬 내용',{'e76.txt'}),
      ('파란 표지 제안서',{'picture_01.png'}),
      ('빨간 표지 제안서',{'picture_02.png'})]
    results=[]
    for query,expected in cases:
        start=time.time(); rows,_=library.smart_search(query,[source]); top=[r['name'] for r in rows[:3]]
        recall=len(expected.intersection(top))/len(expected)
        item={'query':query,'expected':sorted(expected),'top3':top,'recall_at_3':recall,'seconds':round(time.time()-start,2)}
        results.append(item); print(json.dumps(item,ensure_ascii=False),flush=True)
    response=ai.chat('청구 금액과 지급기한을 알려줘.',texts['a17.txt'])
    intent=ai.intent('정리 좀 도와줘')
    report={'fixture_count':len(list(source.iterdir())),'index_and_test_seconds':round(time.time()-started,1),
            'mean_recall_at_3':sum(r['recall_at_3'] for r in results)/len(results),'cases':results,
            'chat_response':response,'intent':intent,'scope':'Synthetic fixtures only. No real user files or long-term usability study.'}
    (base/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'chat':response,'intent':intent,'mean_recall_at_3':report['mean_recall_at_3']},ensure_ascii=False),flush=True)
    assert intent=='CLEAN'
    assert report['mean_recall_at_3']>=.80,report
finally: ai.close()
