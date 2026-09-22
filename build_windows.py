"""Build a portable Windows app with its own Python; no end-user command prompt."""
from pathlib import Path
import subprocess
import sys
import argparse
import shutil
import tempfile

BASE = Path(__file__).resolve().parent

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('destination'); parser.add_argument('--runtime',type=Path)
    parser.add_argument('--node',type=Path); parser.add_argument('--onedir',action='store_true')
    args=parser.parse_args(); destination=Path(args.destination).resolve()
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--windowed', '--onedir' if args.onedir else '--onefile', '--name', '짱구주머니',
               '--distpath', str(destination), '--workpath', str(destination.parent / 'pyinstaller-work'),
               '--specpath', str(destination.parent), '--collect-all', 'tkinterdnd2',
               '--collect-all', 'imageio_ffmpeg', '--exclude-module', 'torch',
               '--exclude-module', 'transformers','--exclude-module','matplotlib','--exclude-module','scipy']
    if args.runtime:command+=['--paths',str(args.runtime/'vendor')]
    for name in ['assets', 'speech.ps1', 'ocr.ps1','ai_worker.mjs','package.json','package-lock.json','qwen_voice_worker.py','app_paths.py']:
        command += ['--add-data', f'{BASE / name};{name if name == "assets" else "."}']
    if args.node and args.runtime:
        command+=['--add-binary',f'{args.node};ai-runtime']
        modules=args.runtime/'node_modules'
        destination.parent.mkdir(parents=True,exist_ok=True)
        staged=Path(tempfile.mkdtemp(prefix='ai-runtime-',dir=destination.parent))/'node_modules'
        # Keep only the Windows x64 native libraries. Models are installed later.
        for path in modules.rglob('*'):
            if not path.is_file():continue
            relative=path.relative_to(modules); normalized=relative.as_posix()
            if any(p in relative.parts for p in ('darwin','linux','arm64')):continue
            if '/bin/napi-v6/' in normalized and '/win32/x64/' not in normalized:continue
            target=staged/relative; target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
        command+=['--add-data',f'{staged};ai-runtime/node_modules']
    command.append(str(BASE / 'desktop_entry.py'))
    subprocess.run(command, cwd=BASE, check=True)
