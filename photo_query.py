"""Apply bounded photo follow-up conditions without altering document searches."""
import re

from visual_query import (COLORS,OBJECTS,BRIGHTNESS_PATTERNS,SCENE_PATTERNS,
                          REQUEST_END,normalize_photo_request,visual_intent)

_PREFIX=re.compile(r'^\s*(?:(?:그\s*중(?:에서)?|거기서|여기서|이\s*결과(?:에서)?|'
                   r'이(?:것|거)(?:보다|는|에서)?|그(?:것|거)(?:보다|는)?|'
                   r'아니(?:야)?|말고|그러면|그럼|조금\s*더|좀\s*더|더(?=\s|어두|밝))[,\s]*)+')


def is_photo_followup(query,previous):
    if not str(previous or '').strip() or not visual_intent(previous)['active']:
        return False
    intent=visual_intent(query)
    if not intent['active'] or intent['broad']:
        return False
    if _PREFIX.match(str(query or '')):
        return True
    # A full new request such as '고양이 사진 찾아줘' resets context.
    if REQUEST_END.search(str(query or '').strip().rstrip('.!?。？！')):
        return False
    return bool(intent['brightness'] or intent['scene'] or intent['excluded_objects']
                or (intent['colors'] and not intent['photo_only'])
                or re.search(r'(?:이|가|은|는)?\s*있(?:는|어|었|고)',str(query or '')))


def _strip_patterns(text,patterns):
    for pattern in patterns:text=re.sub(pattern,'',text)
    return text


def _strip_object_condition(text,obj):
    aliases=sorted((word for word,value in OBJECTS.items() if value==obj),key=len,reverse=True)
    if not aliases:return text
    pattern='(?:'+'|'.join(re.escape(word) for word in aliases)+')'
    return re.sub(pattern+r'\s*(?:(?:이|가|는|은|을|를)\s*)?'
                  r'(?:(?:없|있)(?:었어요|었어|어요|는|어|고|음)?|안\s*(?:보이는|보여|나오는|나와)|'
                  r'빼(?:고|줘)?|제외(?:한|하고)?)?', '', text)


def resolve_photo_query(query,previous):
    """Return a search query with short photo corrections applied to context."""
    query=str(query or '').strip()
    if not is_photo_followup(query,previous):return query
    old=normalize_photo_request(previous)
    new=_PREFIX.sub('',normalize_photo_request(query)).strip()
    intent=visual_intent(query)
    if intent['colors']:
        # New colors replace old colors, rather than requiring both at once.
        old=re.sub('|'.join(re.escape(c) for c in sorted(COLORS,key=len,reverse=True)),'',old)
    if intent['brightness']:old=_strip_patterns(old,BRIGHTNESS_PATTERNS.values())
    if intent['scene']:old=_strip_patterns(old,SCENE_PATTERNS.values())
    for obj in set(intent['objects']+intent['excluded_objects']):old=_strip_object_condition(old,obj)
    old=re.sub(r'(?<![가-힣])(?:어|어요|야|이야|였어|인데|게|색)(?![가-힣])','',old)
    combined=re.sub(r'\s+',' ',old+' '+new).strip()
    if not any(word in combined for word in ('사진','이미지','그림')):combined+=' 사진'
    return combined
