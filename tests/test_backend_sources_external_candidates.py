"""F1/F2/F3 from the I4 integration review, pinned at search_pool's boundary and
end to end into `probe_magnets`.

Split out of `tests/test_backend_sources.py` by subject; the shared
fakes live in `tests/_backend_sources_helpers.py`..
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests._backend_sources_helpers import (
    NOW,
    diao_payload,
    external_entry,
    l1_torrent,
    load_fixture,
    magnet_for,
    probe_magnets,
    run_main_with_fakes,
    torrents_csv_source,
)

class ExternalCandidateGateTests(unittest.TestCase):
    """F1/F2/F3 from the I4 integration review, pinned at search_pool's boundary."""

    def setUp(self):
        self.l1_hash = "A" * 40
        # 走源模块的 parse 路径（裸 API 记录的 `infohash`/`size_bytes` 不是候选字段）
        self.entries = torrents_csv_source.parse_payload(
            load_fixture("torrents_csv_search.json"), now=NOW)
        self.external = self.entries[0]
        self.external_hash = self.external["info_hash"]

    def test_f1_external_candidates_carry_no_metadata_at(self):
        import tempfile

        files = [{"index": 0, "path": "a.mkv", "size": 8053063680}]
        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "pool.json"
            json_out = Path(tmp) / "out.json"
            code, _ = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--pool-file", str(pool_path),
                 "--json-out", str(json_out), "--candidates-out", str(Path(tmp) / "out.magnets")],
                diao_payload([l1_torrent(self.l1_hash, "A.Release.mkv", files)]),
                [self.external])
            self.assertEqual(code, 0)
            by_hash = {item["ih"]: item for item in
                       json.loads(json_out.read_text(encoding="utf-8"))["candidates"]}
            # 有文件清单的 L1 候选才盖章；外部候选不盖章 → probe 才会去抓 metadata。
            self.assertIn("metadata_at", by_hash[self.l1_hash])
            self.assertNotIn("metadata_at", by_hash[self.external_hash])
            external = by_hash[self.external_hash]
            self.assertEqual(external["videos"], [])
            self.assertEqual(external["external_seeders"], self.external["seeders"])
            entries = json.loads(pool_path.read_text(encoding="utf-8"))["entries"]
            self.assertIn("metadata_at", entries[self.l1_hash])
            self.assertNotIn("metadata_at", entries[self.external_hash])

    def test_f2_merge_keeps_a_known_file_list_and_its_metadata_stamp(self):
        import tempfile

        known = "D" * 40
        old_stamp = NOW - 10 * 86400
        name = "Known.Release.2023.1080p.WEB-DL.x264-GRP.mkv"
        pool_payload = {"version": 1, "query": "x", "updated_at": "x", "entries": {
            known: {"name": name,
                    "videos": [{"index": 0, "path": "p.mkv", "name": "p.mkv",
                                "size": 8053063680}],
                    "main_video_gb": 7.5, "size_gb": 7.5, "nfiles": 1,
                    "main_video_ext": ".mkv", "junk_files": 0, "junk_bytes": 0,
                    "metadata_at": old_stamp, "health_at": old_stamp,
                    "seeds": 30, "peers": 40, "health_method": "announce"}}}
        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "pool.json"
            pool_path.write_text(json.dumps(pool_payload), encoding="utf-8")
            code, _ = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--pool-file", str(pool_path),
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                # 新搜索对这个 hash 只给出名字（空文件清单），外部通道也给同一个 hash
                diao_payload([l1_torrent(known, name)]),
                [external_entry(info_hash=known, name="[index] Known")])
            self.assertEqual(code, 0)
            after = json.loads(pool_path.read_text(encoding="utf-8"))["entries"][known]
            self.assertEqual(after["main_video_gb"], 7.5, "空清单把已知的 main_video_gb 覆盖掉了")
            self.assertTrue(after["videos"], "空清单把已知的 videos 覆盖掉了")
            self.assertEqual(after["nfiles"], 1, "空清单把已知的 nfiles 覆盖掉了")
            self.assertEqual(after["metadata_at"], old_stamp, "metadata_at 被合并刷新了")
            self.assertEqual(after["name"], name, "外部通道改写了已由 L1 描述过的条目标识")
            # 只补 swarm 与来源字段
            self.assertEqual(after["external_seeders"], 12)
            self.assertEqual(after["external_seeders_age_days"], 22.7)
            self.assertEqual(after["external_sources"], ["torrents-csv"])
            # announce 的 seeds 不被外部读数冒充
            self.assertEqual(after["seeds"], 30)

    def test_f2_external_only_candidate_cannot_rewrite_a_verified_entry(self):
        """Only an external candidate for this hash: the verified entry still wins.

        The sibling case above also has an L1 hit for the same hash, so its name
        survives through the L1 candidate. This one isolates main()'s own rule:
        a channel that knows no file list may only add provenance fields.
        """
        import tempfile

        known = "D" * 40
        old_stamp = NOW - 3 * 86400
        name = "Verified.Release.2023.2160p.WEB-DL.mkv"
        pool_payload = {"version": 1, "query": "x", "updated_at": "x", "entries": {
            known: {"name": name,
                    "videos": [{"index": 0, "path": "v.mkv", "name": "v.mkv",
                                "size": 16106127360}],
                    "main_video_gb": 15.0, "size_gb": 15.0, "nfiles": 2,
                    "main_video_ext": ".mkv", "metadata_at": old_stamp, "seeds": 44}}}
        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "pool.json"
            pool_path.write_text(json.dumps(pool_payload), encoding="utf-8")
            code, _ = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--pool-file", str(pool_path),
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(Path(tmp) / "out.magnets")],
                diao_payload([]),
                [external_entry(info_hash=known, name="[index] Known", seeders=7, age_days=22.7)])
            self.assertEqual(code, 0)
            after = json.loads(pool_path.read_text(encoding="utf-8"))["entries"][known]
            self.assertEqual(after["name"], name, "只有名字的索引改写了已核实的条目标识")
            self.assertEqual(after["main_video_gb"], 15.0)
            self.assertTrue(after["videos"])
            self.assertEqual(after["metadata_at"], old_stamp)
            self.assertEqual(after["external_seeders"], 7)
            self.assertEqual(after["external_sources"], ["torrents-csv"])

    def test_f3_strict_season_writes_external_magnets(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cand_out = Path(tmp) / "strict.magnets"
            code, printed = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--strict-match",
                 "--collection-hint", "第1季", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(cand_out)],
                diao_payload([]),
                [self.external])
            self.assertEqual(code, 0)
            result = json.loads(next(line for line in printed.splitlines()
                                     if line.startswith("SEARCH_RESULT")).split(" ", 1)[1])
            magnets = [line for line in cand_out.read_text(encoding="utf-8").splitlines()
                       if line and not line.startswith("#")]
            self.assertIn(magnet_for(self.external_hash), magnets)
            # 候选文件的行数必须与 SEARCH_RESULT 报的候选数一致（F3 的原始症状是
            # external=1 而文件为空）。
            self.assertEqual(len(magnets), result["candidates"])
            self.assertEqual(result["external"], len(magnets))

    def test_f3_candidate_file_carries_every_candidate_once(self):
        import tempfile

        files = [{"index": 0, "path": "a.mkv", "size": 8053063680}]
        with tempfile.TemporaryDirectory() as tmp:
            cand_out = Path(tmp) / "out.magnets"
            code, printed = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--no-pool",
                 "--json-out", str(Path(tmp) / "out.json"),
                 "--candidates-out", str(cand_out)],
                diao_payload([l1_torrent(self.l1_hash, "A.Release.mkv", files),
                           l1_torrent("B" * 40, "B.Release.mkv", files)]),
                [self.external, external_entry(info_hash="E" * 40)])
            self.assertEqual(code, 0)
            result = json.loads(next(line for line in printed.splitlines()
                                     if line.startswith("SEARCH_RESULT")).split(" ", 1)[1])
            magnets = [line for line in cand_out.read_text(encoding="utf-8").splitlines()
                       if line and not line.startswith("#")]
            self.assertEqual(len(magnets), result["candidates"])
            self.assertEqual(len(set(magnets)), len(magnets))


class ExternalCandidateRankingTests(unittest.TestCase):
    """F1 end to end: what search_pool writes must let probe reach `ranked`.

    The I4 review's F1 symptom was an offline dry run that ended in rc=4 with
    zero metadata fetches, because the pool entry claimed `metadata_at` for a
    candidate that had no file list. This case runs the real probe over the real
    search_pool output with every network entry point stubbed, and asserts both
    halves: the probe *did* fetch metadata, and the candidate ended up ranked.
    """

    METADATA = {
        "name": "External.Only.2024.1080p.WEB-DL.x264-TEST", "size_gb": 7.5,
        "nfiles": 1, "main_video_gb": 7.5, "main_video_ext": ".mkv",
        "junk_files": 0, "junk_bytes": 0,
        "videos": [{"index": 0, "path": "e.mkv", "name": "e.mkv", "size": 8053063680}],
    }

    def setUp(self):
        self.external = torrents_csv_source.parse_payload(
            load_fixture("torrents_csv_search.json"), now=NOW)[0]
        self.external_hash = self.external["info_hash"]

    def run_probe(self, argv):
        """Run probe_magnets offline; returns (rc, calls, payload)."""
        calls = []

        def record(name, value):
            def _inner(*args, **kwargs):
                calls.append(name)
                return value
            return _inner

        patches = [
            mock.patch.object(probe_magnets, "fetch_metadata",
                              record("fetch_metadata", dict(self.METADATA))),
            mock.patch.object(probe_magnets, "announce_health", record(
                "announce_health",
                {"seeders": 151, "own_peers": 1, "external_peers": 150})),
            mock.patch.object(probe_magnets, "tracker_health", record("tracker_health", (150, 160))),
            mock.patch.object(probe_magnets, "public_ip", record("public_ip", "203.0.113.7")),
        ]
        stdout = io.StringIO()
        for patcher in patches:
            patcher.start()
        try:
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
                rc = probe_magnets.main()
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        payload = json.loads(Path(argv[argv.index("--json-out") + 1]).read_text(encoding="utf-8"))
        return rc, calls, payload

    def test_f1_external_candidate_reaches_ranked_offline(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "pool.json"
            cand_path = Path(tmp) / "candidates.magnets"
            out_path = Path(tmp) / "probe.json"
            code, _ = run_main_with_fakes(
                ["search_pool.py", "狂飙", "--sleep", "0", "--pool-file", str(pool_path),
                 "--json-out", str(Path(tmp) / "search.json"),
                 "--candidates-out", str(cand_path)],
                diao_payload([]), [self.external])
            self.assertEqual(code, 0)
            self.assertIn(magnet_for(self.external_hash),
                          cand_path.read_text(encoding="utf-8"))

            rc, calls, payload = self.run_probe(
                ["probe_magnets.py", "狂飙", "--kind", "movie", "--duration-minutes", "120",
                 "--candidate-file", str(cand_path), "--pool-file", str(pool_path),
                 "--json-out", str(out_path), "--no-live-check", "--workers", "2"])
            ranked = [item.get("ih") for item in payload["ranked"]]
            self.assertIn("fetch_metadata", calls,
                          "probe 没有为外部候选抓 metadata（池子里的 metadata_at 在撒谎）")
            self.assertIn(self.external_hash, ranked,
                          "外部候选没有进入 ranked（F1 的原始症状）")
            self.assertNotEqual(rc, 4, "probe 以 rc=4（没有候选）结束")
            self.assertEqual(rc, 0)
            self.assertTrue(payload["best"], "ranked 有候选却没有 best")
            self.assertEqual(payload["best"]["ih"], self.external_hash)
