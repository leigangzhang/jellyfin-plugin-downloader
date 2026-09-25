#!/usr/bin/env python3
"""省流版本地控制台服务（Python 标准库 http.server，零第三方依赖）。

静态：/ /editor.html /app.js /console.css /vendor/*
API：/api/snapshot /api/search /api/verify /api/submit /api/watch /api/cleanup
     /api/pan/<subcommand> /api/mark /api/publish /api/publish/rollback

所有写动作都委托给主 skill 脚本（subprocess）或复用其库函数（import），本服务
本身不改主 skill 的 pool 与迅雷数据库。
"""
from __future__ import annotations

import argparse
import atexit
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import build_console as bc
from media_download_lib import MEDIA_ROOT, STAGING_ROOT, path_is_within  # noqa: E402
from speed_probe import measure_torrent_speed  # noqa: E402

MAIN_SCRIPTS = bc.MAIN_SCRIPTS
WEB_DIR = bc.WEB_DIR
PUBLISH_HISTORY = bc.PUBLISH_HISTORY
PID_FILE = os.path.join(bc.CONSOLE_STATE_DIR, "console_server.pid")

# 所有还在跑的子进程（search_pool / probe_magnets / pan_* …）。它们都在自己的
# 进程组里，关闭服务时按组整体 SIGKILL，连带把它们的孙进程（aria2c）一起收掉，
# 避免搜索超时或后端被重启后残留一堆孤儿 aria2c。
_ACTIVE_PROCS = set()
_PROCS_LOCK = threading.Lock()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}

SEARCH_TIMEOUT = 1800  # probe（含 metadata 抓取 + 速度实测）可能很慢
SEARCH_POOL_TIMEOUT = 600
VERIFY_TIMEOUT = 60


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

    def _reader():
        try:
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                collected.append(line)
                if on_line:
                    try:
                        on_line(line)
                    except Exception:
                        pass
        except Exception:
            pass

    thread = threading.Thread(target=_reader, daemon=True)
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


# 搜索任务注册表：job_id -> {done, lines, snapshot, slug, search_status, error}
JOBS = {}
JOBS_LOCK = threading.Lock()
_JOB_SEQ = [0]


def _new_job():
    with JOBS_LOCK:
        _JOB_SEQ[0] += 1
        jid = str(_JOB_SEQ[0])
        JOBS[jid] = {
            "done": False, "lines": [], "snapshot": None,
            "slug": "", "search_status": "", "error": "",
        }
        if len(JOBS) > 50:
            for old in sorted(JOBS, key=int)[:-50]:
                JOBS.pop(old, None)
        return jid


def _job_add(jid, line):
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if job is None:
            return
        job["lines"].append(line)
        if len(job["lines"]) > 800:
            job["lines"] = job["lines"][-500:]


def _job_finish(jid, snapshot=None, slug="", search_status="", error=""):
    with JOBS_LOCK:
        job = JOBS.get(jid)
        if job is None:
            return
        job.update({
            "done": True, "snapshot": snapshot, "slug": slug,
            "search_status": search_status, "error": error,
        })


def valid_target_dir(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    real = os.path.realpath(value)
    return path_is_within(real, STAGING_ROOT) or path_is_within(real, MEDIA_ROOT)


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


class Handler(BaseHTTPRequestHandler):
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

    def _ok(self, obj):
        self._send(200, obj)

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
        if path == "/api/verify":
            return self._api_verify(body)
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
        return self._ok(bc.build_snapshot(key))

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
        allow_pansou = bool(body.get("allow_pansou", True))
        allow_pan = bool(body.get("allow_pan", False))
        # 抓取来源：magnet（默认，仅磁力）/ pan（仅云盘）/ both（两者）
        sources = str(body.get("sources") or "").lower()
        if sources not in ("magnet", "pan", "both"):
            sources = "both" if (allow_pansou and allow_pan) else ("pan" if allow_pan else "magnet")
        # 原始片名（英文名等）：既提升召回，也用于「同名噪音」判定
        original_title = str(body.get("original_title") or "")
        # 严格按季：只跑季关键词，不带基础片名（默认开；关掉则整剧+季词都跑）
        strict_season = bool(body.get("strict_season", True))
        final_dir = body.get("final_dir") or bc.derive_final_dir(title, kind, year)
        if not valid_target_dir(final_dir):
            return self._bad("final_dir must be inside staging or Media")

        params = {
            "title": title, "kind": kind, "slug": slug, "duration": duration,
            "year": year, "alt_title": alt_title, "season": season, "episode": episode,
            "allow_pansou": allow_pansou, "allow_pan": allow_pan, "sources": sources,
            "original_title": original_title, "strict_season": strict_season,
            "final_dir": final_dir,
            "category": bc.KIND_TO_CATEGORY[kind],
            "probe_kind": bc.PROBE_KIND[kind],
            "pool_file": os.path.join(bc.POOL_DIR, f"{slug}.json"),
            "cand_file": os.path.join("/tmp", f"{slug}.magnets"),
            # 季级命名空间：各季结果分开存，互不覆盖
            "key": bc.snapshot_key(slug, season),
        }
        params["probe_out"] = os.path.join(bc.SNAPSHOTS_DIR, f"{params['key']}.probe.json")
        jid = _new_job()
        threading.Thread(target=_run_search_job, args=(jid, params), daemon=True).start()
        return self._ok({"job_id": jid, "slug": slug, "done": False})

    def _get_search_status(self, parsed):
        q = parse_qs(parsed.query)
        jid = (q.get("job") or [""])[0]
        with JOBS_LOCK:
            job = JOBS.get(jid)
            if job is None:
                return self._send(404, {"error": "unknown job"})
            resp = dict(job)
        return self._ok(resp)

    # ---- verify -----------------------------------------------------------
    def _api_verify(self, body):
        slug = body.get("slug") or ""
        ih = (body.get("ih") or "").strip()
        if not bc.valid_slug(slug):
            return self._bad("invalid slug")
        if not bc.valid_ih(ih):
            return self._bad("invalid info hash")
        res = measure_torrent_speed(ih.upper(), seconds=15, warmup=3)
        speed_mbps = float(res.get("speed_bps") or 0) / 1e6
        alive = bool(res.get("bytes")) and float(res.get("speed_bps") or 0) > 0

        probe = bc.read_probe(slug)
        if probe:
            updated = bc.rescore_entry(probe, ih.upper(), speed_mbps)
            if updated:
                ranked = probe.get("ranked") or []
                for i, entry in enumerate(ranked):
                    if bc.candidate_key(entry) == ih.upper():
                        ranked[i] = updated
                        break
                ranked.sort(key=lambda e: float(e.get("score") or 0), reverse=True)
                for i, entry in enumerate(ranked, start=1):
                    entry["rank"] = i
                probe["ranked"] = ranked
                if ranked:
                    probe["best"] = ranked[0]
                bc.save_probe(slug, probe)

        verify = {
            "ih": ih.upper(),
            "speed_mbps": round(speed_mbps, 3),
            "alive": alive,
            "bytes": res.get("bytes", 0),
            "error": res.get("error", ""),
        }
        return self._ok({"verify": verify, "snapshot": bc.build_snapshot(slug)})

    # ---- submit / watch / cleanup ----------------------------------------
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
            "submit_xunlei.py", ih, final_dir,
            "--kind", probe_kind,
            "--eta-max-hours", str(eta),
            "--no-watch",
        ]
        if body.get("series_name"):
            cmd += ["--series-name", body["series_name"]]
        if body.get("year"):
            cmd += ["--year", str(body["year"])]
        rc, out, err = run_cli(cmd, 900)
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
        cmd = ["watch_download.py", ih, final_dir, "--detach", "--interval", str(interval)]
        if body.get("series_name"):
            cmd += ["--series-name", body["series_name"]]
        if body.get("year"):
            cmd += ["--year", str(body["year"])]
        rc, out, err = run_cli(cmd, 60)
        return self._ok({"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err})

    def _api_cleanup(self, body):
        target = body.get("dir") or ""
        mode = body.get("mode") or "audit"
        if not valid_target_dir(target):
            return self._bad("dir must be inside staging or Media")
        if mode not in ("audit", "prune-orphans"):
            return self._bad("invalid mode")
        flag = "--audit" if mode == "audit" else "--prune-orphans"
        rc, out, err = run_cli(["cleanup_download.py", "", target, flag], 300)
        return self._ok({"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err})

    # ---- pan --------------------------------------------------------------
    def _api_pan(self, sub, body):
        allowed = {"plan", "verify-transfer", "set-save-path", "verify-retrieve", "watch", "status"}
        if sub not in allowed:
            return self._bad("invalid pan subcommand")
        title = (body.get("title") or "").strip()
        if not title:
            return self._bad("title is required")
        cmd = ["pan_transfer.py", sub, "--title", title]
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
        rc, out, err = run_cli(cmd, 600)
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
        _snapshot_history(index_path)
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


def season_hints(season: int) -> list:
    """把「第 N 季」的常见写法交给 search_pool 的整包关键词档位。"""
    hints = [f"第{season}季"]
    numeral = bc.CN_NUMERALS.get(season)
    if numeral:
        hints.append(f"第{numeral}季")
    hints.append(f"S{season:02d}")
    return hints


def episode_hints(season: int, episode: int) -> list:
    """把「第 N 集」的常见写法交给搜索阶梯（单集页专用）。

    没有这一档，单集页只能搜到季包：diao.im 上单集资源的名字是
    `[第01集]` / `S01E01` 这种写法，靠「第1季」是搜不出来的。
    """
    return [f"第{episode:02d}集", f"第{episode}集", f"S{season:02d}E{episode:02d}"]


def _run_search_job(jid, p):
    """后台执行搜索任务，逐步把进度行追加到 job，完成后写入快照。"""
    def log(line):
        _job_add(jid, line)

    try:
        # probe 会直接往 state/snapshots/<slug>.probe.json 写 --json-out，
        # 目录必须先存在（独立后端首次运行时 state/ 是空的）。
        bc.ensure_dirs()

        log(f"查重：{p['title']}（{p['category']}）")
        rc, out, err = run_cli(["check_exists.py", p["title"], p["category"]], 120)
        exists = _parse_exists(out)
        log("  " + (exists.get("detail") or exists.get("status") or "OK"))

        sources = p.get("sources", "magnet")
        want_magnet = sources in ("magnet", "both")
        want_pan = sources in ("pan", "both")

        if want_magnet:
            log("L1 搜索（diao.im）…")
            search_cmd = [
                "search_pool.py", p["title"],
                "--pool-file", p["pool_file"],
                "--candidates-out", p["cand_file"],
                "--json-out", os.path.join("/tmp", f"{p['slug']}_search.json"),
            ]
            if p["year"]:
                search_cmd += ["--year", p["year"]]
            for a in p["alt_title"]:
                if a:
                    search_cmd += ["--alt-title", a]
            if p.get("original_title") and p["original_title"] not in p["alt_title"]:
                search_cmd += ["--alt-title", p["original_title"]]
            if p["season"]:
                season_number = int(p["season"])
                search_cmd += ["--season", str(season_number)]
                # 单集页：集级关键词排在最前，先抓「只含本集」的种子
                #（strict 模式按顺序跑，整包词排在后面兜底 —— 整包也覆盖本集）
                hints = []
                if p.get("episode"):
                    hints += episode_hints(season_number, int(p["episode"]))
                # 把「第 N 季」的各种写法喂给「整包关键词」档位，让搜索指向该季
                hints += season_hints(season_number)
                for hint in hints:
                    search_cmd += ["--collection-hint", hint]
                if p.get("strict_season", True):
                    # 严格按季：跳过基础片名/年份/平台档位，只用季关键词，
                    # 避免基础片名把整部剧（其它季 + 同名片）都带进来
                    search_cmd += ["--strict-match"]
                # 跑满阶梯：默认 --enough 12 会让基础片名查询提前收工
                search_cmd += ["--enough", "40", "--max-queries", "12"]
            run_cli_stream(search_cmd, log, SEARCH_POOL_TIMEOUT)

        pan_cand = ""
        if want_pan:
            log("云盘搜索（Pansou）…")
            pan_cand = os.path.join("/tmp", f"{p['slug']}_pan.json")
            run_cli_stream(
                ["pan_search.py", p["title"], "--pansou", "http://127.0.0.1:64056", "--candidates-out", pan_cand],
                log, 300,
            )
            # 保留云盘候选（含提取码），供快照富化（probe 的 ranked 输出不含 pass_code）
            if os.path.isfile(pan_cand):
                try:
                    shutil.copyfile(pan_cand, os.path.join(bc.SNAPSHOTS_DIR, f"{p['key']}.pan.json"))
                except OSError:
                    pass

        log("打分 + 实测速度（probe）…")
        probe_cmd = [
            "probe_magnets.py", p["title"],
            "--kind", p["probe_kind"],
            "--duration-minutes", str(p["duration"]),
            "--pool-file", p["pool_file"],
            "--json-out", p["probe_out"],
        ]
        if want_magnet:
            probe_cmd += ["--candidate-file", p["cand_file"]]
        if want_magnet and p["allow_pansou"]:
            probe_cmd += ["--allow-pansou"]
        if want_pan and pan_cand and os.path.isfile(pan_cand):
            probe_cmd += ["--extra-candidates", pan_cand]
        probe_cmd += ["--speed-probe", "--speed-top", "8"]
        rc, lines = run_cli_stream(probe_cmd, log, SEARCH_TIMEOUT)

        probe = {}
        if os.path.isfile(p["probe_out"]):
            try:
                with open(p["probe_out"], encoding="utf-8") as fh:
                    probe = json.load(fh)
            except (OSError, json.JSONDecodeError):
                probe = {}
        bc.save_probe(p["key"], probe)

        out_text = "\n".join(lines)
        search_status = _search_status(out_text, probe, rc)
        ctx = {
            "title": p["title"], "kind": p["kind"], "year": p["year"],
            "season": p.get("season"), "episode": p.get("episode"),
            "duration_minutes": p["duration"], "final_dir": p["final_dir"],
            "exists": exists, "search_status": search_status, "sources": p.get("sources"),
            "original_title": p.get("original_title", ""),
            "strict_season": bool(p.get("strict_season", True)),
        }
        bc.save_context(p["key"], ctx)
        snapshot = bc.build_snapshot(p["key"], probe=probe, ctx=ctx)
        log(f"完成：{len(snapshot['candidates'])} 个候选（状态 {search_status}）")
        _job_finish(jid, snapshot=snapshot, slug=p["slug"], search_status=search_status)
    except Exception as exc:  # noqa: BLE001
        log(f"搜索异常：{exc}")
        _job_finish(jid, error=str(exc))


def _parse_exists(out: str) -> dict:
    for line in out.splitlines():
        if "ALREADY_EXISTS" in line:
            return {"status": "ALREADY_EXISTS", "detail": line.strip()}
        if "NEEDS_DOWNLOAD" in line:
            return {"status": "NEEDS_DOWNLOAD", "detail": line.strip()}
        if "NOT_FOUND" in line:
            return {"status": "NOT_FOUND", "detail": line.strip()}
    return {"status": "UNKNOWN", "detail": out.strip()[:200]}


def _search_status(out: str, probe: dict, rc: int) -> str:
    if probe.get("best_below_min_score"):
        return "best_below_min_score"
    if probe.get("best_dead_swarm"):
        return "best_dead_swarm"
    for token in (
        "SWARM_DEAD", "METADATA_FAILED", "NO_ELIGIBLE_RESOURCE",
        "NO_CANDIDATES", "UNSUPPORTED_THUNDER_DIRECT_LINK",
        "BEST_BELOW_MIN_SCORE", "BEST_DEAD_SWARM",
    ):
        if token in out:
            return {
                "SWARM_DEAD": "swarm_dead",
                "METADATA_FAILED": "metadata_failed",
                "NO_ELIGIBLE_RESOURCE": "no_eligible_resource",
                "NO_CANDIDATES": "no_candidates",
                "UNSUPPORTED_THUNDER_DIRECT_LINK": "unsupported",
                "BEST_BELOW_MIN_SCORE": "best_below_min_score",
                "BEST_DEAD_SWARM": "best_dead_swarm",
            }[token]
    if rc == 0:
        return "ok"
    return f"rc_{rc}"


def _snapshot_history(index_path: str):
    if not os.path.isfile(index_path):
        return
    os.makedirs(PUBLISH_HISTORY, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    with open(index_path, encoding="utf-8") as fh:
        content = fh.read()
    with open(os.path.join(PUBLISH_HISTORY, f"index-{stamp}.html"), "w", encoding="utf-8") as fh:
        fh.write(content)


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


def main() -> int:
    parser = argparse.ArgumentParser(description="省流版本地控制台服务")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    bc.ensure_dirs()
    live_pid = _pidfile_alive()
    if live_pid is not None:
        print(f"JMD 控制台已在运行（pid {live_pid}），拒绝重复启动", file=sys.stderr)
        return 2
    _write_pidfile()
    atexit.register(_remove_pidfile)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"JMD 控制台: http://{args.host}:{args.port}/  (editor: /editor.html)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
