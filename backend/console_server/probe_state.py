"""快照读回与「本轮写过没有」的判定：probe 输出、partial 解析、快照组装、发布历史。

`_file_written_since` 是这条链上的关键口径：用它区分「本轮真产出了结果」与
「这里躺着的还是上一轮的文件」（t10/N2）。实现从 `backend/console_server.py` 搬来，
入口仍以同名符号再导出。
"""
from __future__ import annotations

import json
import os
import time

import build_console as bc

PUBLISH_HISTORY = bc.PUBLISH_HISTORY


def _file_written_since(path: str, since: float, slack: float = 1.0) -> bool:
    """这份文件是不是在 `since` 之后被写过的。

    用 mtime 而不是「文件存在」来判「本轮写过没有」，是为了区分「本轮真产出了
    结果」与「这里躺着的还是上一轮的文件」。`slack` 给粗粒度文件系统时间戳留
    一点余量；上一轮与本轮至少隔一次搜索（分钟级），不会被容差吃掉。
    """
    try:
        return os.path.getmtime(path) >= since - slack
    except OSError:
        return False


def _probe_partial(out_text: str) -> tuple:
    """从 probe 的 stdout 里取 `PROBE_PARTIAL {...}` → (partial, timeout_stage)。

    probe 在预算耗尽时把原因打到 stdout（JSON 那份只在有交付候选时写），
    所以控制台必须从日志里读它，才能把「本轮早退」透给面板（t10/N1）。
    """
    prefix = "PROBE_PARTIAL"
    for line in out_text.splitlines():
        if line.startswith(prefix):
            payload = line[len(prefix):].strip()
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            return True, str(data.get("timeout_stage") or "")
    return False, ""


def _build_snapshot(key, probe=None, ctx=None):
    """组装快照。本轮状态（partial/timeout/round_empty）与「空轮候选标成上一轮」
    都在 `build_console.build_snapshot` 里处理——它没拿到显式传参时回落到
    context.json，所以这里每一个调用点（/api/snapshot、search job）
    都得到同一结论（t15/F-T15-4）。
    """
    ctx = ctx if ctx is not None else bc.read_context(key)
    return bc.build_snapshot(key, probe=probe, ctx=ctx)


def _parse_exists(out: str) -> dict:
    for line in out.splitlines():
        if "ALREADY_EXISTS" in line:
            return {"status": "ALREADY_EXISTS", "detail": line.strip()}
        if "NEEDS_DOWNLOAD" in line:
            return {"status": "NEEDS_DOWNLOAD", "detail": line.strip()}
        if "NOT_FOUND" in line:
            return {"status": "NOT_FOUND", "detail": line.strip()}
    return {"status": "UNKNOWN", "detail": out.strip()[:200]}


def _snapshot_history(index_path: str):
    if not os.path.isfile(index_path):
        return
    os.makedirs(PUBLISH_HISTORY, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    with open(index_path, encoding="utf-8") as fh:
        content = fh.read()
    with open(os.path.join(PUBLISH_HISTORY, f"index-{stamp}.html"), "w", encoding="utf-8") as fh:
        fh.write(content)
