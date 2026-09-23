# 메일 첨부파일 가져오기

앱의 **메일 첨부파일**에서 메일 서비스를 고르고 주소와 앱 비밀번호를 입력한 뒤 **첨부파일 가져오기**를 누르세요. 비밀번호는 채팅창에 보내지 않습니다.

| 서비스 | 준비할 것 |
| --- | --- |
| 네이버 | IMAP 사용 설정, 2단계 인증, 애플리케이션 비밀번호 |
| 네이버웍스 | 회사의 IMAP 사용 허용, 설정 → 보안의 외부 앱 비밀번호 |
| Gmail | 2단계 인증의 앱 비밀번호. 계정·회사 정책에 따라 사용할 수 없습니다. |
| 다른 회사 메일 | 회사가 안내한 SSL IMAP 서버와 전용 비밀번호 |
| Outlook / Microsoft 365 | 현재 직접 로그인은 지원하지 않습니다. 저장한 `.eml` 메일을 가져올 수 있습니다. |

**저장한 메일(.eml) 가져오기**는 로그인 없이 메일 파일의 첨부파일을 가져옵니다. 파일의 보낸 사람·제목·날짜를 확인할 수 있고, 가져온 첨부파일이 앱 검색 대상이 됩니다. 본문에 있는 대용량 파일 다운로드 링크는 따라가지 않습니다.

연결 시 최근 90일의 받은 편지함에서 최대 100통을 확인합니다. 자동으로 주기적인 접속은 하지 않습니다. 보낸편지함이나 다른 폴더를 읽지 않으며, 서버의 원본 메일·읽음 표시를 바꾸거나 메일을 보내지 않습니다.

한 메일은 25MB, 첨부파일 하나는 15MB, 메일 한 통의 첨부파일은 30개까지 가져옵니다. 문서·이미지 형식만 보관하며 실행파일·스크립트·HTML·압축파일은 건너뜁니다. 첨부파일을 자동 실행하지 않습니다. 계정당 250MB 또는 2,000개까지 보관하고 가득 차면 멈춥니다. 이미 가져온 메일은 중복 저장하지 않습니다.

**연결 해제**는 저장한 비밀번호를 지우고 새 가져오기를 중단합니다. 이미 가져온 파일은 검색할 수 있습니다. **이 컴퓨터의 첨부파일 비우기**를 눌러 확인하면 앱 보관함의 사본과 출처 참조를 지웁니다. 원본 메일, 선택했던 `.eml`, 보관함 밖으로 직접 정리한 사본은 지우지 않습니다.

비밀번호 저장은 기본으로 꺼져 있습니다. 선택하면 현재 Windows 사용자에게 묶인 DPAPI로 암호화합니다. 같은 Windows 계정으로 실행되는 프로그램에 대한 방어까지 보장하는 비밀 저장소는 아닙니다. 앱은 비밀번호나 서버의 원문 오류를 기록하지 않습니다.

메일에서 직접 가져온 파일에는 메일함 사이트로 가는 버튼이 표시될 수 있습니다. 이는 특정 메일을 바로 여는 링크가 아닙니다. `.eml`로 가져왔으면 선택했던 원본 메일의 위치를 표시합니다.

## 개발 연결

- `MailStore(data).attachments(limit=2000)`, `attachment_count()`, `by_attachment(path)`는 출처 조회용입니다.
- `attachment_roots()`는 내부 보관함의 계정 폴더만 반환합니다.
- `remap_paths({source: destination})`에는 이번에 성공한 이동만 전달합니다. 되돌리기는 역방향 매핑을 전달합니다.
- `show_mail_connections(app)`는 창을 재사용합니다. 작업 완료 후 UI 스레드에서 `app.mail_attachments_changed()`를 호출합니다. `app.show_mail_attachments()`는 파일 목록 버튼에 연결됩니다.
- IMAP은 인증서·호스트 이름을 검증하는 SSL, 읽기 전용 INBOX 선택, UIDVALIDITY+UID 중복 구분, `BODY.PEEK[]`를 사용합니다. 서버 쓰기 명령은 구현하지 않습니다.
- GUI worker에는 Tk 위젯·앱·StringVar를 전달하지 않습니다. 닫기 시 취소하고, 먼저 저장된 파일은 메인 스레드의 큐 처리에서 검색에 반영합니다.
- Microsoft OAuth를 추가하려면 Microsoft Entra 앱 등록 및 공개 클라이언트 인증 설정, `IMAP.AccessAsUser.All` 위임 권한, 해당 조직이 요구하는 동의가 필요합니다. 앱 등록 없이 Microsoft 기본 비밀번호 로그인을 대체 기능으로 제공하지 않습니다.

## 확인한 공식 문서 (2026-09-23)

- [네이버 IMAP SSL 서버와 사용 설정](https://help.naver.com/service/30029/contents/21351?osType=COMMONOS)
- [네이버 앱 비밀번호 필수 전환 안내](https://help.naver.com/notice/noticeView.help?lang=ko&noticeNo=27581&page=1&serviceNo=5608)
- [네이버웍스 IMAP 및 외부 앱 비밀번호](https://help.worksmobile.com/ko/use-guides/mail/settings/pop3-imap-smtp/)
- [Google 앱 비밀번호 조건](https://support.google.com/mail/answer/185833?hl=ko)
- [Microsoft 365 기본 인증 중단](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online)
- [Microsoft IMAP OAuth 앱 등록과 권한](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)
- [Python imaplib 읽기 전용 선택과 SSL](https://docs.python.org/3/library/imaplib.html)
