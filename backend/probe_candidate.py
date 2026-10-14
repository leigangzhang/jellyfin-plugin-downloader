"""候选清单的读取与解析：候选行解析、--candidate-file / --extra-candidates。

只把外部文本变成 (来源, info hash) 并做结构过滤，不碰网络、不碰池文件。
"""
import base64
import json
import re
import urllib.parse

from candidate_score import structural_reject
from media_download_lib import GB
from probe_health import parse_magnet


def decode_thunder_link(value):
    raw = value.split("://", 1)[1]
    raw += "=" * (-len(raw) % 4)
    decoded = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    if decoded.startswith("AA") and decoded.endswith("ZZ"):
        decoded = decoded[2:-2]
    return decoded


def parse_candidate(value):
    value = value.strip()
    if not value or value.startswith("#"):
        return None
    if value.startswith("magnet:"):
        return parse_magnet(value)
    if value.lower().startswith("thunder://"):
        try:
            decoded = decode_thunder_link(value)
        except (ValueError, UnicodeError):
            return None
        if decoded.startswith("magnet:"):
            return parse_magnet(decoded)
        if re.fullmatch(r"[A-Fa-f0-9]{40}", decoded):
            return decoded
        return None
    if re.fullmatch(r"[A-Fa-f0-9]{40}", value):
        return value
    return None


def gather_candidates(args):
    """读取 search_pool 写下的候选清单（每行一个磁力或 info hash）。

    只剩这一个入口：这次简化删掉了浏览器搜磁力（`--magnet` / `--info-hash`）与本地
    Pansou 两条路。L1 取源的结果由 `--candidate-file` 送进来，云盘候选由
    `--extra-candidates` 送进来。
    """
    direct = []
    if args.candidate_file:
        try:
            with open(args.candidate_file, "r", encoding="utf-8") as handle:
                direct.extend(
                    line.strip()
                    for line in handle
                    if line.strip() and not line.lstrip().startswith("#")
                )
        except OSError as exc:
            print(f"CANDIDATE_FILE_ERROR {exc}")
            return None

    extracted = []
    skipped = []
    for index, candidate in enumerate(direct):
        info_hash = parse_candidate(candidate)
        if info_hash:
            extracted.append((f"direct#{index}", info_hash))
        else:
            skipped.append(candidate)

    # 解析不出来的行要报数：删掉 `--magnet`（thunder:// 直链的唯一入口）之后，直链不再
    # 有专属提示，静默丢掉会让「文件里有几行但一条候选都没有」变得不可解释。
    if skipped:
        print(
            "CANDIDATE_LINES_SKIPPED "
            + json.dumps(
                {
                    "count": len(skipped),
                    # 只报数，不把用户文件里的行抄进日志（可能含本机路径）
                    "reason": "既不是磁力也不是 40 位 info hash 的行会被跳过",
                },
                ensure_ascii=False,
            )
        )
    return extracted


def dedupe(extracted):
    """Keep one entry per info hash, remembering which sources found it."""
    order = []
    sources = {}
    for source, info_hash in extracted:
        info_hash = info_hash.upper()
        if info_hash not in sources:
            order.append(info_hash)
            sources[info_hash] = []
        sources[info_hash].append(source)
    return order, sources


def load_extra_candidates(path, policy):
    """Load cloud-drive (or other non-magnet) candidates from a JSON list."""
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"EXTRA_CANDIDATE_ERROR {exc}")
        return []
    items = payload.get("candidates") if isinstance(payload, dict) else payload
    candidates = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if structural_reject(item, policy):
            print(
                "  EXTRA_SKIP "
                + json.dumps(
                    {
                        "name": str(item.get("name"))[:60],
                        "reason": structural_reject(item, policy),
                    },
                    ensure_ascii=False,
                )
            )
            continue
        candidate = dict(item)
        if policy.get("duration_seconds") and candidate.get("main_video_gb"):
            candidate["estimated_bitrate_mbps"] = (
                candidate["main_video_gb"] * GB * 8
                / policy["duration_seconds"]
                / 1e6
            )
        else:
            candidate.setdefault("estimated_bitrate_mbps", None)
        candidates.append(candidate)
    if candidates:
        print(f"合并 {len(candidates)} 个云盘/非磁力候选进入同一轮打分")
    return candidates
