"""Deterministic visual constraints for common Korean descriptions.

Unknown descriptive words survive in ``description`` rather than silently
turning a specific request into a request for every photo.
"""
import re
import unicodedata

COLORS = {'파란':'blue','파랑':'blue','파랗':'blue','푸른':'blue','파랑색':'blue','파란색':'blue',
          '빨간':'red','빨강':'red','붉은':'red','빨갛':'red','적색':'red',
          '초록':'green','녹색':'green','초록색':'green','녹색빛':'green',
          '노란':'yellow','노랑':'yellow','노랗':'yellow','황색':'yellow',
          '분홍':'pink','핑크':'pink','보라':'purple','주황':'orange',
          '검은':'black','검정':'black','검은색':'black','검정색':'black',
          '하얀':'white','하얗':'white','흰색':'white','흰':'white','회색':'gray','회색빛':'gray'}
OBJECTS = {'의자':'chair','소파':'sofa','쇼파':'sofa','책상':'desk','테이블':'table','식탁':'table',
           '강아지':'dog','고양이':'cat','자동차':'car','자전거':'bicycle',
           '꽃':'flower','나무':'tree','바다':'sea','바닷가':'sea','해변':'sea','하늘':'sky','침대':'bed',
           '사람':'person','인물':'person','가방':'bag','노트북':'laptop','컵':'cup'}
DETECTABLE={'chair':{'chair'},'sofa':{'couch'},'table':{'dining table'},'dog':{'dog'},
            'cat':{'cat'},'car':{'car'},'bicycle':{'bicycle'},'bed':{'bed'},
            'person':{'person'},'bag':{'backpack','handbag','suitcase'},'laptop':{'laptop'},'cup':{'cup'}}

BRIGHTNESS_PATTERNS = {
    'dark': r'어두(?:운|워|웠|움|운데)?|어둡|컴컴|깜깜',
    'bright': r'밝(?:은|아|았|게|음|은데)?|환한|환해|환하게',
}
SCENE_PATTERNS = {
    'indoor': r'실내|방\s*안|집\s*안|건물\s*안',
    'outdoor': r'실외|야외|바깥|밖에서|밖에|밖이',
}
SCENE_TERMS = {'해질녘':'sunset','노을':'sunset','일몰':'sunset','일출':'sunrise',
               '숲':'forest','산속':'mountains','산에서':'mountains',
               '눈밭':'snow','설경':'snow','눈 오는':'snow','비 오는':'rain',
               '공원':'park','거리':'street','도시':'city','카페':'cafe',
               '주방':'kitchen','부엌':'kitchen','거실':'living room','침실':'bedroom',
               '밤':'night','야경':'night','낮':'daytime'}
DOCUMENT_WORDS = ('문서','표지','명세서','계약서','영수증','인보이스','제안서','청구서',
                  '보고서','이력서','견적서','엑셀','스프레드시트','pdf','docx','xlsx','pptx')
PHOTO_WORDS = ('사진','이미지','그림')

# Strip a request only at its end, preserving filenames, places and dates.
REQUEST_END = re.compile(
    r'(?:찾아\s*(?:주실\s*수\s*있(?:나요|어요)|줄\s*수\s*있(?:니|어|나요)|주(?:세요|실래요|실래|면\s*좋겠어)|줘(?:요)?|볼래(?:요)?|봐(?:요)?)'
    r'|보여\s*(?:주실\s*수\s*있(?:나요|어요)|줄\s*수\s*있(?:니|어|나요)|주(?:세요|실래요|실래)|줘(?:요)?)'
    r'|검색\s*(?:해\s*(?:줘(?:요)?|주(?:세요|실래요))|해(?:요)?|해주세요)'
    r'|보고\s*싶(?:어(?:요)?|습니다)|찾고\s*싶(?:어(?:요)?|습니다))\s*$')


def normalize_photo_request(query):
    """Remove polite request framing, preserving the actual description."""
    value=unicodedata.normalize('NFKC',str(query or '')).strip()
    value=re.sub(r'[.!?。？！]+$', '', value).strip()
    value=re.sub(r'^(?:짱구(?:야)?[ ,]+|혹시\s+)', '', value)
    value=REQUEST_END.sub('', value).strip()
    value=re.sub(r'(?:\s+|^)(?:좀|부탁해(?:요)?|부탁드립니다)\s*$', '', value).strip()
    value=re.sub(r'^좀\s+', '', value)
    return re.sub(r'\s+', ' ', value)


def is_broad_photo_request(query):
    """Broad only when no content, date or place condition survives."""
    value=re.sub(r'\s+', '', normalize_photo_request(query))
    return bool(re.fullmatch(
        r'(?:(?:내|제가가진|저장된|저장한|있는|모든|전체|전부|모두|갖고있는))*'
        r'(?:사진|이미지|그림)(?:들)?(?:을|를|이|가|은|는)?'
        r'(?:(?:모두|전부|전체|다|좀))*', value))


def _last_value(patterns,query):
    hits=[(match.start(),value) for value,pattern in patterns.items()
          for match in re.finditer(pattern,query)]
    return max(hits)[1] if hits else None


def _object_excluded(word,query):
    escaped=re.escape(word)
    return bool(re.search(escaped+r'\s*(?:이|가|는|은)?\s*(?:없|안\s*(?:보이|나오)|빼|제외)', query)
                or re.search(escaped+r'\s*(?:을|를)?\s*(?:제외|빼)',query))


def visual_intent(query):
    query=unicodedata.normalize('NFKC',str(query or ''))
    colors=list(dict.fromkeys(v for k,v in COLORS.items() if k in query))
    objects=list(dict.fromkeys(v for k,v in OBJECTS.items() if k in query))
    excluded=list(dict.fromkeys(v for k,v in OBJECTS.items() if _object_excluded(k,query)))
    objects=[obj for obj in objects if obj not in excluded]
    # '파란 의자' identifies object color; '파란 배경에 의자' does not.
    object_color=bool(colors and any(re.search(re.escape(c)+r'\s*(?:색)?\s*'+re.escape(o),query)
                                   for c in COLORS for o in OBJECTS if OBJECTS[o] not in excluded))
    photo=any(w in query for w in PHOTO_WORDS)
    brightness=_last_value(BRIGHTNESS_PATTERNS,query)
    scene=_last_value(SCENE_PATTERNS,query)
    scene_terms=list(dict.fromkeys(v for k,v in SCENE_TERMS.items() if k in query))
    active=photo or bool(objects or excluded or colors or brightness or scene or scene_terms)
    document=any(w in query.casefold() for w in DOCUMENT_WORDS)
    prompt='a photo of '+(' and '.join(objects) if objects else 'a scene')
    if scene:prompt+=', '+scene+' setting'
    if scene_terms:prompt+=', '+', '.join(scene_terms)
    if colors:prompt+=' with '+' and '.join(colors)+(' objects' if object_color else ' colors')
    if brightness:prompt+=', '+('dark low-light' if brightness=='dark' else 'bright well-lit')
    if excluded:prompt+=', without '+' or '.join(excluded)
    return dict(active=active and not document,photo_only=photo and not document,
                colors=colors,objects=objects,excluded_objects=excluded,object_color=object_color,
                broad=is_broad_photo_request(query),brightness=brightness,scene=scene,scene_terms=scene_terms,
                description=normalize_photo_request(query),
                color_min=.25 if any(w in query for w in ('전반','전체','대부분','온통')) else .08,
                prompt=prompt)
