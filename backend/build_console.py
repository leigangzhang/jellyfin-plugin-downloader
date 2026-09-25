#!/usr/bin/env python3
"""Jellyfin Downloader 独立后端 —— 数据层。

职责：把主 skill 的 probe 输出（ranked/best/rejected/breakdown）、迅雷任务、
watcher 状态与人工标记合并成一个快照 JSON，供网页决策面板渲染。

原则：分数唯一来源是主 skill 的 probe 输出，本层不重算、不漂移；迅雷任务与
watcher 只读；人工标记只写本后端的 state/ 目录。

本文件与其依赖脚本（media_download_lib / candidate_score 等）同目录，已与
`~/.agents/skills` 完全剥离，作为插件自带的独立后端运行。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONSOLE_DIR = HERE

# 依赖脚本与本文件同目录；不再回退到任何 skill 目录。
MAIN_SCRIPTS = os.path.abspath(os.environ.get("JMD_MAIN_SCRIPTS") or HERE)
if MAIN_SCRIPTS not in sys.path:
    sys.path.insert(0, MAIN_SCRIPTS)

from media_download_lib import (  # noqa: E402
    DATA_ROOT,
    MEDIA_ROOT,
    STAGING_ROOT,
    POOL_DIR,
    STATE_DIR as WATCHES_DIR,
    connect_db,
    task_status,
    cloud_task_status,
    task_file_names,
    active_download_snapshot,
    read_watcher_state,
    path_is_within,
)
from candidate_score import score_candidate  # noqa: E402

# marks/snapshots 与 pool/watches/pan 同属插件自己的数据根（JMD_DATA_DIR
# 可整体搬走；JMD_CONSOLE_STATE_DIR 仍可只覆盖这一部分）
CONSOLE_STATE_DIR = os.path.abspath(
    os.environ.get("JMD_CONSOLE_STATE_DIR") or DATA_ROOT
)
MARKS_DIR = os.path.join(CONSOLE_STATE_DIR, "marks")
SNAPSHOTS_DIR = os.path.join(CONSOLE_STATE_DIR, "snapshots")
WEB_DIR = os.path.join(HERE, "web")
PUBLISH_HISTORY = os.path.join(WEB_DIR, ".publish_history")

VALID_KINDS = ("movie", "episode", "variety", "record")
KIND_TO_CATEGORY = {
    "movie": "Movies",
    "episode": "TV Shows",
    "variety": "Shows",
    "record": "Records",
}
# probe_magnets 只认 movie/episode，综艺按剧集、纪录片按电影兜底。
PROBE_KIND = {
    "movie": "movie",
    "episode": "episode",
    "variety": "episode",
    "record": "movie",
}
DEFAULT_DURATION = {"movie": 120, "episode": 50, "variety": 50, "record": 90}

MARK_SET = {"pending", "arbitrate", "dead", "submit", "done", "skip"}
IH_RE = re.compile(r"^[0-9A-Fa-f]{40}$")
# 允许「.」以便季级命名空间（王冠.s01），但仍拒绝路径穿越（".."）
SAFE_SLUG_RE = re.compile(r"^[0-9A-Za-z\u4e00-\u9fff][0-9A-Za-z\u4e00-\u9fff\-\.]*$")
EPISODE_RE = re.compile(r"(?:S(\d{1,2}))?[\s._-]*E(?:P)?(\d{1,3})|第\s*(\d{1,3})\s*集", re.IGNORECASE)
SEASON_CODE_RE = re.compile(r"(?:^|[^0-9A-Za-z])S(\d{1,2})(?!\d)", re.IGNORECASE)
SEASON_CN_RE = re.compile(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*季")
SEASON_EN_RE = re.compile(r"[Ss]eason\s*(\d{1,2})", re.IGNORECASE)
CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
CN_NUMERALS = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}


def slugify(text: str) -> str:
    """与主 skill 一致的 slug 规则：只保留字母/数字/中文，其余折叠为 '-'。"""
    if not text:
        return "pool"
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-")
    return (slug or "pool")[:60]


def valid_ih(value) -> bool:
    return isinstance(value, str) and bool(IH_RE.match(value))


def valid_slug(value) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 66
        and bool(SAFE_SLUG_RE.match(value))
        and ".." not in value
    )


def snapshot_key(slug: str, season) -> str:
    """季级命名空间：选了季就用 <slug>.s<NN>，未选季仍用 <slug>。

    这样「第 1 季」与「第 2 季」的结果各存一份，互不覆盖。
    """
    if season is None or season == "":
        return slug
    try:
        return f"{slug}.s{int(season):02d}"
    except (TypeError, ValueError):
        return slug


def derive_final_dir(title: str, kind: str, year: str = "") -> str:
    category = KIND_TO_CATEGORY.get(kind, "TV Shows")
    name = f"{title} ({year})" if year else title
    return os.path.join(MEDIA_ROOT, category, name)


def ensure_dirs() -> None:
    for d in (MARKS_DIR, SNAPSHOTS_DIR):
        os.makedirs(d, exist_ok=True)


def read_marks(slug: str) -> dict:
    path = os.path.join(MARKS_DIR, f"{slug}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def write_marks(slug: str, marks: dict) -> None:
    ensure_dirs()
    path = os.path.join(MARKS_DIR, f"{slug}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(marks, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_probe(slug: str) -> dict:
    path = os.path.join(SNAPSHOTS_DIR, f"{slug}.probe.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_probe(slug: str, payload: dict) -> None:
    ensure_dirs()
    path = os.path.join(SNAPSHOTS_DIR, f"{slug}.probe.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_context(slug: str) -> dict:
    path = os.path.join(SNAPSHOTS_DIR, f"{slug}.context.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_context(slug: str, ctx: dict) -> None:
    ensure_dirs()
    path = os.path.join(SNAPSHOTS_DIR, f"{slug}.context.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ctx, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def candidate_key(entry: dict) -> str:
    """候选稳定标识：磁力用 40 位 ih，云盘用 share_url（无则 id/ih）。"""
    ih = str(entry.get("ih") or "")
    if valid_ih(ih):
        return ih.upper()
    return str(entry.get("share_url") or entry.get("id") or ih or "")


def pwd_from_url(url: str) -> str:
    match = re.search(r"[?&]pwd=([0-9A-Za-z]+)", url or "")
    return match.group(1) if match else ""


def read_pan_map(slug: str) -> dict:
    """读取 <slug>.pan.json（pan_search 原始输出），拿 share_url -> 提取码。"""
    path = os.path.join(SNAPSHOTS_DIR, f"{slug}.pan.json")
    try:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    mapping = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("share_url") or "")
        if url:
            mapping[url] = str(item.get("pass_code") or "")
    return mapping


def _cn_number(token: str) -> int:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token.startswith("十"):
        return 10 + CN_DIGITS.get(token[1:], 0)
    if token.endswith("十"):
        return CN_DIGITS.get(token[:-1], 0) * 10
    if "十" in token:
        head, _, tail = token.partition("十")
        return CN_DIGITS.get(head, 0) * 10 + CN_DIGITS.get(tail, 0)
    return CN_DIGITS.get(token, 0)


def _candidate_blob(candidate: dict) -> str:
    texts = [str(candidate.get("name") or "")]
    for video in candidate.get("videos") or []:
        if isinstance(video, dict):
            texts.append(str(video.get("name") or video.get("path") or ""))
    return " ".join(texts)


def _normalize_latin(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def title_matches(candidate: dict, title: str, original_title: str = ""):
    """候选是否真的属于这个片名。

    只在「能确定」时才下结论：不确定一律返回 None（保留）。宁可多显示几条，
    也不要把真资源悄悄藏掉。

    * 中文片名只在候选里**也出现汉字**时才判定 —— 否则「王冠」会把
      `The.Crown.S01.1080p` 这种英文名资源误判成噪音（Jellyfin 条目常常没有
      OriginalTitle）。判定按「CJK 边界」：前后紧邻其它汉字视为不同作品，
      《空王冠》《罪恶王冠》《9-nine-支配者的王冠》排除，
      「【某站发布】王冠 第一季」仍算匹配。
    * 英文原名按归一化子串判定（The.Crown → thecrown）。
    """
    blob = _candidate_blob(candidate)
    if not blob:
        return None
    title_cjk = bool(re.search(r"[\u4e00-\u9fff]", title or ""))
    blob_cjk = bool(re.search(r"[\u4e00-\u9fff]", blob))
    latin = _normalize_latin(blob)
    checked = False
    if title:
        if title_cjk:
            if blob_cjk:
                checked = True
                pattern = r"(?<![\u4e00-\u9fff])" + re.escape(title) + r"(?![\u4e00-\u9fff])"
                if re.search(pattern, blob):
                    return True
        else:
            normalized = _normalize_latin(title)
            if len(normalized) >= 3 and len(latin) >= 3:
                checked = True
                if normalized in latin:
                    return True
    if original_title:
        normalized = _normalize_latin(original_title)
        if len(normalized) >= 3:
            checked = True
            if normalized in latin:
                return True
    if not checked:
        return None
    return False


def candidate_seasons(candidate: dict) -> list:
    """从候选名/文件清单里推出它覆盖的季号；空列表 = 整剧/未知。"""
    texts = [str(candidate.get("name") or "")]
    for video in candidate.get("videos") or []:
        if isinstance(video, dict):
            texts.append(str(video.get("name") or video.get("path") or ""))
    blob = " ".join(texts)
    seasons = set()
    for match in SEASON_CODE_RE.finditer(blob):
        seasons.add(int(match.group(1)))
    for match in SEASON_CN_RE.finditer(blob):
        number = _cn_number(match.group(1))
        if number:
            seasons.add(number)
    for match in SEASON_EN_RE.finditer(blob):
        seasons.add(int(match.group(1)))
    return sorted(seasons)


def episode_markers(candidate: dict) -> list:
    """从候选名/文件清单里抽出 (季, 集) 标记；季为 None = 名字里没写季。"""
    blob = _candidate_blob(candidate)
    markers = []
    for match in EPISODE_RE.finditer(blob):
        if match.group(3) is not None:
            markers.append((None, int(match.group(3))))
        else:
            markers.append((int(match.group(1)) if match.group(1) else None, int(match.group(2))))
    return markers


def covers_episode(candidate: dict, season: int, episode: int):
    """判断候选是否覆盖指定单集：明确提到本集→True；明确提到别的集→False；无集号（整包）→True。"""
    markers = episode_markers(candidate)
    if not markers:
        return True
    for marker_season, marker_episode in markers:
        if marker_episode != episode:
            continue
        if marker_season is None or marker_season == season:
            return True
    return False


def single_episode_match(candidate: dict, season: int, episode: int) -> bool:
    """候选是否「只含目标这一集」。

    必须有集号，且所有集号都指向目标集：`[第01集]` / `S01E01` → True；
    整包（无集号）→ False；提到别集 → False。

    `covers_episode` 说的是「是否**覆盖**本集」，整包也算覆盖，所以单集页
    只看它的话结果全是整包。这个标记用来把「仅本集」排到整包前面并区分显示，
    不改变 `covers_episode` 的语义。
    """
    markers = episode_markers(candidate)
    if not markers:
        return False
    for marker_season, marker_episode in markers:
        if marker_episode != episode:
            return False
        if marker_season is not None and marker_season != season:
            return False
    return True


def list_thunder_tasks() -> list:
    """列出迅雷 etm_task 全部任务（BT + 云盘取回），供控制台展示。"""
    try:
        conn = connect_db()
    except Exception:
        return []
    try:
        conn.row_factory = None
        conn.execute("PRAGMA busy_timeout=1500")
        cur = conn.cursor()
        cur.execute(
            "SELECT taskid, state, create_param, bt_task_info "
            "FROM etm_task ORDER BY taskid DESC"
        )
        rows = [
            {"taskid": r[0], "state": r[1], "create_param": r[2], "bt_task_info": r[3]}
            for r in cur.fetchall()
        ]
    except Exception:
        return []
    finally:
        conn.close()

    tasks = []
    for row in rows:
        param = json.loads(row.get("create_param") or "{}")
        source = str(param.get("source") or "")
        ih = str(param.get("info_hash") or "").upper()
        names = task_file_names(row)
        file_name = param.get("file_name") or (names[0] if names else "")
        download_path = param.get("download_path") or ""
        if source.startswith("cloud/"):
            st = cloud_task_status(row)
            entry = {
                "taskid": row["taskid"],
                "state": row.get("state"),
                "info_hash": ih,
                "file_name": file_name,
                "download_path": download_path,
                "source": source,
                "kind": "cloud",
                "pct": round(st["pct"], 2) if st else 0.0,
                "speed": 0,
                "done": st["done"] if st else 0,
                "total": st["expected"] if st else 0,
            }
        else:
            st = task_status(row)
            entry = {
                "taskid": row["taskid"],
                "state": row.get("state"),
                "info_hash": ih,
                "file_name": file_name,
                "download_path": download_path,
                "source": source,
                "kind": "bt",
                "pct": round(st["pct"], 2) if st else 0.0,
                "speed": st["speed"] if st else 0,
                "done": st["done"] if st else 0,
                "total": st["total"] if st else 0,
            }
        total, done, speed = entry["total"], entry["done"], entry["speed"]
        entry["eta_hours"] = (
            round((total - done) / speed / 3600.0, 2)
            if (total > done and speed > 0)
            else None
        )
        tasks.append(entry)
    return tasks


def list_watches() -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(WATCHES_DIR, "*.json"))):
        base = os.path.basename(path)
        ih = os.path.splitext(base)[0]
        state = read_watcher_state(ih)
        if state:
            state.setdefault("info_hash", ih)
            out.append(state)
    return out


def rescore_entry(probe: dict, key: str, measured_speed_mbps: float) -> dict | None:
    """用实测速度回填单个候选并按同一套 policy 重算分，返回更新后的候选。"""
    policy = probe.get("policy") or {}
    for entry in probe.get("ranked") or []:
        if candidate_key(entry) != key:
            continue
        cand = dict(entry)
        cand["measured_speed_mbps"] = float(measured_speed_mbps)
        try:
            result = score_candidate(cand, policy)
        except Exception:
            return None
        cand["score"] = result["score"]
        cand["dimensions"] = result["dimensions"]
        cand["breakdown"] = result["breakdown"]
        cand["provisional"] = result.get("provisional", False)
        min_score = probe.get("min_score") or 45
        cand["band"] = (
            "优秀" if result["score"] >= 75 else
            "良好" if result["score"] >= 60 else
            "可接受" if result["score"] >= min_score else "不推荐"
        )
        return cand
    return None


def build_snapshot(slug: str, probe: dict | None = None, ctx: dict | None = None) -> dict:
    probe = probe if probe is not None else read_probe(slug)
    ctx = ctx if ctx is not None else read_context(slug)
    marks = read_marks(slug)
    ranked = probe.get("ranked") or []
    pan_codes = read_pan_map(slug)
    snapshot_title = ctx.get("title") or probe.get("query") or ""
    snapshot_original = ctx.get("original_title") or ""

    candidates = []
    for entry in ranked:
        cand = dict(entry)
        key = candidate_key(cand)
        cand["key"] = key
        cand["mark"] = marks.get(key, "")
        cand["seasons"] = candidate_seasons(cand)
        cand["title_match"] = title_matches(cand, snapshot_title, snapshot_original)
        if cand.get("kind") == "pan":
            url = str(cand.get("share_url") or "")
            cand["pass_code"] = pan_codes.get(url) or pwd_from_url(url)
        if ctx.get("season") is not None and ctx.get("episode") is not None:
            season_number = int(ctx["season"])
            episode_number = int(ctx["episode"])
            cand["covers_episode"] = covers_episode(cand, season_number, episode_number)
            cand["single_episode"] = single_episode_match(cand, season_number, episode_number)
        candidates.append(cand)

    try:
        active = active_download_snapshot()
    except Exception:
        active = {}

    return {
        "slug": slug,
        "query": probe.get("query") or ctx.get("title") or "",
        "kind": probe.get("kind") or ctx.get("kind") or "episode",
        "year": ctx.get("year", ""),
        "season": ctx.get("season"),
        "episode": ctx.get("episode"),
        "duration_minutes": ctx.get("duration_minutes"),
        "final_dir": ctx.get("final_dir", ""),
        "sources": ctx.get("sources", ""),
        "strict_season": ctx.get("strict_season", True),
        "exists": ctx.get("exists") or {},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "candidates": candidates,
        "best": probe.get("best"),
        "best_below_min_score": probe.get("best_below_min_score"),
        "best_dead_swarm": probe.get("best_dead_swarm"),
        "rejected": probe.get("rejected") or [],
        "policy": probe.get("policy") or {},
        "min_score": probe.get("min_score"),
        "pool_file": probe.get("pool_file") or "",
        "search_status": ctx.get("search_status", ""),
        "tasks": list_thunder_tasks(),
        "watches": list_watches(),
        "active": active,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="构建控制台快照（读最新 probe 快照）")
    parser.add_argument("title", nargs="?", default="")
    parser.add_argument("--kind", choices=VALID_KINDS, default="episode")
    parser.add_argument("--year", default="")
    parser.add_argument("--duration-minutes", type=float, default=None)
    parser.add_argument("--probe-json", default="", help="直接读一个 probe --json-out 文件")
    parser.add_argument("--json-out", default="", help="把快照写到指定路径")
    args = parser.parse_args()

    if args.probe_json:
        try:
            with open(args.probe_json, encoding="utf-8") as fh:
                probe = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"PROBE_READ_ERROR {exc}")
            return 2
        slug = slugify(probe.get("query") or args.title)
        save_probe(slug, probe)
    else:
        slug = slugify(args.title)
        probe = None

    ctx = read_context(slug)
    if args.kind and not ctx.get("kind"):
        ctx["kind"] = args.kind
    if args.year and not ctx.get("year"):
        ctx["year"] = args.year
    if args.duration_minutes and not ctx.get("duration_minutes"):
        ctx["duration_minutes"] = args.duration_minutes
    if args.title and not ctx.get("title"):
        ctx["title"] = args.title
        ctx["final_dir"] = ctx.get("final_dir") or derive_final_dir(
            args.title, args.kind, args.year
        )
    save_context(slug, ctx)

    snapshot = build_snapshot(slug, probe=probe, ctx=ctx)
    ensure_dirs()
    out_path = args.json_out or os.path.join(SNAPSHOTS_DIR, f"{slug}.json")
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)
    print(f"SNAPSHOT {out_path} candidates={len(snapshot['candidates'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
