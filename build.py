"""Build the standalone Windows directory; run after the frontend build."""
import subprocess
import os
import sys
import shutil
import json
from pathlib import Path
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtSvg import QSvgRenderer
from PyInstaller.utils.win32.versioninfo import VSVersionInfo,FixedFileInfo,StringFileInfo,StringTable,StringStruct,VarFileInfo,VarStruct
from core.version import VERSION

root=Path(__file__).parent
app=QApplication([])
image=QImage(256,256,QImage.Format_ARGB32);image.fill(0)
painter=QPainter(image);QSvgRenderer(str(root/'ui'/'icon.svg')).render(painter);painter.end()
icon=root/'icon.ico';image.save(str(icon))
shutil.copy2(icon,root/'ui-qce/icon.ico')
version=tuple(int(part) for part in VERSION.split('.'))+(0,)
version_file=root/'version_info.txt'
info=VSVersionInfo(ffi=FixedFileInfo(filevers=version,prodvers=version,mask=0x3f,flags=0,
    OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[
    StringFileInfo([StringTable('080404b0',[
        StringStruct('FileDescription','Moect'),StringStruct('ProductName','Moect'),
        StringStruct('FileVersion',VERSION),StringStruct('ProductVersion',VERSION),
        StringStruct('InternalName','Moect'),StringStruct('OriginalFilename','Moect.exe')])]),
    VarFileInfo([VarStruct('Translation',[0x0804,1200])])])
version_file.write_text(str(info),encoding='utf-8')
# Dependency scanning must not pick unrelated ICU/OpenSSL DLLs from other
# applications on PATH. Qt uses Windows' system ICU API on this platform.
env=os.environ.copy()
win=Path(env.get('SystemRoot',r'C:\Windows'))
env['PATH']=os.pathsep.join([str(Path(sys.executable).parent),str(Path(sys.base_prefix)),
                            str(win/'System32'),str(win)])
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
               '--name','Moect','--icon',str(icon),
               '--manifest',str(root/'windows.manifest'),
               '--version-file',str(version_file),
               '--add-data','ui-qce;ui-qce','--add-data','ui/viewer.html;ui',
               '--add-data','ui/collection.html;ui',
               '--add-data','ui/date-range-picker.js;ui',
               '--add-data','ui/archive-print.js;ui',
               '--add-data','ui/archive-media.js;ui',
               '--add-data','ui/inline-faces.js;ui',
               '--add-data','ui/wechat-faces.json;ui',
               '--add-data','ui/wechat-faces;ui/wechat-faces',
               '--add-data','ui/online-config.json;ui',
               '--add-data','LICENSE;.','--add-data','THIRD_PARTY_NOTICES.md;.',
               '--add-data','THIRD_PARTY_LICENSES;THIRD_PARTY_LICENSES',
               '--exclude-module','PyQt5','--exclude-module','PyQt6',
               '--exclude-module','PySide6',
               '--exclude-module','pywinauto','--exclude-module','comtypes',
               '--exclude-module','tkinter','--exclude-module','wx4.gui',
               '--exclude-module','wx4.cli','--exclude-module','wx4.export',
               '--collect-all','webview','--collect-all','pythonnet','--collect-all','clr_loader',
               '--collect-all','imageio_ffmpeg',
               '--collect-all','pysilk',
               '--collect-all','pyzipper','--collect-all','xlsxwriter',
               '--hidden-import','webview.platforms.edgechromium','--hidden-import','webview.platforms.winforms',
               'edge_app.py'],cwd=root,env=env,check=True)

# The update helper has its own bundled trust key and runs outside the app dir.
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onefile',
    '--name','MoectUpdater','--icon',str(icon),'--distpath','build-support/updater',
    '--manifest',str(root/'windows.manifest'),
    '--workpath','build-support/updater-build','--specpath','build-support',
    '--add-data',str(root/'ui/online-config.json')+';ui','--exclude-module','PySide6','--exclude-module','webview',
    str(root/'updater.py')],cwd=root,env=env,check=True)
destination=root/'dist/Moect';shutil.copy2(root/'build-support/updater/MoectUpdater.exe',destination/'MoectUpdater.exe')
managed=sorted(p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file() and p.name!='program-files.json')
(destination/'program-files.json').write_text(json.dumps({'version':VERSION,'files':managed},ensure_ascii=False,indent=2),encoding='utf-8')
