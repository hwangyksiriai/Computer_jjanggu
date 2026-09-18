"""Honest, deterministic reactions for supported user requests."""
import re

def quick_intent(text):
    q=re.sub(r'\s+','',text)
    if any(w in q for w in ('음소거','소리꺼','조용히')): return 'QUIET'
    if any(w in q for w in ('찾','검색','보여줘')) and not any(w in q for w in ('옷장','우리방','트레이')): return 'SEARCH'
    for intent,words in [('CLEAN',('정리해','정리좀','정리도와','치워')),('CLOSET',('꾸미','꾸며','옷바','모자바','옷장')),
                         ('DANCE',('춤춰','춤춰줘','춤추자','춤추어','훌라')),('TRAY',('트레이',)),('ROOM',('우리방',)),('LAUNCHER',('앱목록','프로그램목록'))]:
        if any(w in q for w in words): return intent
    if any(w in q for w in ('인보이스','명세서','계약서','파일','그중','사진','#')): return 'SEARCH'
    return None

REACTIONS={
 'SEARCH':('search','알았어! 돋보기 들고 찾아볼게!'),
 'CLEAN':('clean','좋아! 정리할 파일부터 챙겨볼게!'),
 'CHAT':('think','응, 들었어! 잠깐 생각해 볼게!'),
 'CLOSET':('dress','좋아! 어떤 옷이 어울릴까?'),
 'DANCE':('dance','좋아! 신나게 춤춰볼까?'),
 'QUIET':('quiet','알았어, 조용히 있을게.'),
 'TRAY':('carry','맡겨 둔 파일을 꺼내볼게!'),
 'ROOM':('room','우리 방으로 가자!'),
 'LAUNCHER':('launch','사용할 앱을 골라볼까?')}
COMPLETIONS={
 'CLEAN':('정리 안내를 확인해 줘. 파일은 확인 후에 옮길게.',1),
 'CLOSET':('꾸미기 화면을 열었어! 마음에 드는 옷을 골라줘.',0),
 'DANCE':('훌라훌라! 같이 춤추자!',2),
 'QUIET':('알았어, 조용히 있을게.',None),
 'TRAY':('작업 트레이를 열었어! 맡겨 둔 파일을 확인해 봐.',3),
 'ROOM':('우리 방에 왔어! 가구를 눌러 봐.',0),
 'LAUNCHER':('앱 목록을 열었어! 사용할 앱을 골라줘.',0)}

ACTIVITY_LABELS={'search':'돋보기 들고 찾는 중!','clean':'파일 챙기는 중!','think':'음… 생각하는 중!',
 'dress':'어떤 옷이 좋을까?','dance':'훌라훌라 준비!','quiet':'쉿, 조용히!','carry':'파일을 꺼내볼게!',
 'room':'우리 방으로 가자!','launch':'앱을 골라볼까?'}
