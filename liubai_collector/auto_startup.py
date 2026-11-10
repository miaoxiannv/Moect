"""Register the local collector for an hourly Windows user session."""
import hashlib
import os
import subprocess
import sys


def task_name():
    location = sys.executable if getattr(sys, 'frozen', False) else __file__
    signature = hashlib.sha256(os.path.abspath(location).encode('utf-8')).hexdigest()[:12]
    return 'LiubaiCollector-' + signature


def configure(enabled):
    if os.name != 'nt':
        raise RuntimeError('自动后台启动目前只支持 Windows')
    name = task_name()
    if enabled:
        # Store-installed Python may be readable interactively but denied to
        # Task Scheduler. Prefer the project venv with its tested dependencies.
        if getattr(sys, 'frozen', False):
            command = subprocess.list2cmdline([sys.executable])
        else:
            local_pythonw = os.path.join(os.path.dirname(__file__), '.venv', 'Scripts', 'pythonw.exe')
            fallback = os.path.join(sys.base_prefix, 'pythonw.exe')
            pythonw = local_pythonw if os.path.isfile(local_pythonw) else fallback
            if not os.path.isfile(pythonw) or 'windowsapps' in pythonw.lower():
                raise RuntimeError('缺少可由计划任务启动的本地 Python 环境；请先为 collector 创建 .venv 并安装依赖')
            script = os.path.abspath(os.path.join(os.path.dirname(__file__), 'api.py'))
            command = subprocess.list2cmdline([pythonw, script])
        args = ['schtasks.exe', '/Create', '/SC', 'HOURLY', '/MO', '1', '/TN', name, '/TR', command, '/IT', '/F']
    else:
        existing = subprocess.run(['schtasks.exe', '/Query', '/TN', name], capture_output=True,
                                  stdin=subprocess.DEVNULL, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        if existing.returncode != 0:
            return
        args = ['schtasks.exe', '/Delete', '/TN', name, '/F']
    result = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError('Windows 计划任务配置失败，自动启动不可用')
