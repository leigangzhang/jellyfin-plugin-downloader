"""子进程与进程生命周期：CLI 调用、进程组回收、pidfile 与 TERM/INT 收尾。

所有主 skill 的 CLI 都从这里拉起（独立进程组，超时按组 SIGKILL，连孙进程 aria2c
一起收），服务自身的 pidfile 与关停路径也在这里。实现从 `backend/console_server.py`
搬来，入口仍以同名符号再导出（外部契约不变）。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

import build_console as bc

MAIN_SCRIPTS = bc.MAIN_SCRIPTS
PID_FILE = os.path.join(bc.CONSOLE_STATE_DIR, "console_server.pid")

# 所有还在跑的子进程（search_pool / probe_magnets / pan_* …）。它们都在自己的
# 进程组里，关闭服务时按组整体 SIGKILL，连带把它们的孙进程（aria2c）一起收掉，
# 避免搜索超时或后端被重启后残留一堆孤儿 aria2c。
_ACTIVE_PROCS = set()
_PROCS_LOCK = threading.Lock()


def _track(proc):
    with _PROCS_LOCK:
        _ACTIVE_PROCS.add(proc)
    return proc


def _untrack(proc):
    with _PROCS_LOCK:
        _ACTIVE_PROCS.discard(proc)


def _kill_group(proc, grace=2.0):
    """按进程组整体终止（含孙进程），先 SIGTERM 再 SIGKILL，并回收避免僵尸。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=grace)
        return
    except Exception:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def _shutdown_children():
    with _PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
    for proc in procs:
        _kill_group(proc)


def run_cli(argv, timeout=600):
    """运行主 skill 的一个 CLI 脚本，返回 (returncode, stdout, stderr)。

    子进程放进独立进程组（start_new_session），超时/异常时按组整体杀掉，
    连它 spawn 出来的 aria2c 一起收，不会留下孤儿进程。
    """
    cmd = [sys.executable, os.path.join(MAIN_SCRIPTS, argv[0]), *argv[1:]]
    proc = _track(subprocess.Popen(
        cmd, cwd=MAIN_SCRIPTS, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    ))
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or ""
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return 124, "", "timeout"
    except Exception as exc:  # noqa: BLE001
        _kill_group(proc)
        return 125, "", str(exc)
    finally:
        _untrack(proc)


def _emit(on_line, line):
    """把一行流式日志交给回调；回调自己抛异常不影响读线程（原实现的同款吞法）。"""
    if on_line:
        try:
            on_line(line)
        except Exception:
            pass


def _stream_reader(proc, collected, on_line):
    """读线程：逐行收集并回调。

    原来它是 `run_cli_stream` 里的闭包，这里提出来只为把嵌套压到 4 层以内
    （形状判据）；语义逐字不变：读到的行先进 collected，再回调。
    """
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            collected.append(line)
            _emit(on_line, line)
    except Exception:
        pass


def run_cli_stream(argv, on_line=None, timeout=600):
    """实时运行主 skill 的一个 CLI，逐行回调 on_line；返回 (returncode, lines)。

    用 `-u` 强制无缓冲，让主 skill 脚本的 print 逐行即时可读，用于进度滚动。
    同样放进独立进程组，超时时连孙进程一起杀掉并回收。
    """
    cmd = [sys.executable, "-u", os.path.join(MAIN_SCRIPTS, argv[0]), *argv[1:]]
    proc = _track(subprocess.Popen(
        cmd, cwd=MAIN_SCRIPTS, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, start_new_session=True,
    ))
    collected = []
    thread = threading.Thread(
        target=_stream_reader, args=(proc, collected, on_line), daemon=True)
    thread.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        rc = 124
    finally:
        thread.join(timeout=5)
        _untrack(proc)
    return rc, collected


def _write_pidfile() -> None:
    try:
        bc.ensure_dirs()
        with open(PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def _remove_pidfile() -> None:
    try:
        if os.path.isfile(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass


def _pidfile_alive() -> int | None:
    try:
        with open(PID_FILE, encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    if pid <= 0 or pid == os.getpid():
        return None
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def _handle_signal(signum, frame):  # noqa: ARG001
    # 收到 TERM/INT：先按组清掉所有子进程（含 aria2c），再删 pidfile、退出。
    # 插件侧 Stop() 先发 TERM，给这里留出收尾时间，不直接 SIGKILL。
    _shutdown_children()
    _remove_pidfile()
    raise SystemExit(0)
