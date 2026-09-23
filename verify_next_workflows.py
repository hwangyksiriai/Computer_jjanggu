"""Offline release checks for collections, comparison and imported attachments."""
from email.message import EmailMessage
from pathlib import Path
import hashlib
import time


def run(app,base,source,idle):
    from mail_sync import import_eml
    from smart_collections import SmartCollectionStore
    from file_compare_ui import FileComparisonWindow
    from final_versions import FinalVersions

    def wait(predicate):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            app.root.update()
            if predicate():return
            time.sleep(.02)
        raise AssertionError('New workflow did not settle')

    def row(path):
        stat=path.stat()
        return dict(path=str(path),name=path.name,mtime=stat.st_mtime,size=stat.st_size)

    app.query.set('');app.last_submitted='';app.is_photo_search=False
    message=EmailMessage()
    message['From']='practice@example.invalid';message['To']='receiver@example.invalid'
    message['Subject']='연습 첨부파일 전달';message['Date']='Wed, 23 Sep 2026 09:00:00 +0900'
    message['Message-ID']='<synthetic-workflow@example.invalid>'
    message.set_content('합성 검증용 메일입니다.')
    message.add_attachment('월별지원비 1000원\n급여명세서 기본급 공제 실지급액'.encode('utf-8'),
                           maintype='text',subtype='plain',filename='자료_001.txt')
    eml=base/'합성 연습 메일.eml';eml.write_bytes(message.as_bytes())
    original=hashlib.sha256(eml.read_bytes()).hexdigest()
    store=app.get_mail_store()
    result=import_eml(store,[eml])
    assert not result.error and len(result.files)==1
    assert import_eml(store,[eml]).duplicates==1,'mail imports must deduplicate'
    attachment=Path(result.files[0])
    app.mail_attachments_changed();idle()
    matches,_=app.search_documents('월별지원비')
    assert str(attachment) in {r['path'] for r in matches},'mail attachment body must enter scoped search'
    provenance=app.mail_source(str(attachment))
    assert provenance['subject']==message['Subject'] and provenance['source_eml']==str(eml)
    assert hashlib.sha256(eml.read_bytes()).hexdigest()==original,'import must preserve the chosen .eml'
    mail_window=app.show_mail_connections();app.root.update()
    assert mail_window.winfo_exists();mail_window.destroy()
    mail_results=app.show_mail_attachments()
    assert len(mail_results.rows)==1
    mail_results.toggle_details()
    assert 'practice@example.invalid' in mail_results.details_text.get('1.0','end')
    mail_results.win.destroy()

    smart=SmartCollectionStore(app.data)
    ident=smart.create('월별 지원비 자동 모음',query='월별지원비')
    assert SmartCollectionStore(app.data).get(ident)['query']=='월별지원비'
    smart_window=app.show_smart_collections()
    browser=app.smart_collections_view.open(ident)
    loader=browser._smart_collection_source
    wait(lambda:not loader.loading and len(browser.rows)==1)
    additional=source/'새로 받은 문서.txt';additional.write_text('월별지원비 2000원',encoding='utf-8')
    app.reindex();idle()
    wait(lambda:not loader.loading and len(browser.rows)==2)
    before=list(app.document_roots())
    app.set_document_roots([]);app.document_locations_changed()
    assert not browser.rows,'removing roots must immediately remove old scoped results'
    idle();wait(lambda:not loader.loading)
    assert not browser.rows
    app.set_document_roots(before);app.document_locations_changed();idle()
    wait(lambda:not loader.loading and len(browser.rows)==2)
    browser.win.destroy();smart_window.destroy()

    left=base/'연습계약_v1.txt';right=base/'연습계약_v2.txt'
    left.write_text('계약 기간 12개월\n대금 1000원',encoding='utf-8')
    right.write_text('계약 기간 12개월\n대금 2000원\n추가 조건 확인',encoding='utf-8')
    originals={p:p.read_bytes() for p in (left,right)}
    comparison=FileComparisonWindow(app,row(left),[row(right)])
    comparison.toggle_changes()
    wait(lambda:comparison.diff_result is not None)
    assert comparison.diff_result.state=='ready' and comparison.diff_result.added and comparison.diff_result.removed
    wait(lambda:comparison.panes[1]['final_state'] is not None)
    comparison.toggle_final(1)
    wait(lambda:not comparison._final_busy and app.final_versions.describe(right)['state']=='final')
    assert FinalVersions(app.data).describe(right)['state']=='final'
    assert {p:p.read_bytes() for p in originals}==originals
    comparison.win.destroy()
    idle()
    return ['offline EML import deduplicates and preserves originals',
            'mail attachment body is searchable with sender and subject provenance',
            'mail connection and imported attachment windows open in the bundled app',
            'automatic collection definitions persist across reopen',
            'automatic collection includes newly indexed files',
            'automatic collection removes out-of-scope results immediately',
            'comparison highlights added and removed text',
            'user-selected final version persists without changing originals']
