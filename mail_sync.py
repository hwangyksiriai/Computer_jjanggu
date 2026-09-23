"""Read-only INBOX ingestion and explicit local .eml import. No SMTP or STORE."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.errors import MessageError
from email.parser import BytesParser
import hashlib
import imaplib
from pathlib import Path
import re
import ssl

from mail_providers import profile_values
from mail_store import MailStore, _safe_chain, clean_text, safe_filename

MAX_MESSAGE = 25 * 1024 * 1024
MAX_ATTACHMENT = 15 * 1024 * 1024
MAX_HEADERS = 64 * 1024
MAX_PARTS = 200
MAX_ATTACHMENTS = 30
MAX_MESSAGES = 100
MAX_SEARCH_BYTES = 1024 * 1024
ALLOWED_EXTENSIONS = {'.pdf', '.txt', '.csv', '.tsv', '.doc', '.docx', '.xls', '.xlsx',
                      '.ppt', '.pptx', '.hwp', '.hwpx', '.odt', '.ods', '.odp', '.rtf',
                      '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff'}


@dataclass
class ImportResult:
    files: list = field(default_factory=list)
    messages: int = 0
    duplicates: int = 0
    skipped: int = 0
    cancelled: bool = False
    error: str = ''

    def summary(self):
        if self.error:
            return self.error + (f' 그전에 가져온 파일 {len(self.files)}개는 보관했어요.' if self.files else '')
        if self.cancelled:
            return f'가져오기를 멈췄어요. 파일 {len(self.files)}개를 보관했어요.'
        text = f'첨부파일 {len(self.files)}개를 가져왔어요.'
        if self.skipped:
            text += f' 크기·형식 등의 이유로 {self.skipped}개는 건너뛰었어요.'
        if not self.files and self.duplicates and not self.skipped:
            text = '이미 가져온 메일이에요. 새 첨부파일이 없어요.'
        return text


class BoundedIMAP(imaplib.IMAP4_SSL):
    """Reject an oversized server literal before imaplib allocates it."""
    def read(self, size):
        if size > MAX_MESSAGE + 1:
            raise ValueError('메일이 너무 커서 가져오지 못했어요.')
        return super().read(size)


def parse_message(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_MESSAGE:
        raise ValueError('메일 한 통이 25MB를 넘어 건너뛰었어요.')
    # Bound the header block before MIME parsing and prevent excessive MIME fan-out.
    boundary = raw.find(b'\r\n\r\n')
    if boundary < 0:
        boundary = raw.find(b'\n\n')
    if boundary < 0 or boundary > MAX_HEADERS or raw.lower().count(b'\ncontent-type:') > MAX_PARTS:
        raise ValueError('메일 형식을 읽을 수 없어요.')
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        metadata = dict(sender=clean_text(message.get('From')), subject=clean_text(message.get('Subject')),
                        date=clean_text(message.get('Date'), 100), message_id=clean_text(message.get('Message-ID'), 400))
        files, skipped = [], 0
        for number, part in enumerate(message.walk()):
            if number >= MAX_PARTS:
                raise ValueError('첨부파일 구성이 너무 많아 가져오지 못했어요.')
            if sum(len(str(k))+len(str(v)) for k, v in part.raw_items()) > MAX_HEADERS:
                raise ValueError('메일 형식을 읽을 수 없어요.')
            if part.is_multipart():
                continue
            filename = part.get_filename()
            if not filename and part.get_content_disposition() != 'attachment':
                continue
            if not filename or len(files) >= MAX_ATTACHMENTS:
                skipped += 1
                continue
            name = safe_filename(filename)
            if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
                skipped += 1
                continue
            # The total encoded message is already capped; decode one part at a time.
            payload = part.get_payload(decode=True)
            if not payload or len(payload) > MAX_ATTACHMENT:
                skipped += 1
                continue
            files.append((name, payload))
        return metadata, files, skipped
    except (RecursionError, UnicodeError, TypeError, LookupError, MessageError) as error:
        raise ValueError('메일 형식을 읽을 수 없어요.') from error


def import_eml(store, paths, cancelled=lambda: False):
    result = ImportResult()
    profile = store.save_profile('eml')
    for number, path in enumerate(paths):
        if cancelled():
            result.cancelled = True
            break
        if number >= MAX_MESSAGES:
            result.skipped += 1
            continue
        try:
            path = _safe_chain(Path(path))
            if path.suffix.lower() != '.eml' or not path.is_file():
                raise ValueError('저장한 메일(.eml)을 선택해 주세요.')
            with path.open('rb') as stream:
                raw = stream.read(MAX_MESSAGE + 1)
            key = 'eml:' + hashlib.sha256(raw).hexdigest()
            if store.seen(profile['id'], key):
                result.duplicates += 1
                continue
            metadata, files, skipped = parse_message(raw)
            metadata['source_eml'] = str(path)
            result.skipped += skipped
            result.files.extend(store.add_message(profile['id'], key, metadata, files, cancelled))
            result.messages += 1
        except InterruptedError:
            result.cancelled = True
            break
        except OSError:
            result.skipped += 1
        except ValueError as error:
            # Local validation errors contain no remote response or credentials.
            result.skipped += 1
            result.error = str(error)
            if '보관함이 찼' in result.error:
                break
        except Exception:
            result.error = '메일을 읽지 못했어요. 저장한 메일 파일을 확인해 주세요.'
            break
    return result


def _ok(response):
    kind, data = response
    if kind != 'OK':
        raise imaplib.IMAP4.error('Command failed')
    return data


def _uidvalidity(connection):
    kind, values = connection.response('UIDVALIDITY')
    if kind != 'UIDVALIDITY' or not values or not isinstance(values[0], bytes) or not re.fullmatch(rb'[1-9][0-9]{0,19}', values[0]):
        raise ValueError('메일함의 고유 번호를 확인하지 못했어요. 다시 연결해 주세요.')
    return values[0].decode('ascii')


def sync_profile(store, profile, secret, cancelled=lambda: False, imap_factory=BoundedIMAP):
    """Only EXAMINE INBOX, UID SEARCH/FETCH(Peek), and LOGOUT are permitted."""
    result, connection = ImportResult(), None
    try:
        values = profile_values(profile['provider'], profile['address'], profile['host'])
        current = store.profile(profile['id'])
        if values['provider'] == 'eml' or not current or not current['enabled']:
            raise ValueError('연결할 메일 주소를 선택해 주세요.')
        if any(values[key] != current[key] for key in ('provider','address','host','port')):
            raise ValueError('메일 연결 정보를 다시 선택해 주세요.')
        if not isinstance(secret, str) or not secret or len(secret) > 4096 or any(c in secret for c in '\r\n\0'):
            raise ValueError('앱 비밀번호를 입력해 주세요.')
        if cancelled():
            result.cancelled = True
            return result
        connection = imap_factory(values['host'], port=993, ssl_context=ssl.create_default_context(), timeout=20)
        # Socket operations, including authentication/FETCH, retain a finite timeout.
        if getattr(connection, 'sock', None) is not None:
            connection.sock.settimeout(20)
        _ok(connection.login(values['address'], secret))
        secret = ''
        if cancelled():
            result.cancelled = True
            return result
        _ok(connection.select('INBOX', readonly=True))
        validity = _uidvalidity(connection)
        since = datetime.now(timezone.utc) - timedelta(days=90)
        # English month names independent of the user's Windows locale.
        month = ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')[since.month-1]
        data = _ok(connection.uid('SEARCH', None, 'SINCE', f'{since.day:02d}-{month}-{since.year}'))
        raw_uids = b' '.join(item for item in data if isinstance(item, bytes))
        if len(raw_uids) > MAX_SEARCH_BYTES:
            raise ValueError('받은 메일이 너무 많아 한 번에 확인하지 못했어요. 저장한 메일로 가져와 주세요.')
        uids = raw_uids.split()
        if any(not re.fullmatch(rb'[1-9][0-9]{0,19}', uid) for uid in uids):
            raise ValueError('메일 목록을 읽을 수 없어요.')
        uids = sorted(set(uids), key=int)[-MAX_MESSAGES:]
        for uid in reversed(uids):
            if cancelled():
                result.cancelled = True
                break
            uid = uid.decode('ascii')
            key = f'INBOX:{validity}:{uid}'
            if store.seen(profile['id'], key):
                result.duplicates += 1
                continue
            metadata = _ok(connection.uid('FETCH', uid, '(UID RFC822.SIZE)'))
            text = b' '.join(value for value in metadata if isinstance(value, bytes))
            size_match = re.search(rb'RFC822\.SIZE\s+(\d+)', text, re.I)
            uid_match = re.search(rb'\bUID\s+(\d+)', text, re.I)
            if not size_match or not uid_match or uid_match[1].decode() != uid:
                result.skipped += 1
                continue
            if int(size_match[1]) > MAX_MESSAGE:
                result.skipped += 1
                continue
            data = _ok(connection.uid('FETCH', uid, f'(UID BODY.PEEK[]<0.{MAX_MESSAGE+1}>)'))
            literals = [part for part in data if isinstance(part, tuple) and len(part) == 2 and isinstance(part[1], bytes)]
            if len(literals) != 1 or len(literals[0][1]) != int(size_match[1]):
                result.skipped += 1
                continue
            # Servers may place UID after the body literal despite request order.
            envelope = b' '.join(item[0] if isinstance(item, tuple) else item
                                 for item in data if isinstance(item, (tuple, bytes)))
            literal_uid = re.search(rb'\bUID\s+(\d+)', envelope, re.I)
            if not literal_uid or literal_uid[1].decode() != uid:
                result.skipped += 1
                continue
            try:
                message, files, skipped = parse_message(literals[0][1])
            except ValueError:
                result.skipped += 1
                continue
            result.skipped += skipped
            result.files.extend(store.add_message(profile['id'], key, message, files, cancelled))
            result.messages += 1
    except InterruptedError:
        result.cancelled = True
    except imaplib.IMAP4.error:
        result.error = '연결하지 못했어요. 앱 비밀번호와 메일의 IMAP 사용 설정을 확인해 주세요.'
    except (OSError, ssl.SSLError):
        result.error = '메일 서버에 연결하지 못했어요. 인터넷과 회사 메일 설정을 확인해 주세요.'
    except ValueError as error:
        result.error = str(error)
    except Exception:
        result.error = '메일을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.'
    finally:
        secret = ''
        if connection is not None:
            # Never CLOSE/EXPUNGE/STORE; LOGOUT does not write flags.
            try:
                connection.logout()
            except Exception:
                try:
                    connection.shutdown()
                except Exception:
                    pass
    return result
