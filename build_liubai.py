"""Build the portable Windows Liubai distribution after building the frontend."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
if os.name != 'nt':
    raise SystemExit('Windows is required')
env = os.environ.copy()
win = Path(env.get('SystemRoot', r'C:\Windows'))
env['PATH'] = os.pathsep.join([str(Path(sys.executable).parent), str(Path(sys.base_prefix)), str(win/'System32'), str(win)])
def build(*args):
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', *args], cwd=root, env=env, check=True)
build('--windowed', '--onefile', '--name', 'LiubaiCollector', '--distpath', 'build-support/collector', '--workpath', 'build-support/collector-build', '--specpath', 'build-support', '--collect-all', 'cryptography', '--collect-all', 'zstandard', 'liubai_collector/api.py')
data = ['ui-qce;ui-qce', 'ui/viewer.html;ui', 'ui/collection.html;ui', 'ui/date-range-picker.js;ui', 'ui/archive-print.js;ui', 'ui/archive-media.js;ui', 'ui/online-config.json;ui', 'LICENSE;.', 'THIRD_PARTY_NOTICES.md;.', 'THIRD_PARTY_LICENSES;THIRD_PARTY_LICENSES', 'README-LIUBAI.md;.']
options = [item for entry in data for item in ('--add-data', entry)]
for module in ('PyQt5', 'PyQt6', 'PySide6', 'pywinauto', 'comtypes', 'tkinter', 'wx4.gui', 'wx4.cli', 'wx4.export'):
    options += ['--exclude-module', module]
for module in ('webview', 'pythonnet', 'clr_loader', 'imageio_ffmpeg', 'pysilk', 'pyzipper', 'xlsxwriter'):
    options += ['--collect-all', module]
build('--windowed', '--onedir', '--name', 'Liubai', '--icon', 'icon.ico', '--manifest', 'windows.manifest', *options, '--hidden-import', 'webview.platforms.edgechromium', '--hidden-import', 'webview.platforms.winforms', 'edge_app.py')
shutil.copy2(root/'build-support/collector/LiubaiCollector.exe', root/'dist/Liubai/LiubaiCollector.exe')
print('Distribution:', root/'dist/Liubai/Liubai.exe')
