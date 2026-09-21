"""Windows entry point with a readable startup failure instead of a disappearing window."""
import traceback
from pathlib import Path
import tempfile

if __name__ == '__main__':
    try:
        import sys
        if sys.argv[1:] == ['--play-voice']:
            from voice_clips import play_request
            play_request()
        elif len(sys.argv) == 3 and sys.argv[1] == '--self-test':
            import json
            from verify_easy import run
            Path(sys.argv[2]).write_text(json.dumps(run(), ensure_ascii=False), encoding='utf-8')
        else:
            from app import main
            main()
    except Exception:
        log = Path(tempfile.gettempdir()) / 'jjanggu-startup-error.txt'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            '짱구를 열지 못했어요. ZIP 파일 전체를 압축 해제한 뒤 다시 실행해 주세요.\n\n'
            f'계속 안 되면 아래 오류 기록을 전달해 주세요.\n{log}', '실행 확인', 0x10)
        raise SystemExit(1)
