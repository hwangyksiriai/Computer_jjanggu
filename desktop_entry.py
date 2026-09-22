"""Windows entry point with a readable startup failure instead of a disappearing window."""
import traceback
from pathlib import Path
import tempfile
import sys

# State is external to a single-file executable's temporary extraction directory.
if '--data-dir' in sys.argv:
    import os
    i=sys.argv.index('--data-dir')
    if i+1<len(sys.argv):os.environ['JJANGGU_DATA_DIR']=sys.argv[i+1]

if __name__ == '__main__':
    try:
        import multiprocessing
        multiprocessing.freeze_support()
        if sys.argv[1:] == ['--play-voice']:
            from voice_clips import play_request
            play_request()
        elif len(sys.argv) == 3 and sys.argv[1] == '--self-test':
            import json
            from verify_easy import run
            from unittest.mock import patch
            from local_ai import LocalAI
            with patch.object(LocalAI,'ready',return_value=False),patch.object(LocalAI,'ready_for',return_value=False),patch.object(LocalAI,'detector_ready',return_value=False):result=run()
            Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        elif len(sys.argv) in (3,4) and sys.argv[1]=='--self-test-photo':
            from verify_photo import run
            raise SystemExit(0 if run(sys.argv[2],sys.argv[3] if len(sys.argv)==4 else None) else 1)
        else:
            from app import main
            main()
    except Exception:
        log = Path(tempfile.gettempdir()) / 'jjanggu-startup-error.txt'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            '짱구를 열지 못했어요. 앱을 다시 실행해 주세요.\n\n'
            f'계속 안 되면 아래 오류 기록을 전달해 주세요.\n{log}', '실행 확인', 0x10)
        raise SystemExit(1)
