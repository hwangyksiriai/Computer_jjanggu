"""Build a portable Windows app with its own Python; no end-user command prompt."""
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent

if __name__ == '__main__':
    destination = Path(sys.argv[1]).resolve()
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--windowed', '--onedir', '--name', '짱구주머니',
               '--distpath', str(destination), '--workpath', str(destination.parent / 'pyinstaller-work'),
               '--specpath', str(destination.parent), '--collect-all', 'tkinterdnd2',
               '--collect-all', 'imageio_ffmpeg', '--exclude-module', 'torch',
               '--exclude-module', 'transformers']
    for name in ['assets', 'speech.ps1', 'ocr.ps1']:
        command += ['--add-data', f'{BASE / name};{name if name == "assets" else "."}']
    command.append(str(BASE / 'desktop_entry.py'))
    subprocess.run(command, cwd=BASE, check=True)
