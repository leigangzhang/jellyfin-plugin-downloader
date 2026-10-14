"""控制台的 HTTP 面：路由、处理器基类、静态资源与各 API 端点。

Handler 的实现从 `backend/console_server.py` 搬来。入口以它的子类把 service 信封
钉在入口（`{**obj, "service": SERVICE_ID}` 这个字面量按契约留在入口文件里），并把
两处接线接回入口 —— 见下面 `valid_target_dir` / `dispatch_search` 的说明。
"""
from __future__ import annotations

import glob
import json
import os
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import build_console as bc
from media_download_lib import MEDIA_ROOT, STAGING_ROOT, path_is_within

from . import jobs as jobs_mod
from . import probe_state
from . import service

WEB_DIR = bc.WEB_DIR
PUBLISH_HISTORY = probe_state.PUBLISH_HISTORY

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def _target_dir_ok(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    real = os.path.realpath(value)
    return path_is_within(real, STAGING_ROOT) or path_is_within(real, MEDIA_ROOT)


# 组合根接线：下面这两个名字由入口（backend/console_server.py 的 `_wire_http_api`）
# 替换成按**入口模块全局**解析的薄壳。理由：入口是唯一持有预算常量与
# `_run_search_job` 的地方，而测试用 mock.patch.object(console_server,
# "valid_target_dir" / "_run_search_job") 替换的实现，只有在调用点回查入口全局时
# 才生效。包内不复制那些定量与函数。不经过入口直接 import 本包（没有这种用法）时，
# valid_target_dir 仍是可用的实现，dispatch_search 会明确报错而不是静默不起搜索。
valid_target_dir = _target_dir_ok


def render_index(css: str, body_html: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Jellyfin 下载控制台</title>\n"
        "<style>\n" + (css or "") + "\n</style>\n"
        "</head>\n<body>\n" + (body_html or "") + "\n"
        "<script src=\"/app.js\"></script>\n</body>\n</html>\n"
    )


def _body_inner(html: str) -> str:
    """GrapesJS 的 getHtml() 可能带 <body> 包裹，这里剥离成纯 body 内容。"""
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    return m.group(1) if m else html


def dispatch_search(jid, params):
    """把一轮搜索丢进后台线程；入口在组合根里替换它（包单独用时明确报错）。"""
    raise RuntimeError("搜索调度未接线：请经 backend/console_server.py 入口启动服务")


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "JMDConsole/1.0"

    # ---- helpers ----------------------------------------------------------
    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            body = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _envelope(self, obj):
        """给 JSON 响应加自身标识；入口的 Handler 子类在这里钉 SERVICE_ID。"""
        return obj

    def _ok(self, obj):
        self._send(200, self._envelope(obj))

    def _bad(self, msg):
        self._send(400, {"error": msg})

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def log_message(self, fmt, *args):  # 静默默认访问日志
        return

    # ---- routing ----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path == "/editor.html":
            return self._serve_static("editor.html")
        if path == "/app.js":
            return self._serve_static("app.js")
        if path == "/console.css":
            return self._serve_static("console.css")
        if path.startswith("/vendor/"):
            name = os.path.basename(path)
            if name in ("grapes.min.js", "grapes.min.css"):
                return self._serve_static(os.path.join("vendor", name))
        if path == "/api/snapshot":
            return self._get_snapshot(parsed)
        if path == "/api/search/status":
            return self._get_search_status(parsed)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._body()
        if body is None:
            return self._bad("invalid json body")
        if path == "/api/search":
            return self._api_search(body)
        if path == "/api/submit":
            return self._api_submit(body)
        if path == "/api/watch":
            return self._api_watch(body)
        if path == "/api/cleanup":
            return self._api_cleanup(body)
        if path == "/api/mark":
            return self._api_mark(body)
        if path == "/api/publish":
            return self._api_publish(body)
        if path == "/api/publish/rollback":
            return self._api_publish_rollback()
        if path.startswith("/api/pan/"):
            sub = path[len("/api/pan/"):]
            return self._api_pan(sub, body)
        return self._send(404, {"error": "not found"})

    # ---- static -----------------------------------------------------------
    def _serve_static(self, rel):
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)):
            return self._send(403, {"error": "forbidden"})
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            data = fh.read()
        return self._send(200, data, MIME.get(ext, "application/octet-stream"))

    # ---- snapshot ---------------------------------------------------------
    def _get_snapshot(self, parsed):
        q = parse_qs(parsed.query)
        slug = (q.get("slug") or [""])[0]
        title = (q.get("title") or [""])[0]
        season = (q.get("season") or [""])[0]
        if not slug and title:
            slug = bc.slugify(title)
        if not slug:
            return self._ok(bc.build_snapshot("pool", probe={}, ctx={}))
        if not bc.valid_slug(slug):
            return self._bad("invalid slug")
        # 季级快照：选了季就读该季的文件，未选季读整剧文件
        key = bc.snapshot_key(slug, season)
        if not bc.valid_slug(key):
            return self._bad("invalid slug")
        return self._ok(probe_state._build_snapshot(key))

    # ---- search -----------------------------------------------------------
    def _api_search(self, body):
        title = (body.get("title") or "").strip()
        kind = body.get("kind") or "episode"
        if not title:
            return self._bad("title is required")
        if kind not in bc.VALID_KINDS:
            return self._bad("invalid kind")
        slug = bc.slugify(title)
        duration = float(body.get("duration_minutes") or bc.DEFAULT_DURATION[kind])
        year = str(body.get("year") or "")
        alt_title = body.get("alt_title") or []
        season = body.get("season")
        episode = body.get("episode")
        # 抓取来源：magnet（仅资源站磁力）/ pan（仅云盘）/ both（两者）。
        # 本地 Pansou 已删除，所以云盘候选只能来自「站点网页搜索地址」，见
        # `_run_search_job` 的云盘阶段。
        sources = str(body.get("sources") or "").lower()
        if sources not in ("magnet", "pan", "both"):
            sources = "pan" if body.get("allow_pan") else "magnet"
        # 原始片名（英文名等）：既提升召回，也用于「同名噪音」判定
        original_title = str(body.get("original_title") or "")
        # 严格按季：只跑季关键词，不带基础片名（默认开；关掉则整剧+季词都跑）
        strict_season = bool(body.get("strict_season", True))
        # 单源：只跑配置的 L1 关键字档（发布者署名），并只保留该源候选。
        # **默认开**（2026-10-13 使用者要求「开启单源」）：候选面收窄成一家源。
        # 仍可用 body 里的 "publisher_only": false 单次关掉——关键字没有资源的片名
        # 会搜不到候选，所以保留这个退路，且空结果会被打印成 PUBLISHER_ONLY_EMPTY。
        publisher_only = bool(body.get("publisher_only", True))
        final_dir = body.get("final_dir") or bc.derive_final_dir(title, kind, year)
        if not valid_target_dir(final_dir):
            return self._bad("final_dir must be inside staging or Media")

        # 候选文件按**本轮**命名：同名标题连开两轮（同一季重搜、或不同季但同一个 slug）
        # 不会互相读到对方的候选。原来固定 `/tmp/<slug>.magnets`，只能靠 mtime 判断
        # 「是不是本轮写的」，而两个写者并发时那条判定就失效了（复核第二轮 F3）。
        jid = jobs_mod._new_job()
        params = {
            "title": title, "kind": kind, "slug": slug, "duration": duration,
            "year": year, "alt_title": alt_title, "season": season, "episode": episode,
            "sources": sources,
            "original_title": original_title, "strict_season": strict_season,
            "publisher_only": publisher_only,
            "final_dir": final_dir,
            "category": bc.KIND_TO_CATEGORY[kind],
            "probe_kind": bc.PROBE_KIND[kind],
            "pool_file": os.path.join(bc.POOL_DIR, f"{slug}.json"),
            "cand_file": os.path.join("/tmp", f"{slug}.{jid}.magnets"),
            # 季级命名空间：各季结果分开存，互不覆盖
            "key": bc.snapshot_key(slug, season),
        }
        params["probe_out"] = os.path.join(bc.SNAPSHOTS_DIR, f"{params['key']}.probe.json")
        # 起后台线程这件事按入口全局解析（见文件顶部的接线说明）：入口是唯一知道
        # `_run_search_job` 与预算常量在哪的地方。
        dispatch_search(jid, params)
        return self._ok({"job_id": jid, "slug": slug, "done": False})

    def _get_search_status(self, parsed):
        q = parse_qs(parsed.query)
        jid = (q.get("job") or [""])[0]
        with jobs_mod.JOBS_LOCK:
            job = jobs_mod.JOBS.get(jid)
            if job is None:
                return self._send(404, {"error": "unknown job"})
            resp = dict(job)
        return self._ok(resp)

    # ---- verify -----------------------------------------------------------
    def _api_submit(self, body):
        ih = (body.get("ih") or "").strip()
        final_dir = body.get("final_dir") or ""
        kind = body.get("kind") or "episode"
        if not bc.valid_ih(ih):
            return self._bad("invalid info hash")
        category = (body.get("category") or "").strip()
        if category:
            if category not in bc.CATEGORIES:
                return self._bad("invalid category")
            final_dir = bc.derive_final_dir(
                (body.get("series_name") or "").strip(),
                kind,
                str(body.get("year") or ""),
                category,
            )
        if not valid_target_dir(final_dir):
            return self._bad("final_dir must be inside staging or Media")
        probe_kind = bc.PROBE_KIND.get(kind, "episode")
        eta = float(body.get("eta_max_hours") or 4.0)
        cmd = [
            "download/submit_xunlei.py", ih, final_dir,
            "--kind", probe_kind,
            "--eta-max-hours", str(eta),
            "--no-watch",
        ]
        if body.get("series_name"):
            cmd += ["--series-name", body["series_name"]]
        if body.get("year"):
            cmd += ["--year", str(body["year"])]
        rc, out, err = service.run_cli(cmd, 900)
        return self._ok({"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err})

    def _api_watch(self, body):
        ih = (body.get("ih") or "").strip()
        final_dir = body.get("final_dir") or ""
        if not bc.valid_ih(ih):
            return self._bad("invalid info hash")
        category = (body.get("category") or "").strip()
        if category:
            if category not in bc.CATEGORIES:
                return self._bad("invalid category")
            final_dir = bc.derive_final_dir(
                (body.get("series_name") or "").strip(),
                body.get("kind") or "episode",
                str(body.get("year") or ""),
                category,
            )
        if not valid_target_dir(final_dir):
            return self._bad("final_dir must be inside staging or Media")
        interval = int(body.get("interval") or 900)
        cmd = ["download/watch_download.py", ih, final_dir, "--detach", "--interval", str(interval)]
        if body.get("series_name"):
            cmd += ["--series-name", body["series_name"]]
        if body.get("year"):
            cmd += ["--year", str(body["year"])]
        rc, out, err = service.run_cli(cmd, 60)
        return self._ok({"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err})

    def _api_cleanup(self, body):
        target = body.get("dir") or ""
        mode = body.get("mode") or "audit"
        if not valid_target_dir(target):
            return self._bad("dir must be inside staging or Media")
        if mode not in ("audit", "prune-orphans"):
            return self._bad("invalid mode")
        flag = "--audit" if mode == "audit" else "--prune-orphans"
        rc, out, err = service.run_cli(["download/cleanup_download.py", "", target, flag], 300)
        return self._ok({"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err})

    # ---- pan --------------------------------------------------------------
    def _api_pan(self, sub, body):
        allowed = {"plan", "verify-transfer", "set-save-path", "verify-retrieve", "watch", "status"}
        if sub not in allowed:
            return self._bad("invalid pan subcommand")
        title = (body.get("title") or "").strip()
        if not title:
            return self._bad("title is required")
        cmd = ["pan/pan_transfer.py", sub, "--title", title]
        if body.get("final_dir"):
            cmd += ["--final-dir", body["final_dir"]]
        if body.get("series_name"):
            cmd += ["--series-name", body["series_name"]]
        if body.get("year"):
            cmd += ["--year", str(body["year"])]
        if body.get("season"):
            cmd += ["--season", str(int(body["season"]))]
        if body.get("share_url"):
            cmd += ["--share-url", body["share_url"]]
        if body.get("pass_code"):
            cmd += ["--pass-code", body["pass_code"]]
        if sub == "watch":
            cmd += ["--detach"]
        rc, out, err = service.run_cli(cmd, 600)
        return self._ok({"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err})

    # ---- mark -------------------------------------------------------------
    def _api_mark(self, body):
        slug = body.get("slug") or ""
        key = body.get("key") or body.get("ih") or ""
        mark = body.get("mark") or ""
        if not bc.valid_slug(slug):
            return self._bad("invalid slug")
        if not key:
            return self._bad("key is required")
        if mark not in bc.MARK_SET:
            return self._bad("invalid mark")
        marks = bc.read_marks(slug)
        marks[key] = mark
        bc.write_marks(slug, marks)
        return self._ok({"ok": True, "key": key, "mark": mark, "marks": marks})

    # ---- publish ----------------------------------------------------------
    def _api_publish(self, body):
        html = _body_inner(body.get("html") or "")
        css = body.get("css") or ""
        final = render_index(css, html)
        index_path = os.path.join(WEB_DIR, "index.html")
        probe_state._snapshot_history(index_path)
        with open(index_path, "w", encoding="utf-8") as fh:
            fh.write(final)
        return self._ok({"ok": True, "published": True})

    def _api_publish_rollback(self):
        index_path = os.path.join(WEB_DIR, "index.html")
        history = sorted(glob.glob(os.path.join(PUBLISH_HISTORY, "index-*.html")))
        if not history:
            return self._send(404, {"error": "no history"})
        latest = history[-1]
        with open(latest, encoding="utf-8") as fh:
            content = fh.read()
        with open(index_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        try:
            os.remove(latest)
        except OSError:
            pass
        return self._ok({"ok": True, "rolled_back": os.path.basename(latest)})
