"""Provider settings are fixed app data, never taken from message headers."""
import re

PROVIDERS = {
    'naver': dict(label='네이버 메일', host='imap.naver.com', web='https://mail.naver.com/',
                  help='https://help.naver.com/service/30029/contents/21351?osType=COMMONOS',
                  note='네이버에서 IMAP 사용을 켜고, 2단계 인증의 앱 비밀번호를 입력해 주세요.'),
    'works': dict(label='네이버웍스 (회사)', host='imap.worksmobile.com', web='https://mail.worksmobile.com/',
                  help='https://help.worksmobile.com/ko/use-guides/mail/settings/pop3-imap-smtp/',
                  note='네이버웍스 설정 → 보안에서 만든 외부 앱 비밀번호를 입력해 주세요. 회사의 IMAP 허용이 필요해요.'),
    'gmail': dict(label='Gmail', host='imap.gmail.com', web='https://mail.google.com/',
                  help='https://support.google.com/mail/answer/185833?hl=ko',
                  note='Google 2단계 인증의 앱 비밀번호를 입력해 주세요. 회사 정책에 따라 사용하지 못할 수 있어요.'),
    'custom': dict(label='다른 회사 메일 (IMAP)', host='', web='', help='',
                  note='회사에서 안내한 SSL IMAP 서버와 메일 전용 비밀번호가 필요해요. Microsoft 365는 지원하지 않아요.'),
    'outlook': dict(label='Outlook / Microsoft 365', host='', web='https://outlook.office.com/mail/',
                  help='https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online',
                  note='Microsoft 로그인 연결은 아직 지원하지 않아요. 아래에서 저장한 메일(.eml)을 가져올 수 있어요.'),
    'eml': dict(label='저장한 메일', host='', web='', help='', note=''),
}


def profile_values(provider, address='', host=''):
    if provider not in PROVIDERS or provider == 'outlook':
        raise ValueError('이 서비스는 저장한 메일(.eml)로 가져와 주세요.')
    if provider == 'eml':
        return dict(provider='eml', address='', host='', port=993)
    address = str(address).strip()
    if len(address) > 254 or not re.fullmatch(r'[^\s<>@\x00-\x1f]+@[^\s<>@\x00-\x1f]+\.[^\s<>@\x00-\x1f]+', address):
        raise ValueError('메일 주소를 확인해 주세요.')
    host = PROVIDERS[provider]['host'] or str(host).strip().lower()
    if not re.fullmatch(r'(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', host) or '..' in host:
        raise ValueError('SSL IMAP 서버 주소를 확인해 주세요.')
    if host.lower() in {'outlook.office365.com', 'imap-mail.outlook.com', 'outlook.office.com'}:
        raise ValueError('Microsoft 365는 이 방식으로 연결할 수 없어요. 저장한 메일(.eml)을 가져와 주세요.')
    return dict(provider=provider, address=address, host=host.lower(), port=993)


def webmail_url(provider):
    return PROVIDERS.get(provider, {}).get('web', '')
