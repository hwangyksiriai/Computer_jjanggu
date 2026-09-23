"""Synthetic-only mail tests: no accounts, sockets, or user files."""
from email.message import EmailMessage
import hashlib
import imaplib
import os
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from mail_providers import profile_values, webmail_url
from mail_store import MailStore, safe_filename
from mail_sync import BoundedIMAP, MAX_MESSAGE, import_eml, parse_message, sync_profile


def message(*attachments, subject='9월 급여명세서'):
    mail = EmailMessage()
    mail['From'] = '회계팀 <payroll@example.test>'
    mail['To'] = 'employee@example.test'
    mail['Subject'] = subject
    mail['Date'] = 'Wed, 23 Sep 2026 12:00:00 +0900'
    mail['Message-ID'] = '<fixture-1@example.test>'
    mail.set_content('합성 검증용 메일입니다.')
    for name, data in attachments:
        mail.add_attachment(data, maintype='application', subtype='octet-stream', filename=name)
    return mail.as_bytes()


class FakeIMAP:
    def __init__(self, payload, validity=b'123', after_peek=None):
        self.payload = payload
        self.validity = validity
        self.after_peek = after_peek
        self.commands = []
        self.reported_uid = b'12'
        self.reported_size = len(payload)
        self.uid_list = b'12'

    def login(self, address, secret):
        self.commands.append(('LOGIN', address))
        return 'OK', [b'accepted']

    def select(self, mailbox, readonly=False):
        self.commands.append(('SELECT', mailbox, readonly))
        return 'OK', [b'1']

    def response(self, code):
        return code, [self.validity]

    def uid(self, command, *args):
        self.commands.append((command, *args))
        if command == 'SEARCH':
            return 'OK', [self.uid_list]
        if args[1] == '(UID RFC822.SIZE)':
            return 'OK', [b'1 (UID '+self.reported_uid+b' RFC822.SIZE '+str(self.reported_size).encode()+b')']
        if 'BODY.PEEK[]' in args[1]:
            if self.after_peek:
                self.after_peek()
            return 'OK', [(b'1 (UID '+self.reported_uid+b' BODY[]<0> {'+str(len(self.payload)).encode()+b'}',self.payload),b')']
        raise AssertionError('Unexpected command')

    def logout(self):
        self.commands.append(('LOGOUT',))


class MailImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jjanggu-mail-test-')
        self.base = Path(self.temp.name)
        self.store = MailStore(self.base / 'data')
        self.profile = self.store.save_profile('naver', 'fixture@naver.com')

    def tearDown(self):
        self.temp.cleanup()

    def sync(self, fake, cancelled=lambda:False):
        def factory(host, **kwargs):
            self.assertEqual(host, 'imap.naver.com')
            self.assertTrue(kwargs['ssl_context'].check_hostname)
            self.assertEqual(kwargs['port'], 993)
            self.assertEqual(kwargs['timeout'], 20)
            return fake
        return sync_profile(self.store, self.profile, 'synthetic-password', cancelled, factory)

    def eml(self, raw=None):
        path = self.base/'원본 메일.eml'
        path.write_bytes(raw or message(('급여.pdf',b'%PDF-fixture')))
        return path

    def test_eml_metadata_original_preserved_and_restart(self):
        path = self.eml()
        before = (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).digest())
        result = import_eml(self.store,[path])
        self.assertEqual(len(result.files),1)
        store = MailStore(self.store.data)
        row = store.by_attachment(result.files[0])
        self.assertIn('회계팀',row['sender'])
        self.assertEqual(row['subject'],'9월 급여명세서')
        self.assertEqual(row['source_eml'],str(path))
        self.assertEqual(Path(result.files[0]).read_bytes(),b'%PDF-fixture')
        self.assertEqual(before,(path.stat().st_mtime_ns,hashlib.sha256(path.read_bytes()).digest()))
        self.assertEqual(store.attachment_count(),1)
        self.assertEqual(len(store.attachments(limit=1)),1)
        self.assertEqual(store.attachments(limit=0),[])

    def test_eml_dedup_content_not_filename(self):
        path = self.eml()
        duplicate = self.base/'복사본.eml'
        duplicate.write_bytes(path.read_bytes())
        import_eml(self.store,[path])
        result = import_eml(self.store,[duplicate])
        self.assertEqual(result.duplicates,1)
        self.assertEqual(self.store.attachment_count(),1)

    def test_single_provenance_lookup_does_not_enumerate_cache(self):
        result = import_eml(self.store,[self.eml()])
        with patch.object(self.store,'attachments',side_effect=AssertionError('Must query one row')):
            self.assertIsNotNone(self.store.by_attachment(result.files[0]))

    def test_malicious_paths_and_unicode_duplicate_names_stay_inside_cache(self):
        names = ['../../밖.txt',r'C:\Users\누군가\문서.txt','CON.txt','급여.txt','급여.txt','가.txt','가.txt','사진\u202egnp.txt']
        path = self.eml(message(*[(name,b'fixture') for name in names]))
        result = import_eml(self.store,[path])
        self.assertEqual(len(result.files),len(names))
        self.assertEqual(len({os.path.normcase(x) for x in result.files}),len(names))
        for file in result.files:
            self.assertTrue(Path(file).is_relative_to(self.store.cache))
        self.assertFalse((self.base/'밖.txt').exists())
        self.assertEqual(safe_filename('CON.txt'),'_CON.txt')

    def test_executables_and_html_are_not_imported_or_opened(self):
        raw = message(('run.exe',b'MZ'),('script.js',b'alert()'),('page.html',b'<script>'),('good.txt',b'hello'))
        metadata,files,skipped = parse_message(raw)
        self.assertEqual([x[0] for x in files],['good.txt'])
        self.assertEqual(skipped,3)

    def test_large_message_and_headers_refused(self):
        with self.assertRaises(ValueError):
            parse_message(b'a'*(MAX_MESSAGE+1))
        with self.assertRaises(ValueError):
            parse_message(b'X-Large: '+b'a'*70000+b'\r\n\r\nbody')

    def test_cache_limit_refuses_without_partial_write(self):
        small = MailStore(self.base/'small',max_bytes=5)
        profile = small.save_profile('eml')
        with self.assertRaises(ValueError):
            small.add_message(profile['id'],'fixture',{},[('a.txt',b'123'),('b.txt',b'456')])
        self.assertEqual(small.attachments(),[])
        self.assertEqual(list(small.cache.rglob('*.txt')),[])

    def test_cancelled_message_rolls_back_new_files_and_seen_record(self):
        calls = []
        def stop():
            calls.append(1)
            return len(calls)>1
        with self.assertRaises(InterruptedError):
            self.store.add_message(self.profile['id'],'fixture',{},[('a.txt',b'a'),('b.txt',b'b')],stop)
        self.assertEqual(self.store.attachments(),[])
        self.assertFalse(self.store.seen(self.profile['id'],'fixture'))
        self.assertEqual(list(self.store.cache.rglob('*.txt')),[])

    def test_symlink_guard_refuses_before_writing(self):
        import mail_store
        original = mail_store._link
        account = self.store.cache/self.profile['id']
        account.mkdir()
        with patch('mail_store._link',side_effect=lambda p:p==account or original(p)):
            with self.assertRaises(ValueError):
                self.store.add_message(self.profile['id'],'fixture',{},[('a.txt',b'a')])
        self.assertEqual(self.store.attachments(),[])

    def test_imap_readonly_peek_only_and_no_server_mutations(self):
        fake = FakeIMAP(message(('급여.txt',b'fixture')))
        result = self.sync(fake)
        self.assertEqual(result.error,'')
        self.assertEqual(len(result.files),1)
        self.assertIn(('SELECT','INBOX',True),fake.commands)
        self.assertTrue(any('BODY.PEEK[]' in str(x) for x in fake.commands))
        self.assertEqual({c[0] for c in fake.commands},{'LOGIN','SELECT','SEARCH','FETCH','LOGOUT'})
        self.assertEqual(self.store.by_attachment(result.files[0])['webmail_url'],'https://mail.naver.com/')

    def test_uidvalidity_dedup_and_reset(self):
        raw = message(('급여.txt',b'fixture'))
        first = self.sync(FakeIMAP(raw))
        second_fake = FakeIMAP(raw)
        second = self.sync(second_fake)
        third = self.sync(FakeIMAP(raw,validity=b'124'))
        self.assertEqual(second.duplicates,1)
        self.assertFalse(any(c[0]=='FETCH' for c in second_fake.commands))
        self.assertEqual(len(first.files)+len(third.files),2)
        self.assertEqual(self.store.attachment_count(),2)

    def test_uidvalidity_missing_refuses_all_fetch(self):
        fake = FakeIMAP(message(),validity=None)
        result = self.sync(fake)
        self.assertIn('고유 번호',result.error)
        self.assertFalse(any(c[0]=='FETCH' for c in fake.commands))
        self.assertEqual(fake.commands[-1],('LOGOUT',))

    def test_size_preflight_prevents_large_body_download(self):
        fake = FakeIMAP(message())
        fake.reported_size = MAX_MESSAGE+1
        result = self.sync(fake)
        self.assertEqual(result.skipped,1)
        self.assertFalse(any('PEEK' in str(c) for c in fake.commands))

    def test_server_literal_limit_before_allocation(self):
        connection = object.__new__(BoundedIMAP)
        with self.assertRaises(ValueError):
            connection.read(MAX_MESSAGE+2)

    def test_mismatched_uid_and_message_length_never_imported(self):
        for uid,size in [(b'13',None),(b'12',3)]:
            fake = FakeIMAP(message(('급여.txt',b'fixture')))
            fake.reported_uid = uid
            if size is not None: fake.reported_size=size
            self.assertEqual(self.sync(fake).files,[])

    def test_uid_may_follow_the_body_literal(self):
        fake = FakeIMAP(message(('급여.txt',b'fixture')))
        original = fake.uid
        def uid(command,*args):
            if command=='FETCH' and 'PEEK' in args[1]:
                return 'OK',[(b'1 (BODY[]<0> {'+str(len(fake.payload)).encode()+b'}',fake.payload),b' UID 12)']
            return original(command,*args)
        fake.uid=uid
        self.assertEqual(len(self.sync(fake).files),1)

    def test_cancellation_after_peek_does_not_commit(self):
        cancelled = threading.Event()
        fake = FakeIMAP(message(('급여.txt',b'fixture')),after_peek=cancelled.set)
        result = self.sync(fake,cancelled.is_set)
        self.assertTrue(result.cancelled)
        self.assertEqual(self.store.attachment_count(),0)
        self.assertEqual(fake.commands[-1],('LOGOUT',))

    def test_cancellation_before_network(self):
        fake = FakeIMAP(message())
        self.assertTrue(self.sync(fake,lambda:True).cancelled)
        self.assertEqual(fake.commands,[])

    def test_auth_error_is_sanitized(self):
        fake = FakeIMAP(message())
        fake.login = lambda *args: (_ for _ in ()).throw(imaplib.IMAP4.error('secret synthetic-password'))
        result = self.sync(fake)
        self.assertNotIn('synthetic-password',result.error)
        self.assertIn('앱 비밀번호',result.error)

    def test_password_not_stored_by_default_and_disconnect_keeps_copies(self):
        result = self.sync(FakeIMAP(message(('급여.txt',b'fixture'))))
        self.assertFalse(self.store.profile(self.profile['id'])['has_saved_secret'])
        self.store.disconnect(self.profile['id'])
        self.assertTrue(Path(result.files[0]).exists())
        self.assertFalse(self.store.profile(self.profile['id'])['enabled'])
        self.assertIn('imap.naver.com',self.profile['host'])
        self.assertNotIn(b'synthetic-password',self.store.db.read_bytes())

    def test_dpapi_storage_uses_cipher_and_disconnect_forgets(self):
        with patch('mail_credentials.protect',return_value=b'encrypted') as protect:
            self.store.store_secret(self.profile['id'],'fixture-secret')
        protect.assert_called_once_with('fixture-secret')
        self.assertNotIn(b'fixture-secret',self.store.db.read_bytes())
        with patch('mail_credentials.unprotect',return_value='fixture-secret'):
            self.assertEqual(self.store.saved_secret(self.profile['id']),'fixture-secret')
        self.store.disconnect(self.profile['id'])
        self.assertEqual(self.store.saved_secret(self.profile['id']),'')

    def test_clear_cache_only_copies_not_eml(self):
        source = self.eml()
        result = import_eml(self.store,[source])
        profile = next(p for p in self.store.profiles() if p['provider']=='eml')
        self.store.clear_cache(profile['id'])
        self.assertTrue(source.exists())
        self.assertFalse(Path(result.files[0]).exists())
        self.assertEqual(self.store.attachment_roots(),[])

    def test_remap_and_undo_preserve_provenance_external_copy_not_deleted(self):
        result = self.sync(FakeIMAP(message(('급여.txt',b'fixture'))))
        source = Path(result.files[0]); target = self.base/'내가 정리한 파일.txt'
        source.rename(target)
        self.store.remap_paths({str(source):str(target)})
        self.assertEqual(self.store.by_attachment(target)['subject'],'9월 급여명세서')
        self.assertIsNone(self.store.by_attachment(source))
        self.assertEqual(self.store.attachment_roots(),[])
        target.rename(source)
        self.store.remap_paths({str(target):str(source)})
        self.assertTrue(self.store.by_attachment(source)['available'])
        source.rename(target)
        self.store.remap_paths({str(source):str(target)})
        self.store.clear_cache(self.profile['id'])
        self.assertTrue(target.exists())

    def test_disconnected_profile_cannot_be_written_by_inflight_job(self):
        self.store.disconnect(self.profile['id'])
        with self.assertRaises(ValueError):
            self.store.add_message(self.profile['id'],'key',{},[('a.txt',b'fixture')])

    def test_provider_fixed_hosts_and_microsoft_basic_auth_refused(self):
        self.assertEqual(profile_values('works','fixture@example.test')['host'],'imap.worksmobile.com')
        self.assertEqual(profile_values('gmail','fixture@gmail.com','evil.example')['host'],'imap.gmail.com')
        for provider,host in [('outlook',''),('custom','outlook.office365.com'),('custom','https://imap.example.test/x')]:
            with self.assertRaises(ValueError):
                profile_values(provider,'fixture@example.test',host)
        self.assertEqual(webmail_url('https://evil.example'),'')

    def test_worker_import_payload_no_gui_dependencies(self):
        from mail_connections_ui import _worker
        replies = queue.Queue()
        _worker(str(self.store.data),dict(kind='eml',paths=[str(self.eml())]),replies,threading.Event())
        result,identity = replies.get_nowait()
        self.assertEqual(len(result.files),1)
        self.assertIsNone(identity)


if __name__ == '__main__':
    unittest.main()
