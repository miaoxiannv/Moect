"""Windows WebView2 desktop host: native DirectWrite text and QCE UI."""
import argparse
import ctypes
import json
import os
import sys
from pathlib import Path
import webview
from core.service import Service
from core.version import SOURCE_URLS
from core.edge_chrome import NativeFrame
from core.window_state import DEFAULT_SIZE,MIN_SIZE,WindowPlacement

BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
UI=BASE/'ui-qce'


class Api:
    def __init__(self,service):self._service=service;self._window=None;self._frame=None

    def _paint_frame(self):
        from System import Action
        from System.Drawing import ColorTranslator
        color=self._service.appearance()['background']
        def paint():
            self._window.native.BackColor=ColorTranslator.FromHtml(color)
        self._window.native.Invoke(Action(paint))

    def invoke(self,action,payload=None):
        try:
            if not isinstance(action,str) or not isinstance(payload or {},dict):raise ValueError('请求无效')
            payload=payload or {}
            bounded = {k: v for k, v in payload.items() if k != 'sessions'} if action == 'export' else payload
            if len(json.dumps(bounded))>100000:raise ValueError('请求过大')
            svc=self._service;window=self._window
            if action.startswith('window_'):
                if action=='window_overlay':
                    if not isinstance(payload.get('active'),bool):raise ValueError('窗口遮罩状态无效')
                    # Compatibility only. The WebView now covers the full client
                    # area, so its overlay paints in one pass without native dimming.
                elif action=='window_resize':
                    edge=payload.get('edge')
                    if edge not in ('n','s','e','w','nw','ne','sw','se'):raise ValueError('窗口边缘无效')
                    from System import Action
                    window.native.Invoke(Action(lambda:self._frame.resize(edge)))
                elif action=='window_drag':
                    from System import Action
                    # ReleaseCapture must run on the HWND's owning UI thread.
                    window.native.Invoke(Action(self._frame.drag))
                elif action=='window_minimize':window.minimize()
                elif action=='window_maximize':
                    window.restore() if self._frame.isMaximized() else window.maximize()
                elif action=='window_close':window.destroy()
                elif action!='window_state':raise ValueError('不支持的窗口操作')
                return {'result':{'maximized':self._frame.isMaximized()}}
            if action in ('choose_archive','choose_output','choose_storage','choose_cache','choose_diagnostics'):
                folders=window.create_file_dialog(webview.FileDialog.FOLDER)
                if not folders:return {'result':{'cancelled':True}}
                if action=='choose_output':
                    svc.config['export_dir']=folders[0];svc.save();result=svc.state()
                elif action=='choose_archive':result=svc.import_archive(folders[0])
                elif action=='choose_cache':result=svc.storage_job('storage_migrate', folders[0])
                elif action=='choose_diagnostics':
                    from core.diagnostics import write_report
                    result=write_report(svc, folders[0], payload.get('id'))
                else:result=svc.add_storage(folders[0])
            elif action in ('open_export','open_task_folder','open_output'):
                if action=='open_export':target=svc.task_file(payload.get('id'))
                elif action=='open_task_folder':target=svc.task_folder(payload.get('id'))
                else:
                    target=Path(svc.config['export_dir']);target.mkdir(parents=True,exist_ok=True)
                os.startfile(str(target));result={'ok':True}
            elif action=='source':
                url=SOURCE_URLS.get(payload.get('name'))
                if url:os.startfile(url)
                result={'ok':True}
            elif action=='online_open_link':
                from core.online_protocol import web_url
                os.startfile(web_url(payload.get('url')));result={'ok':True}
            elif action=='update_apply':
                raise ValueError('此二开版本不支持安装 Moect 官方更新。')
            else:
                result=svc.call(action,payload)
                if action=='settings':
                    self._paint_frame()
            return {'result':result}
        except Exception as exc:return {'error':str(exc)[:240] or '操作失败'}

    def _install_frame(self):
        from System import Action
        from System.Drawing import Icon,ColorTranslator
        from System.Windows.Forms import Padding
        def install():
            form=self._window.native
            form.BackColor=ColorTranslator.FromHtml(self._service.appearance()['background'])
            self._frame=NativeFrame(form.Handle.ToInt64())
            self._frame.hideBorder()
            self._placement=WindowPlacement(form,self._service,self._frame)
            # Fill every client pixel. User-driven edge input is forwarded to
            # Windows' native sizing loop, preserving snap, DPI and min sizes.
            form.Padding=Padding(0)
            def resize(*_):
                if not form.webview.IsDisposed and form.webview.CoreWebView2 is not None:
                    state=json.dumps({'maximized':self._frame.isMaximized()})
                    form.webview.CoreWebView2.ExecuteScriptAsync('window.dispatchEvent(new CustomEvent("wce-window-state",{detail:'+state+'}));')
            self._resize_handler=resize
            form.Resize+=resize
            form.DpiChanged+=resize
            resize()
            def browser_ready(*_):
                core=form.webview.CoreWebView2
                if core is None:return
                try:
                    core.Settings.IsNonClientRegionSupportEnabled=True
                    script='window.__wceNativeRegions=true;'
                except Exception:
                    # The UI-thread bridge above remains the fallback for old
                    # WebView2 SDKs; modern runtimes use native CSS hit testing.
                    script='window.__wceNativeRegions=false;'
                core.AddScriptToExecuteOnDocumentCreatedAsync(script)
                core.ExecuteScriptAsync(script)
                if core.Source and core.Source.startswith('file:'):core.Reload()
            self._browser_ready_handler=browser_ready
            form.webview.CoreWebView2InitializationCompleted+=browser_ready
            if form.webview.CoreWebView2 is not None:browser_ready()
            icon=BASE/'ui-qce/icon.ico'
            if icon.exists():form.Icon=Icon(str(icon))
        self._window.native.Invoke(Action(install))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--demo',action='store_true')
    parser.add_argument('--data-dir');parser.add_argument('--smoke');parser.add_argument('--self-check')
    args=parser.parse_args()
    if args.self_check:
        from core.selfcheck import run
        return run(args.self_check)
    # The portable distribution carries its own independent collector. A
    # running collector holds an exclusive port, so duplicate launches exit.
    if getattr(sys, 'frozen', False) and not args.demo:
        import subprocess
        collector = Path(sys.executable).parent / 'LiubaiCollector.exe'
        if collector.is_file():
            subprocess.Popen([str(collector)], cwd=str(collector.parent),
                             creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                             close_fds=True)
    # Select monitor-native physical pixels before creating any HWND.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    service=Service(BASE/'ui',args.data_dir)
    if args.demo:service.demo()
    api=Api(service)
    window=webview.create_window('Moect · 聊天记录归档',(UI/'index.html').as_uri(),
        js_api=api,width=DEFAULT_SIZE[0],height=DEFAULT_SIZE[1],min_size=MIN_SIZE,frameless=True,
        easy_drag=False,shadow=True,background_color=service.appearance()['background'])
    api._window=window
    window.events.before_show+=api._install_frame
    def closing():
        if any(t['status'] in ('running','queued') for t in service.config['tasks']):
            if not window.create_confirmation_dialog('退出软件','仍有任务正在处理，退出会取消任务。是否退出？'):return False
        return True
    window.events.closing+=closing
    window.events.closed+=service.shutdown
    def launch():
        if args.smoke:
            if not window.events._pywebviewready.wait(30):return
            from tests.edge_smoke import run
            run(window,api,Path(args.smoke))
    webview.start(launch,gui='edgechromium',private_mode=True,http_server=False,
                  storage_path=str(service.root/'webview2'))
    return 0


if __name__=='__main__':sys.exit(main())
