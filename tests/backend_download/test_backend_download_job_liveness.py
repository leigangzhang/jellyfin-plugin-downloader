"""搜索 job 的存活契约：首行 + 心跳 + supervisor + 停滞看门狗。

Subject: `backend/console_server/jobs.py`（job 注册表 / 一轮搜索）与
`backend/console_server/http_api.py` 的 `/api/search/status`。

真机复现（2026-10-15）：手动面板搜「凡人修仙传 / 集=188」后 `POST /api/search` 秒回
`job_id=16`，而 `/api/search/status?job=16` 连续 2 分钟以上返回
`{"done": false, "lines": [], "error": ""}`，子进程跑完、job 仍是空壳且永不 done ——
面板表现就是永远停在「正在连接后端…」。三条守卫各钉一组用例：

* 建立即写首行（含 title/kind）与心跳时间戳；
* supervisor 兜住「没写终态就退出」的线程（提前 return / BaseException）；
* 停滞看门狗把心跳超时的 job 收成 done + error。

每个用例自造 job，`tearDown` 从注册表里删掉，避免 50 条上限的清理逻辑互相串味。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from urllib.parse import urlparse

from tests.backend_download._backend_download_helpers import http_api, ROOT  # noqa: F401
from console_server import jobs  # noqa: E402
import console_server as cs  # noqa: E402

TITLE = "凡人修仙传"
KIND = "episode"

# 在子进程里按**脚本路线**（插件就是这样起后端的）执行入口，再比较身份。
# `serve_forever` 换成立刻返回：只走一遍启动接线，不真的占端口、不真的服务。
_SCRIPT_ROUTE_PROBE = r'''
import http.server, os, sys, tempfile
root = os.path.abspath(os.getcwd())
entry = os.path.join(root, "backend", "console_server.py")
sys.path.insert(0, os.path.join(root, "backend"))
sys.argv = ["console_server.py", "--port", "0"]
os.environ["JMD_DATA_DIR"] = tempfile.mkdtemp(prefix="jmd-probe-")
sys.modules["__main__"].__file__ = entry          # __main__ 就是入口（脚本路线）
http.server.ThreadingHTTPServer.serve_forever = lambda self: None
src = open(entry, encoding="utf-8").read()
g = {"__name__": "__main__", "__file__": entry}
try:
    exec(compile(src, entry, "exec"), g)
except SystemExit:
    pass
import console_server.http_api as ha
import console_server.jobs as canonical
print("MODULES", sorted(k for k in sys.modules if k.endswith(("jobs", "http_api"))))
print("HTTP_FACE_IS_CANONICAL", ha.jobs_mod is canonical)
print("REGISTRY_SINGLE", g["JOBS"] is ha.jobs_mod.JOBS is canonical.JOBS)
'''


class StatusStub:
    """只够 `_get_search_status` 用的替身（真实的那个要一路造 socket）。"""

    def __init__(self):
        self.sent = []

    def _envelope(self, obj):
        return obj

    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        self.sent.append((status, payload))

    def _ok(self, obj):
        self._send(200, obj)

    @property
    def last(self):
        return self.sent[-1]


class _JobCase(unittest.TestCase):
    def setUp(self):
        self._created = []

    def tearDown(self):
        with jobs.JOBS_LOCK:
            for jid in self._created:
                jobs.JOBS.pop(jid, None)

    def new_job(self, *args, **kwargs):
        jid = jobs._new_job(*args, **kwargs)
        self._created.append(jid)
        return jid

    def status(self, jid):
        stub = StatusStub()
        http_api.ConsoleHandler._get_search_status(
            stub, urlparse(f"/api/search/status?job={jid}"))
        return stub.last

    def job(self, jid):
        with jobs.JOBS_LOCK:
            return dict(jobs.JOBS[jid])


class FirstLineAndHeartbeatTests(_JobCase):
    """第一次轮询就必须有首行与 started_at：客户端不能停在「正在连接后端…」。"""

    def test_first_status_query_already_has_the_start_line(self):
        jid = self.new_job(TITLE, KIND)
        code, payload = self.status(jid)
        self.assertEqual(code, 200)
        self.assertFalse(payload["done"])
        self.assertTrue(payload["lines"], "第一条状态查询就不能是空 lines（客户端会一直转圈）")
        self.assertEqual(payload["lines"][0], jobs.start_line(TITLE, KIND))
        self.assertIn(TITLE, payload["lines"][0])
        self.assertIn(KIND, payload["lines"][0])

    def test_first_status_query_exposes_the_heartbeat(self):
        jid = self.new_job(TITLE, KIND)
        _, payload = self.status(jid)
        self.assertIn("started_at", payload)
        self.assertIn("updated_at", payload)
        self.assertIsInstance(payload["started_at"], float)
        self.assertGreaterEqual(payload["stale_seconds"], 0.0)

    def test_old_status_fields_are_untouched(self):
        jid = self.new_job(TITLE, KIND)
        _, payload = self.status(jid)
        for field in ("done", "lines", "snapshot", "slug", "search_status", "error"):
            self.assertIn(field, payload, f"旧字段 {field} 不能丢")

    def test_every_line_and_the_finish_refresh_the_heartbeat(self):
        """不用 sleep 同步（`no-time-based-test-sync`）：把心跳改成哨兵值再看有没有被刷。"""
        jid = self.new_job(TITLE, KIND)
        with jobs.JOBS_LOCK:
            jobs.JOBS[jid]["updated_at"] = 0.0
        jobs._job_add(jid, "查重：凡人修仙传")
        self.assertGreater(self.job(jid)["updated_at"], 0.0, "_job_add 必须刷新心跳")
        with jobs.JOBS_LOCK:
            jobs.JOBS[jid]["updated_at"] = 0.0
        jobs._job_finish(jid, search_status="ok")
        self.assertGreater(self.job(jid)["updated_at"], 0.0, "_job_finish 必须刷新心跳")


class SupervisorTests(_JobCase):
    """supervisor：线程怎么退出都要留下终态。"""

    def test_worker_returning_without_finish_is_closed(self):
        jid = self.new_job(TITLE, KIND)
        jobs.supervised_worker(jid, lambda jid, params: None, {"title": TITLE})
        job = self.job(jid)
        self.assertTrue(job["done"], "工人没写终态就返回，job 必须被 supervisor 收成 done")
        self.assertEqual(job["error"], jobs.EARLY_EXIT_ERROR)

    def test_worker_that_finishes_keeps_its_result(self):
        jid = self.new_job(TITLE, KIND)

        def worker(jid, params):
            jobs._job_finish(jid, snapshot={"candidates": []}, slug="demo", search_status="ok")

        jobs.supervised_worker(jid, worker, {})
        job = self.job(jid)
        self.assertTrue(job["done"])
        self.assertEqual(job["error"], "", "正常收尾的 job 不能被 supervisor 改写成失败")
        self.assertEqual(job["search_status"], "ok")
        self.assertEqual(job["slug"], "demo")

    def test_base_exception_out_of_the_worker_is_closed(self):
        jid = self.new_job(TITLE, KIND)

        def worker(jid, params):
            raise KeyboardInterrupt("线程被外部打断")

        jobs.supervised_worker(jid, worker, {})
        job = self.job(jid)
        self.assertTrue(job["done"], "Exception 之外的退出也必须收成终态")
        self.assertTrue(job["error"])


class StalledJobWatchdogTests(_JobCase):
    """停滞看门狗：心跳超时（还是 done=false）就按失败收尾。"""

    def test_stalled_job_is_closed_by_the_watchdog(self):
        jid = self.new_job(TITLE, KIND)
        with jobs.JOBS_LOCK:
            jobs.JOBS[jid]["updated_at"] -= jobs.STALL_TIMEOUT_SECS + 1
        swept = jobs.sweep_stalled_jobs()
        self.assertIn(jid, swept, "心跳超时的 job 必须被看门狗扫到")
        job = self.job(jid)
        self.assertTrue(job["done"])
        self.assertEqual(job["error"], jobs.STALL_ERROR)

    def test_fresh_job_is_not_swept(self):
        jid = self.new_job(TITLE, KIND)
        self.assertNotIn(jid, jobs.sweep_stalled_jobs())
        self.assertFalse(self.job(jid)["done"], "心跳还新的 job 不许被看门狗动")

    def test_finished_job_is_never_swept(self):
        jid = self.new_job(TITLE, KIND)
        jobs._job_finish(jid, search_status="ok")
        with jobs.JOBS_LOCK:
            jobs.JOBS[jid]["updated_at"] -= jobs.STALL_TIMEOUT_SECS + 1
        self.assertNotIn(jid, jobs.sweep_stalled_jobs(), "已收尾的 job 不能被改写成失败")

    def test_threshold_leaves_room_for_the_single_stage_budget(self):
        """阈值取值依据：要大于入口单阶段上界 SEARCH_TIMEOUT，否则健康的长阶段会被误杀。"""
        self.assertGreater(jobs.STALL_TIMEOUT_SECS, cs.SEARCH_TIMEOUT)
        self.assertGreater(jobs.WATCHDOG_INTERVAL_SECS, 0)


class SingleRegistryOnTheScriptRouteTests(unittest.TestCase):
    """脚本路线下 job 注册表只能有一份 —— 否则前面四条防线全都白修。

    真机 job=16 的根因：`python3 backend/console_server.py` 时 `console_server` 被别名成
    `__main__`，于是 http_api 的 `from . import jobs` 按父模块名字导入了 "__main__.jobs"，
    入口的 `from console_server.jobs import ...` 又按规范名导入了一遍 —— HTTP 面（建 job、
    读 status）与 worker（写 lines、写终态）各持一份 JOBS，客户端永远 done=false，而
    worker 其实正常跑完了整轮。
    """

    def test_script_route_has_exactly_one_registry(self):
        proc = subprocess.run([sys.executable, "-c", _SCRIPT_ROUTE_PROBE],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("HTTP_FACE_IS_CANONICAL True", out, f"HTTP 面拿到的是另一份 jobs 模块：\n{out}")
        self.assertIn("REGISTRY_SINGLE True", out, f"脚本路线下出现了两份 job 注册表：\n{out}")


if __name__ == "__main__":
    unittest.main()
