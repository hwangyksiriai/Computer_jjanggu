"""Real, offline photo-search smoke test for source and packaged execution.

Only the five public fixtures listed below are copied. User photos, existing
catalogues and organizer folders are never used or changed. No fake AI results
or readiness patches are involved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback
import uuid


FIXTURES = (
    '000000000139.jpg',  # Indoor dining room, chairs and a person.
    '000000000285.jpg',  # Bear outside; no person/chair.
    '000000000785.jpg',  # Person skiing outside; no chair.
    '000000039769.jpg',  # Two cats on a couch; no person/chair.
    'blue-chair.jpg',   # Blue/turquoise chair; no person.
)
SOURCES = [
    {'name': 'COCO validation images',
     'url': 'https://cocodataset.org/#download',
     'files': list(FIXTURES[:4])},
    {'name': 'Bluechair.jpg by karol m, CC BY 2.0',
     'url': 'https://commons.wikimedia.org/wiki/File:Bluechair.jpg',
     'files': ['blue-chair.jpg']},
]


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(report, fixture_dir=None):
    """Write a JSON report and return whether all real-model checks passed.

    ``fixture_dir`` must contain the five named public images. The default is
    the source workspace's .local/visual-benchmark directory. Models must have
    already been downloaded into the configured runtime; this test does not
    install or fetch anything. TEMP/TMP control the isolated test directory.
    """
    destination=Path(report).resolve()
    started=time.monotonic()
    result={'ok': False, 'kind': 'real offline photo search', 'checks': [],
            'sources': SOURCES, 'queries': [], 'warnings': [
                'Five selected public photos are a regression sample, not a general accuracy measurement.',
                'This verifies the actual model and catalogue pipeline, not novice usability or physical keyboard input.',
            ]}
    ai=None

    def check(name, passed, **details):
        result['checks'].append(dict(name=name,ok=bool(passed),**details))

    try:
        import bootstrap  # Adds source-install dependencies; no-op for bundled imports.
        from app_paths import BASE
        from app_version import VERSION
        from local_ai import LocalAI
        from photo_library import PhotoLibrary, photo_roots

        result['version']=VERSION
        result['frozen']=bool(getattr(sys,'frozen',False))
        fixtures=Path(fixture_dir).resolve() if fixture_dir else BASE/'.local/visual-benchmark'
        missing=[name for name in FIXTURES if not (fixtures/name).is_file()]
        check('all five public fixtures are available',not missing,missing=missing)
        if missing:raise RuntimeError('Public photo fixtures are missing: '+', '.join(missing))
        originals={name:_digest(fixtures/name) for name in FIXTURES}
        ai=LocalAI()
        capabilities={name:ai.ready_for(name) for name in ('vision','objects','photo')}
        result['capabilities']=capabilities
        check('real photo models are locally ready',all(capabilities.values()))
        if not all(capabilities.values()):
            raise RuntimeError('Configure and prepare the local photo models before running this check.')

        with tempfile.TemporaryDirectory(prefix='jjanggu-real-photo-') as folder:
            temporary=Path(folder).resolve()
            sample_home=temporary/'sample-user'
            common=('Desktop','Downloads','Pictures','Documents')
            for name in common:(sample_home/name).mkdir(parents=True)
            photo_by_path={}
            copied={}
            for number,name in enumerate(FIXTURES):
                # The only filenames visible to search carry no descriptive clue.
                nested=sample_home/common[number%len(common)]/uuid.uuid4().hex[:12]
                nested.mkdir()
                target=nested/(uuid.uuid4().hex+'.jpg')
                shutil.copy2(fixtures/name,target)
                photo_by_path[str(target.resolve())]=name
                copied[name]=target
            roots=photo_roots(home=sample_home)
            check('common folders are discovered without providing individual paths',
                  {p.name for p in roots}==set(common),folders=sorted(p.name for p in roots))
            check('every searchable filename is unrelated to picture content',
                  all(len(path.stem)==32 and all(c in '0123456789abcdef' for c in path.stem)
                      for path in copied.values()))

            library=PhotoLibrary(temporary/'catalogue',ai)
            phases=[]
            def progress(state):
                phase=state['phase']
                if not phases or phases[-1]!=phase:phases.append(phase)
            scan_started=time.monotonic()
            state=library.scan(roots,progress=progress)
            result['scan_seconds']=round(time.monotonic()-scan_started,3)
            result['scan_phases']=phases
            check('scan finishes through real image and object inference',
                  state.get('phase')=='complete' and not library.ai_error,
                  phase=state.get('phase'),error=library.ai_error)
            coverage=library.coverage(roots)
            result['coverage']={name:coverage[name] for name in
                ('registered','images','visual','detected','thumbnails','unreadable','offline')}
            check('all five photos are visible, thumbnailed and fully analysed',
                  all(coverage[name]==len(FIXTURES) for name in
                      ('registered','images','visual','detected','thumbnails'))
                  and coverage['unreadable']==0 and coverage['offline']==0,
                  **result['coverage'])
            check('scan publishes names before expensive model analysis',
                  phases==['catalog','thumbnails','analysis','complete'],phases=phases)

            def query_check(text,expected):
                began=time.monotonic()
                rows,interpreted=library.smart_search(text,roots)
                actual=sorted(photo_by_path.get(row['path'],'UNEXPECTED FILE') for row in rows)
                expected=sorted(expected)
                entry={'query':text,'interpreted_query':interpreted,'expected':expected,
                       'actual':actual,'ok':actual==expected and not library.ai_error,
                       'seconds':round(time.monotonic()-began,3),'error':library.ai_error,
                       'notice':getattr(library,'search_notice','')}
                result['queries'].append(entry)
                check('query: '+text,entry['ok'])
                return actual

            broad_results=[]
            for text in ('사진 찾아줘','사진 좀 보여줘','사진 찾아주세요',
                         '사진 보여줘','사진 보고 싶어','내 사진 모두 보여줘'):
                broad_results.append(query_check(text,FIXTURES))
            check('equivalent broad requests return identical photos',
                  all(found==broad_results[0] for found in broad_results))
            query_check('파란 의자 사진 찾아줘',['blue-chair.jpg'])
            query_check('고양이 사진 찾아줘',['000000039769.jpg'])
            query_check('의자가 있는 사진 찾아줘',['000000000139.jpg','blue-chair.jpg'])
            query_check('사람 없는 사진 찾아줘',
                        ['000000000285.jpg','000000039769.jpg','blue-chair.jpg'])
            query_check('의자 사진 중 사람은 제외해줘',['blue-chair.jpg'])
            result['actual_ai_requests']=ai.requests
            check('real inference requests were sent',ai.requests>=2*len(FIXTURES),count=ai.requests)
            check('search and analysis preserve the copied source files',
                  all(path.is_file() and _digest(path)==originals[name] for name,path in copied.items()))

        check('original public fixture files were left unchanged',
              all(_digest(fixtures/name)==originals[name] for name in FIXTURES))
        result['ok']=all(item['ok'] for item in result['checks'])
    except Exception as error:
        result['error']=str(error)
        result['traceback']=traceback.format_exc()
        result['ok']=False
    finally:
        if ai is not None:
            try:ai.close()
            except Exception as error:
                result['cleanup_error']=str(error);result['ok']=False
        result['elapsed_seconds']=round(time.monotonic()-started,3)
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result['ok']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',help='JSON report output path')
    parser.add_argument('fixture_dir',nargs='?',help='Directory containing the five public benchmark photos')
    args=parser.parse_args()
    raise SystemExit(0 if run(args.report,args.fixture_dir) else 1)
