"""控制台的 HTTP 面：路由、处理器基类、静态资源与各 API 端点。

Handler 的实现从 `backend/console_server.py` 搬来。入口以它的子类把 service 信封
钉在入口（`{**obj, "service": SERVICE_ID}` 这个字面量按契约留在入口文件里），并把
两处接线接回入口 —— 见下面 `valid_target_dir` / `dispatch_search` 的说明。
"""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import build_console as bc
from media_download_lib import MEDIA_ROOT, STAGING_ROOT, path_is_within

from trending import trending_cache, trending_source, trending_watchlist

from . import jobs as jobs_mod
from . import probe_state
from . import service


# 无 TMDb Key 时的引导文案：热门榜单的数据源是 TMDb，没有 key 就只能把入口指到
# 配置页。路由在这个分支里**不触网**（见 `_get_trending`）。
TRENDING_GUIDANCE = (
    "未配置 TMDb API Key，热门榜单暂不可用："
    "请到 控制台 → 插件 → Jellyfin Downloader 填 TMDb API Key。"
)


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


def dispatch_search(jid, params):
    """把一轮搜索丢进后台线程；入口在组合根里替换它（包单独用时明确报错）。"""
    raise RuntimeError("搜索调度未接线：请经 backend/console_server.py 入口启动服务")


def _publisher_only_from_config(kind: str) -> bool:
    """按类型取「单源」缺省值；`source_config` 的接口没就绪就退回 True。

    退回 True 是**修复前的行为**：接口缺席时宁可少一个按类型的优待，也不要让
    整个搜索变成 500。接口在、但形态不符（签名变了、抛错）同样退回，只是会打一行
    WARNING 留痕 —— 静默退回会让这个接线坏了半年都没人知道。
    """
    try:
        import source_config
    except ImportError:
        return True
    resolve = getattr(source_config, "publisher_only_default", None)
    if resolve is None:
        return True
    try:
        return bool(resolve(kind))
    except Exception as exc:  # noqa: BLE001 —— 接口坏了不能拖垮搜索，但必须留痕
        print(f"WARNING publisher_only_default({kind!r}) 调用失败：{exc!r}", flush=True)
        return True


def resolve_publisher_only(kind: str, body: dict) -> bool:
    """单源开关的取值顺序：请求显式给了就以请求为准，没给才问按类型的缺省。"""
    if "publisher_only" in body:
        return bool(body["publisher_only"])
    return _publisher_only_from_config(kind)


def resolve_year(body: dict, title: str) -> str:
    """年份：请求优先，缺了回落到该条目的搜索上下文（`<slug>.context.json`）。

    实测（F2，2026-10-15）：面板提交时 `year` 可能是空串，`final_dir` 因此丢掉
    `(年份)`，归档目录与迅雷任务目录对不上。两处都没有就返回空串 —— 不编年份。
    """
    year = str(body.get("year") or "").strip()
    if year:
        return year
    if not title:
        return ""
    try:
        ctx = bc.read_context(bc.slugify(title)) or {}
    except Exception as exc:  # noqa: BLE001 —— 读不到上下文不该让提交失败
        print(f"WARNING 读条目上下文取年份失败：{exc!r}", flush=True)
        return ""
    return str(ctx.get("year") or "").strip()


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
        if path == "/api/snapshot":
            return self._get_snapshot(parsed)
        if path == "/api/search/status":
            return self._get_search_status(parsed)
        if path == "/api/trending":
            return self._get_trending(parsed)
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
        if path == "/api/trending/watch":
            return self._api_trending_watch(body)
        if path.startswith("/api/pan/"):
            sub = path[len("/api/pan/"):]
            return self._api_pan(sub, body)
        return self._send(404, {"error": "not found"})

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
        # 归一化后再判合法性与查缺省值：`" Movie "` 与 `"movie"` 必须是同一个答案
        kind = str(body.get("kind") or "episode").strip().lower()
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
        # 缺省值由 `source_config.publisher_only_default(kind)` 按**类型**决定
        # （拿不到那个接口时退回 True = 修复前的行为）；请求显式传了
        # `"publisher_only": true/false` 就以请求为准 —— 关键字没有资源的片名
        # 仍可用它单次关掉，空结果会被打印成 PUBLISHER_ONLY_EMPTY。
        publisher_only = resolve_publisher_only(kind, body)
        final_dir = body.get("final_dir") or bc.derive_final_dir(title, kind, year)
        if not valid_target_dir(final_dir):
            return self._bad("final_dir must be inside staging or Media")

        # 候选文件按**本轮**命名：同名标题连开两轮（同一季重搜、或不同季但同一个 slug）
        # 不会互相读到对方的候选。原来固定 `/tmp/<slug>.magnets`，只能靠 mtime 判断
        # 「是不是本轮写的」，而两个写者并发时那条判定就失效了（复核第二轮 F3）。
        # 首行与心跳在建立时就写好（含 title/kind）：客户端拿到 job_id 后第一次轮询
        # 就有内容，不会停在「正在连接后端…」（真机 job 空壳复现，见 jobs.py 顶部）。
        jid = jobs_mod._new_job(title, kind)
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
        # 心跳字段（started_at / updated_at 是单调时钟）以外再给一个能直接看的
        # stale_seconds；旧字段一个不动。
        resp["stale_seconds"] = round(time.monotonic() - float(job.get("updated_at") or 0.0), 1)
        return self._ok(resp)

    # ---- trending ---------------------------------------------------------
    def _get_trending(self, parsed):
        """热门榜单：TMDb 榜单海报墙的数据面。

        query：`language`（缺省 zh-CN）、`lists`（逗号分隔榜单 id，缺省全部四个，
        未知 id 忽略）。`JMD_TMDB_API_KEY` 为空/缺失时返回引导文案且**不触网** ——
        没有 key 的请求注定 401，发出去只是白等一次超时。

        有 key 时：缓存/取源的任何异常都降级成「空榜单 + stale」，绝不 500 ——
        热门榜是锦上添花，它的失败不该让面板整体报错。响应形状固定四个键
        （lists / stale / guidance / watched），客户端按它渲染。
        """
        q = parse_qs(parsed.query)
        language = (q.get("language") or [""])[0].strip() or trending_source.DEFAULT_LANGUAGE
        requested = [
            part.strip()
            for part in ((q.get("lists") or [""])[0]).split(",")
            if part.strip()
        ]
        list_ids = [
            list_id
            for list_id in (requested or trending_source.LIST_ORDER)
            if list_id in trending_source.LISTS
        ]
        # 已关注集合与榜单无关：没有 key 时也要回给客户端（媒体库占位靠它）。
        watched = trending_watchlist.watchlist_ids()
        api_key = (os.environ.get("JMD_TMDB_API_KEY") or "").strip()
        if not api_key:
            return self._ok(
                {
                    "lists": [],
                    "stale": False,
                    "guidance": TRENDING_GUIDANCE,
                    "watched": watched,
                }
            )
        try:
            result = trending_cache.load_lists(language, list_ids, api_key)
        except Exception as exc:  # noqa: BLE001 —— 取热门不该让面板拿到 500
            print(f"WARNING 取热门榜单失败：{exc!r}", flush=True)
            return self._ok(
                {"lists": [], "stale": True, "guidance": None, "watched": watched}
            )
        if result.get("error"):
            print(f"WARNING 热门榜单降级：{result['error']}", flush=True)
        return self._ok(
            {
                "lists": [
                    {
                        "id": item.get("id"),
                        "label": item.get("label") or item.get("id"),
                        "items": item.get("items") or [],
                    }
                    for item in (result.get("lists") or [])
                ],
                "stale": bool(result.get("stale")),
                "guidance": None,
                "watched": watched,
            }
        )

    # ---- verify -----------------------------------------------------------
    def _api_submit(self, body):
        ih = (body.get("ih") or "").strip()
        final_dir = body.get("final_dir") or ""
        kind = str(body.get("kind") or "episode").strip().lower()
        if not bc.valid_ih(ih):
            return self._bad("invalid info hash")
        series_name = (body.get("series_name") or "").strip()
        # 年份可能在请求里、也可能只在条目上下文里；两处都看，否则 final_dir
        # 会丢掉 (年份)（F2 实测载荷恒为 "year":""）。
        year = resolve_year(body, series_name)
        category = (body.get("category") or "").strip()
        if category:
            if category not in bc.CATEGORIES:
                return self._bad("invalid category")
            final_dir = bc.derive_final_dir(series_name, kind, year, category)
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
        if series_name:
            cmd += ["--series-name", series_name]
        if year:
            cmd += ["--year", year]
        # 逃生开关：缺省 false，命令行与修复前逐字一致（向后兼容）；true 时由
        # submit_xunlei.py 决定「元数据窗口拿不到文件列表就按磁力直连放行」。
        if bool(body.get("trust_magnet", False)):
            cmd += ["--trust-magnet"]
        rc, out, err = service.run_cli(cmd, 900)
        return self._ok({"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err})

    def _api_watch(self, body):
        ih = (body.get("ih") or "").strip()
        final_dir = body.get("final_dir") or ""
        if not bc.valid_ih(ih):
            return self._bad("invalid info hash")
        series_name = (body.get("series_name") or "").strip()
        # 年份与 /api/submit 同源：都走这一个 resolve_year（请求优先、缺了回落到
        # 条目上下文）。t24（P1b）实测面板给 watch 传空 year 时，watcher 的监控/
        # 归档目录会丢掉 (年份)，与 submit 的落点对不上 —— 修 submit 时漏了这里。
        year = resolve_year(body, series_name)
        category = (body.get("category") or "").strip()
        if category:
            if category not in bc.CATEGORIES:
                return self._bad("invalid category")
            final_dir = bc.derive_final_dir(
                series_name,
                body.get("kind") or "episode",
                year,
                category,
            )
        if not valid_target_dir(final_dir):
            return self._bad("final_dir must be inside staging or Media")
        interval = int(body.get("interval") or 900)
        cmd = ["download/watch_download.py", ih, final_dir, "--detach", "--interval", str(interval)]
        if series_name:
            cmd += ["--series-name", series_name]
        if year:
            cmd += ["--year", year]
        rc, out, err = service.run_cli(cmd, 60)
        return self._ok({"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err})

    def _api_trending_watch(self, body):
        """关注一条热门条目：幂等写 `trending/watchlist.json`。

        `tmdb_id` 是唯一必填项（整数或数字串）；重复关注只回 `exists=True`，
        不重写文件（见 `trending_watchlist.add_watch`）。标题/年份/类型是客户端
        顺手带的展示字段，缺了就存空值 —— 不等于关注失败。
        """
        if not isinstance(body, dict):
            return self._bad("invalid json body")
        raw = body.get("tmdb_id")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return self._bad("tmdb_id is required")
        if isinstance(raw, bool):
            return self._bad("invalid tmdb_id")
        try:
            tmdb_id = int(raw)
        except (TypeError, ValueError):
            return self._bad("invalid tmdb_id")
        try:
            result = trending_watchlist.add_watch(
                {
                    "tmdb_id": tmdb_id,
                    "title": body.get("title"),
                    "year": body.get("year"),
                    "kind": body.get("kind"),
                }
            )
        except ValueError:
            return self._bad("tmdb_id is required")
        except OSError as exc:
            print(f"WARNING 写入关注清单失败：{exc!r}", flush=True)
            return self._send(500, {"error": "failed to write watchlist"})
        return self._ok(
            {
                "ok": True,
                "exists": bool(result.get("exists")),
                "written": bool(result.get("written")),
            }
        )

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
