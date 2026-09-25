#!/usr/bin/env python3
"""Collect Xunlei cloud-share links from pages, pasted text, or a Pansou API.

Scripted search engines do not work from this network (Bing answers with an
endless redirect loop for datacenter/VPN exits), so *finding* a resource page
stays a browser task. Everything after that is here: turn a resource page, a
pasted blob, or a Pansou-compatible API response into scored pan candidates.

    pan_search.py --page URL --title "剧名"          # resource page -> candidates
    pan_search.py --text-file /tmp/page.txt ...      # already-copied page text
    pan_search.py --pansou http://localhost:53460 ...  # Pansou instance

Output feeds `pan_pool.py` / `probe_magnets.py --extra-candidates`.
"""

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from pan_pool import SHARE_PATTERN, parse_share_text, share_candidates

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
PASSWORD_HINTS = [
    r"(?:提取码|密\s*码|访问码|pwd|pass(?:word)?)\s*[:：=]?\s*([0-9A-Za-z]{4,8})",
]
TITLE_HINTS = [
    r"<title>(.*?)</title>",
    r"<h1[^>]*>(.*?)</h1>",
]


def clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = (
        text.replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
    )
    return re.sub(r"\s+", " ", text).strip()


def extract_share_entries(html, title_hint=""):
    """Find every pan share link plus any nearby pass code."""
    text = clean_text(html)
    entries = []
    seen = set()
    for match in SHARE_PATTERN.finditer(text):
        url = match.group(0)
        # SHARE_PATTERN stops at the id, so a "?pwd=xxxx" tail is part of the
        # same link and has to be captured explicitly.
        tail = text[match.end() : match.end() + 48]
        tail_match = re.match(r"\?[^\s\"'<>)]*", tail)
        if tail_match:
            url = url + tail_match.group(0)
        if url in seen:
            continue
        seen.add(url)
        window = text[max(match.start() - 120, 0) : match.end() + 160]
        # A code carried in the URL itself always wins over one that merely
        # sits near it in the page text.
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        code = (query.get("pwd") or [""])[0]
        if not code:
            for pattern in PASSWORD_HINTS:
                code_match = re.search(pattern, window, re.IGNORECASE)
                if code_match:
                    code = code_match.group(1)
                    break
        entries.append(
            {
                "share_url": url,
                "share_id": match.group(1),
                "pass_code": code,
                "snippet": window[:160],
            }
        )
    return entries


def page_title(html):
    for pattern in TITLE_HINTS:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(1))[:120]
    return ""


def fetch_page(url, timeout=25):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    charset = "utf-8"
    head = raw[:2048].decode("utf-8", "replace").lower()
    match = re.search(r'charset=["\']?([a-z0-9-]+)', head)
    if match:
        charset = match.group(1)
    return raw.decode(charset, "replace")


def pansou_search(base_url, keyword, timeout=25, limit=50):
    url = (
        f"{base_url.rstrip('/')}/api/search?kw={urllib.parse.quote(keyword)}"
        "&res=merge&src=all"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    merged = (payload.get("data") or {}).get("merged_by_type") or {}
    entries = []
    for kind, items in merged.items():
        for item in (items or [])[:limit]:
            raw_url = item.get("url") or ""
            if "pan.xunlei.com/s/" in raw_url:
                parsed = parse_share_text(raw_url)
                if parsed:
                    note = clean_text(item.get("note") or "")
                    entries.append(
                        {
                            # The note is the release name (resolution, source,
                            # subtitle tags); score on it, not on the bare title.
                            "name": note or "",
                            "share_url": parsed[0]["share_url"],
                            "share_id": parsed[0]["share_id"],
                            "pass_code": item.get("password")
                            or parsed[0]["pass_code"],
                            "snippet": note[:160],
                            "source": f"pansou:{kind}",
                        }
                    )
    return entries


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("title")
    parser.add_argument("--page", action="append", default=[], help="资源页 URL，可重复")
    parser.add_argument("--text-file", action="append", default=[])
    parser.add_argument("--text", default="")
    parser.add_argument(
        "--pansou",
        default=os.environ.get("PANSOU_BASE", "http://127.0.0.1:64056"),
        help="Pansou 实例地址（默认本机 64056）",
    )
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--candidates-out", default="")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--pool-file", default="")
    parser.add_argument("--no-pool", action="store_true")
    parser.add_argument("--size-gb", type=float, default=0.0, help="已知总大小（可选）")
    return parser.parse_args()


def main():
    args = parse_args()
    found = []
    report = {"title": args.title, "pages": [], "errors": []}

    for url in args.page:
        try:
            html = fetch_page(url, args.timeout)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            report["errors"].append({"url": url, "error": str(exc)})
            print(f"PAGE_ERROR {url} {exc}")
            continue
        entries = extract_share_entries(html)
        for entry in entries:
            entry.setdefault("snippet", "")
            entry["source"] = url
        found.extend(entries)
        report["pages"].append(
            {
                "url": url,
                "title": page_title(html),
                "bytes": len(html),
                "links": len(entries),
            }
        )
        print(f"  {url[:70]} -> {len(entries)} 个云盘链接 ({page_title(html)[:40]})")

    for path in args.text_file:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                blob = handle.read()
        except OSError as exc:
            report["errors"].append({"file": path, "error": str(exc)})
            continue
        entries = extract_share_entries(blob)
        for entry in entries:
            entry["source"] = path
        found.extend(entries)
        print(f"  {path} -> {len(entries)} 个云盘链接")

    if args.text:
        entries = extract_share_entries(args.text)
        for entry in entries:
            entry["source"] = "text"
        found.extend(entries)

    if args.pansou:
        try:
            entries = pansou_search(args.pansou, args.title, args.timeout)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            report["errors"].append({"pansou": args.pansou, "error": str(exc)})
            print(f"PANSOU_ERROR {exc}")
            entries = []
        found.extend(entries)
        print(f"  pansou {args.pansou} -> {len(entries)} 个云盘链接")

    deduped = []
    seen = set()
    for entry in found:
        # Pansou sometimes returns a truncated share id next to the full one
        # (e.g. "VOtW9RI9z" for "VOtW9RI9z-lLnIVMqN-MCjPTA1"); a real id is
        # long, so short ones are dropped before they reach scoring.
        if len(entry.get("share_id") or "") < 16:
            continue
        key = entry["share_url"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    full_ids = [entry["share_id"] for entry in deduped]
    deduped = [
        entry
        for entry in deduped
        if not any(
            other != entry["share_id"] and other.startswith(entry["share_id"])
            for other in full_ids
        )
    ]

    candidates = share_candidates(
        [
            {
                "name": entry.get("name") or args.title,
                "share_url": entry["share_url"],
                "pass_code": entry.get("pass_code", ""),
                "size_gb": args.size_gb,
            }
            for entry in deduped
        ]
    )
    for candidate, entry in zip(candidates, deduped):
        candidate["snippet"] = entry.get("snippet", "")
        candidate["found_via"] = entry.get("source", "")

    report["candidates"] = candidates
    report["links"] = deduped

    if args.candidates_out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.candidates_out)), exist_ok=True)
            with open(args.candidates_out, "w", encoding="utf-8") as handle:
                json.dump(candidates, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            print(f"CANDIDATE_WRITE_SKIPPED {exc}")

    if args.pool_file and not args.no_pool:
        from pan_pool import load_pool, save_pool

        pool = load_pool(args.pool_file)
        for candidate in candidates:
            entry = pool.setdefault(candidate["id"], {})
            entry.update(candidate)
        save_pool(args.pool_file, pool, args.title)

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            print(f"JSON_WRITE_SKIPPED {exc}")

    for index, entry in enumerate(deduped, start=1):
        print(
            f"{index:>2} {entry['share_url'][:60]} 提取码={entry.get('pass_code') or '-'} "
            f"{entry.get('snippet', '')[:50]}"
        )
    print(
        "PAN_SEARCH_RESULT "
        + json.dumps(
            {
                "title": args.title,
                "links": len(deduped),
                "candidates": len(candidates),
                "candidates_out": args.candidates_out,
                "errors": len(report["errors"]),
            },
            ensure_ascii=False,
        )
    )
    if not candidates:
        print(
            "NO_PAN_LINK 页面里没有 pan.xunlei.com 分享链接；"
            "用浏览器搜索资源站后再把页面 URL 或整页文本喂进来"
        )
        return 4
    print(
        "下一步: python3 scripts/probe_magnets.py \"{title}\" --extra-candidates "
        "{file} --json-out /tmp/{title}_pool.json".format(
            title=args.title, file=args.candidates_out or "/tmp/pan_candidates.json"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
