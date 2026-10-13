"""速度子分的契约：seeders 先验做下限，短窗口实测只加分。

Subject：`backend/candidate_score.py` 的 `_speed_score`。改进前的行为是逆激励——
被抽中测速的强 swarm 候选（如 seeds=141）会因为「窗口内 0 字节」被硬锁到 0.40，
反而低于同 swarm 的未抽中候选；写成 0 的实测还会落到 0.02（比死种 0.05 更低）。

`backend/` 不是包，所以按路径加载模块（与 `harness.py` 加载 `tools/` 的写法一致），
不做 `sys.path` 手术。全部离线、无时钟、无网络。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest

from tests.harness import ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "jmd_scoring" / "speed_cases.json"
POLICY_OVERRIDES = {"kind": "movie", "duration_seconds": 7200}


def load_scoring():
    """Load backend/candidate_score.py by path, the way the kit loads its own tools."""
    path = ROOT / "backend" / "candidate_score.py"
    spec = importlib.util.spec_from_file_location("jmd_candidate_score", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{path} is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCORING = load_scoring()
POLICY = SCORING.normalize_policy(POLICY_OVERRIDES)


def candidate(**overrides):
    """字段完整的合成候选；种子名走真实的 `analyze_release` 路径，不做替身。"""
    item = {
        "name": "Movie.Name.2024.1080p.WEB-DL.x265.CHS.mkv",
        "videos": [
            {"index": 0, "path": "a.mkv", "name": "a.mkv", "size": 7 * 1024 ** 3}
        ],
        "main_video_gb": 7.5,
        "size_gb": 7.9,
        "nfiles": 2,
        "main_video_ext": ".mkv",
        "junk_files": 0,
        "junk_bytes": 0,
        "estimated_bitrate_mbps": 8.5,
        "seeds": 0,
        "peers": 0,
    }
    item.update(overrides)
    return item


def scored(**overrides):
    return SCORING.score_candidate(candidate(**overrides), POLICY)


def speed_of(**overrides):
    """速度子分（0–100），与 `dimensions.speed` 同口径。"""
    return scored(**overrides)["dimensions"]["speed"]


def speed_detail(**overrides):
    return scored(**overrides)["breakdown"]["speed"]


UNCCOUNTED_SCENARIOS = (
    {"seeds": 141, "peers": 200},
    {"seeds": 8, "peers": 10},
    {"seeds": 1, "peers": 0},
    {"seeds": 0, "peers": 50},
    {"seeds": 0, "peers": 0},
)
MEASUREMENTS = (0.0, 0.0005, 0.001, -1.0, 0.5, 2.0, 15.0, 1000000.0)


class MeasuredSpeedOnlyAdds(unittest.TestCase):
    """实测只加分：任何一次短窗口结果都不得低于「不测量」时的分数。"""

    def test_any_measurement_keeps_or_raises_the_speed_score(self):
        for base in UNCCOUNTED_SCENARIOS:
            uncounted = speed_of(**base)
            for measured in MEASUREMENTS:
                for flag in ({}, {"speed_unmeasured": True}):
                    with self.subTest(swarm=base, measured=measured, flag=bool(flag)):
                        got = speed_of(
                            **{**base, "measured_speed_mbps": measured, **flag}
                        )
                        self.assertGreaterEqual(got, uncounted - 1e-9)

    def test_zero_measurement_never_scores_like_a_dead_swarm(self):
        # 改进前：measured<=0.001 → 0.02，比 dead_swarm 的 0.05 还低。
        for measured in (0.0, 0.0005, -1.0):
            for flag in ({}, {"speed_unmeasured": True}):
                with self.subTest(measured=measured, flag=bool(flag)):
                    detail = speed_detail(
                        seeds=0, peers=0, measured_speed_mbps=measured, **flag
                    )
                    self.assertNotAlmostEqual(detail["speed_floor"], 0.02)
                    self.assertGreaterEqual(speed_of(
                        seeds=0, peers=0, measured_speed_mbps=measured, **flag
                    ), 40.0 - 1e-9)

    def test_strong_swarm_counted_zero_is_not_below_uncounted(self):
        # 验收口径：seeds=141/peers=200、窗口 0 字节的被抽中候选不得低于未抽中。
        uncounted = scored(seeds=141, peers=200)
        counted = scored(
            seeds=141, peers=200, measured_speed_mbps=0.0, speed_unmeasured=True
        )
        self.assertGreaterEqual(
            counted["dimensions"]["speed"], uncounted["dimensions"]["speed"]
        )
        self.assertGreaterEqual(counted["score"], uncounted["score"])
        self.assertEqual(counted["breakdown"]["speed"]["mode"], "unmeasured")
        self.assertEqual(uncounted["breakdown"]["speed"]["mode"], "seeds")

    def test_measured_below_the_prior_is_ignored_not_penalised(self):
        detail = speed_detail(seeds=141, peers=200, measured_speed_mbps=3.5)
        self.assertEqual(detail["mode"], "measured")
        self.assertEqual(detail["score_source"], "seeds-prior")
        self.assertEqual(detail["seeds_prior"], 1.0)
        self.assertAlmostEqual(speed_of(seeds=141, peers=200), 100.0)

    def test_measured_above_the_prior_is_adopted(self):
        detail = speed_detail(
            seeds=8, peers=10, measured_speed_mbps=3.5, speed_unmeasured=True
        )
        self.assertEqual(detail["mode"], "measured")
        self.assertEqual(detail["score_source"], "measured")
        self.assertGreater(speed_of(seeds=8, peers=10), 45.0)


class FallbackSemanticsKept(unittest.TestCase):
    """回退语义不丢：死种 0.05、无信号中性 0.40。"""

    def test_dead_swarm_stays_at_the_floor(self):
        detail = speed_detail(seeds=141, peers=200, dead_swarm=True)
        self.assertEqual(detail["mode"], "dead-swarm")
        self.assertEqual(detail["score_source"], "dead-swarm")
        self.assertAlmostEqual(speed_of(seeds=141, peers=200, dead_swarm=True), 5.0)

    def test_no_signal_stays_neutral(self):
        detail = speed_detail(seeds=0, peers=0)
        self.assertEqual(detail["mode"], "unknown")
        self.assertEqual(detail["score_source"], "neutral")
        self.assertAlmostEqual(speed_of(seeds=0, peers=0), 40.0)

    def test_unmeasured_between_neutral_and_prior(self):
        # 1 个种子：先验 0.1156 < 中性 0.40 → 取 0.40；8 个种子：先验 0.4568 → 取先验。
        self.assertAlmostEqual(
            speed_of(seeds=1, peers=0, measured_speed_mbps=0.0, speed_unmeasured=True),
            40.0,
        )
        self.assertAlmostEqual(
            speed_of(seeds=8, peers=10, measured_speed_mbps=0.0, speed_unmeasured=True),
            round(speed_of(seeds=8, peers=10), 6),
        )


class ModeAndSourceExplainThePath(unittest.TestCase):
    """同一 swarm 的「抽中 / 没抽中」两条路径子分一致，但标签必须可解释。"""

    def test_counted_and_uncounted_share_the_score_but_not_the_label(self):
        uncounted = speed_detail(seeds=141, peers=200)
        counted = speed_detail(
            seeds=141, peers=200, measured_speed_mbps=0.0, speed_unmeasured=True
        )
        self.assertEqual(uncounted["mode"], "seeds")
        self.assertEqual(counted["mode"], "unmeasured")
        self.assertEqual(uncounted["score_source"], counted["score_source"])
        self.assertEqual(uncounted["seeds_prior"], counted["seeds_prior"])

    def test_discarded_measurement_is_recorded_for_diagnosis(self):
        discarded = speed_detail(seeds=141, peers=200, measured_speed_mbps=0.0)
        self.assertEqual(discarded["measured_discarded"], 0.0)
        adopted = speed_detail(seeds=141, peers=200, measured_speed_mbps=1000000.0)
        self.assertNotIn("measured_discarded", adopted)
        self.assertEqual(adopted["measured_mbps"], 1000000.0)

    def test_mode_and_source_come_from_the_declared_set(self):
        modes = {"measured", "unmeasured", "dead-swarm", "unknown", "seeds", "seeds-external"}
        sources = {"measured", "seeds-prior", "neutral", "dead-swarm"}
        for base in UNCCOUNTED_SCENARIOS:
            for overrides in (
                {},
                {"measured_speed_mbps": 0.0, "speed_unmeasured": True},
                {"measured_speed_mbps": 2.0},
                {"dead_swarm": True},
            ):
                with self.subTest(swarm=base, overrides=overrides):
                    detail = speed_detail(**{**base, **overrides})
                    self.assertIn(detail["mode"], modes)
                    self.assertIn(detail["score_source"], sources)


class SaturatedPriorIsTiedOnPurpose(unittest.TestCase):
    """先验封顶（seeds≥120、peers≥200）时速度维度打平，这是口径而非缺陷。

    t8 报告把这列为未决项：``max(先验, 实测曲线)`` 在饱和区无法区分最强的那批候选。
    队长复核过一个候选修法——「确实测到过吞吐」给一点证据余量——结论是它在饱和区
    **永远不可见**（上限就是 1.0），只会在 0.4–1.0 的中间带把「有实测」抬高 3 分，
    等于给噪声开口子，故不采纳。这个类把当前口径钉住：谁想改，先改这里的断言。
    """

    def test_saturated_prior_ties_regardless_of_measurement(self):
        uncounted = speed_of(seeds=141, peers=200)
        self.assertAlmostEqual(uncounted, 100.0)
        # 实测只要有可用值、且曲线不高于先验，就落在 floor 上 → 与未抽中打平。
        for measured in (0.5, 1.0, 3.5, 15.0):
            with self.subTest(measured=measured):
                self.assertAlmostEqual(
                    speed_of(seeds=141, peers=200, measured_speed_mbps=measured), 100.0
                )
        # 窗口内 0 字节的「未测到」路径同样打平——这正是改进前的逆激励所在。
        self.assertAlmostEqual(
            speed_of(seeds=141, peers=200, measured_speed_mbps=0.0, speed_unmeasured=True),
            100.0,
        )

    def test_below_saturation_the_measured_curve_can_still_win(self):
        # 先验未封顶时排序仍可判定：曲线高于先验就被采纳，低于先验则落在先验上。
        prior_only = speed_of(seeds=8, peers=10)
        measured_fast = speed_of(seeds=8, peers=10, measured_speed_mbps=3.5)
        measured_slow = speed_of(seeds=8, peers=10, measured_speed_mbps=0.2)
        self.assertGreater(measured_fast, prior_only)
        # 子分是 0.457×100=45.68 再四舍五入到两位，所以用「未超过先验」钉住语义，
        # 同时确认慢实测确实被判成 floor（score_source 报的是 seeds-prior）。
        self.assertLessEqual(measured_slow, prior_only)
        slow_detail = speed_detail(seeds=8, peers=10, measured_speed_mbps=0.2)
        self.assertEqual(slow_detail["score_source"], "seeds-prior")
        fast_detail = speed_detail(seeds=8, peers=10, measured_speed_mbps=3.5)
        self.assertEqual(fast_detail["score_source"], "measured")



class ExternalSeedersPrior(unittest.TestCase):
    """第二磁力源（torrents-csv）的外部种子数进先验（t9 报告 F4 的接口契约）。

    字段：`external_seeders`（int）+ `external_seeders_age_days`（float，快照比当前
    时刻旧多少天）。规则：announce 实测优先；`seeds<=0 and peers<=0` 时才兜底；
    新鲜（≤7 天）不打折，每多 1 天衰减 0.02，折扣下限 0.5，且折扣后的先验不得
    低于无信号中性 0.40。旧行为是「完全不读」——下面每条改动前都会红。
    """

    def test_external_seeders_reach_the_prior_when_announce_is_silent(self):
        silent = speed_detail(seeds=0, peers=0)
        external = speed_detail(
            seeds=0, peers=0, external_seeders=5000, external_seeders_age_days=3.0
        )
        self.assertEqual(silent["seed_source"], "none")
        self.assertEqual(silent["mode"], "unknown")
        self.assertEqual(external["seed_source"], "external")
        self.assertEqual(external["mode"], "seeds-external")
        self.assertGreater(external["seeds_prior"], silent["seeds_prior"])
        self.assertGreater(
            speed_of(seeds=0, peers=0, external_seeders=5000, external_seeders_age_days=3.0),
            speed_of(seeds=0, peers=0),
        )

    def test_fresh_snapshot_is_not_discounted(self):
        for age_days in (0.0, 3.0, SCORING.EXTERNAL_SEEDERS_FRESH_DAYS):
            with self.subTest(age_days=age_days):
                detail = speed_detail(
                    seeds=0,
                    peers=0,
                    external_seeders=5000,
                    external_seeders_age_days=age_days,
                )
                self.assertEqual(detail["external_seeders_discount"], 1.0)
                self.assertAlmostEqual(detail["seeds_prior"], 1.0)

    def test_older_snapshot_decays_and_stops_at_the_floor(self):
        fresh, per_day, floor = (
            SCORING.EXTERNAL_SEEDERS_FRESH_DAYS,
            SCORING.EXTERNAL_SEEDERS_DECAY_PER_DAY,
            SCORING.EXTERNAL_SEEDERS_MIN_DISCOUNT,
        )
        for age_days, expected in (
            (fresh + 1.0, 1.0 - per_day),
            (30.0, 1.0 - per_day * 23),
            (200.0, floor),
        ):
            with self.subTest(age_days=age_days):
                detail = speed_detail(
                    seeds=0,
                    peers=0,
                    external_seeders=5000,
                    external_seeders_age_days=age_days,
                )
                self.assertAlmostEqual(
                    detail["external_seeders_discount"], round(expected, 3)
                )
                self.assertGreaterEqual(detail["external_seeders_discount"], floor)

    def test_decay_is_monotone_in_age(self):
        priors = [
            speed_detail(
                seeds=0,
                peers=0,
                external_seeders=5000,
                external_seeders_age_days=age_days,
            )["seeds_prior"]
            for age_days in range(0, 40, 5)
        ]
        self.assertEqual(priors, sorted(priors, reverse=True))

    def test_discounted_prior_never_falls_below_the_neutral(self):
        # 1 个种子：曲线只有 0.1443，任何年龄都必须被抬到中性 0.40，不能更低。
        for age_days in (0.0, 30.0, 400.0):
            with self.subTest(age_days=age_days):
                detail = speed_detail(
                    seeds=0,
                    peers=0,
                    external_seeders=1,
                    external_seeders_age_days=age_days,
                )
                self.assertAlmostEqual(detail["seeds_prior"], 0.40)
                self.assertAlmostEqual(
                    speed_of(
                        seeds=0,
                        peers=0,
                        external_seeders=1,
                        external_seeders_age_days=age_days,
                    ),
                    40.0,
                )

    def test_missing_or_unusable_values_score_exactly_like_no_signal(self):
        # t9 的 F4 口径：这些输入对总分没有任何影响（旧字段名同样不许复活）。
        reference = scored(seeds=0, peers=0)
        variants = (
            {},
            {"external_seeders": None},
            {"external_seeders": 0},
            {"external_seeders": -5},
            {"external_seeders": "not-a-number"},
            {"external_seeders": None, "external_seeders_age_days": 22.7},
            {"seeders": 5000, "seeders_snapshot_age_days": 30.0},
        )
        for extra in variants:
            with self.subTest(extra=extra):
                result = scored(seeds=0, peers=0, **extra)
                self.assertAlmostEqual(result["score"], reference["score"], delta=1e-9)
                detail = speed_detail(seeds=0, peers=0, **extra)
                self.assertEqual(detail["seed_source"], "none")
                self.assertEqual(detail["mode"], "unknown")

    def test_announce_measurement_wins_over_external_snapshot(self):
        announce_only = scored(seeds=30, peers=40)
        both = scored(
            seeds=30, peers=40, external_seeders=5000, external_seeders_age_days=3.0
        )
        self.assertAlmostEqual(both["score"], announce_only["score"], delta=1e-9)
        detail = speed_detail(
            seeds=30, peers=40, external_seeders=5000, external_seeders_age_days=3.0
        )
        self.assertEqual(detail["seed_source"], "announce")
        self.assertEqual(detail["mode"], "seeds")
        self.assertEqual(detail["seeds_prior"], round(speed_of(seeds=30, peers=40) / 100, 4))
        self.assertNotIn("external_seeders_discount", detail)

    def test_negative_age_is_treated_as_fresh(self):
        detail = speed_detail(
            seeds=0, peers=0, external_seeders=5000, external_seeders_age_days=-3.0
        )
        self.assertEqual(detail["external_seeders_discount"], 1.0)

    def test_measurement_still_only_adds_on_top_of_the_external_prior(self):
        base = {"seeds": 0, "peers": 0, "external_seeders": 18,
                "external_seeders_age_days": 22.7}
        uncounted = speed_of(**base)
        for measured in (0.0, 0.0005, -1.0, 0.5, 2.0, 15.0, 1000000.0):
            with self.subTest(measured=measured):
                self.assertGreaterEqual(speed_of(**{**base, "measured_speed_mbps": measured}),
                                        uncounted - 1e-9)

    def test_unmeasured_and_dead_swarm_keep_their_semantics(self):
        unmeasured = speed_detail(
            seeds=0,
            peers=0,
            external_seeders=5000,
            external_seeders_age_days=22.7,
            measured_speed_mbps=0.0,
            speed_unmeasured=True,
        )
        self.assertEqual(unmeasured["mode"], "unmeasured")
        self.assertAlmostEqual(unmeasured["seeds_prior"], 0.686)
        dead = speed_detail(
            seeds=0, peers=0, external_seeders=5000, external_seeders_age_days=3.0,
            dead_swarm=True,
        )
        self.assertEqual(dead["mode"], "dead-swarm")
        # dead_swarm 是可达性判决，仍然压过先验：先验照算，分数是 0.05。
        self.assertAlmostEqual(dead["seeds_prior"], 1.0)
        self.assertAlmostEqual(
            speed_of(
                seeds=0, peers=0, external_seeders=5000, external_seeders_age_days=3.0,
                dead_swarm=True,
            ),
            5.0,
        )


class MetadataAtIsNotAScoringInput(unittest.TestCase):
    """`metadata_at` 的语义边界（t9 报告 F6/N7）：它在池条目上，不在候选字典里。

    含义只有一个——该候选的**文件清单已被核实**；本模块读的是那份清单的投影
    （`videos` / `main_video_gb`），并在 `structural_reject` 的 docstring 里写明。
    这条用例钉住「打分不读 metadata_at」这个事实，避免又出现第三处定义。
    """

    def test_metadata_at_never_changes_the_score(self):
        reference = scored()
        for value in (0, 1, 10 ** 9, None):
            with self.subTest(metadata_at=value):
                self.assertAlmostEqual(
                    scored(metadata_at=value)["score"], reference["score"], delta=1e-9
                )

    def test_documented_boundary_mentions_metadata_at(self):
        doc = SCORING.structural_reject.__doc__ or ""
        self.assertIn("metadata_at", doc)
        self.assertIn("文件清单", doc)


class CliHelpIsReadable(unittest.TestCase):
    """t9 报告 F7：`--help` 曾经是空过（stdout 0 字节、退出码 0）。"""

    SCRIPT = ROOT / "backend" / "candidate_score.py"

    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *argv],
            capture_output=True, text=True, check=False,
        )

    def test_help_exits_zero_with_a_readable_description(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(len(result.stdout), 200)
        for token in ("候选打分库", "quality", "对外接口"):
            self.assertIn(token, result.stdout)

    def test_bare_run_prints_the_same_contract(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("候选打分库", result.stdout)

    def test_show_weights_prints_the_declared_weights(self):
        result = self._run("--show-weights")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("quality 0.34", result.stdout)
        self.assertIn("min_score 45", result.stdout)


class GoldenFixture(unittest.TestCase):
    """golden 表：每行都写明为什么，改动 `_speed_score` 必须连带改这里。"""

    def test_fixture_cases_match(self):
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        policy = SCORING.normalize_policy(doc["policy"])
        self.assertEqual(len(doc["cases"]), 12)
        for case in doc["cases"]:
            with self.subTest(case=case["id"]):
                result = SCORING.score_candidate(candidate(**case["candidate"]), policy)
                detail = result["breakdown"]["speed"]
                self.assertAlmostEqual(
                    round(result["dimensions"]["speed"], 2), case["expect"]["speed"]
                )
                self.assertEqual(detail["mode"], case["expect"]["mode"])
                self.assertEqual(detail["score_source"], case["expect"]["score_source"])


if __name__ == "__main__":
    unittest.main()


class PublisherSourcePreference(unittest.TestCase):
    """发布者偏好：有界的源可用性倾斜，加在总分上。

    依据（2026-10-13 本机实测，全部可复现）：同一机器同一时段内，
    `【高清剧集网发布 www.DDHDTV.com】…` 那条经 watch 记录与磁盘增量双向印证跑到
    9.3–9.7MB/s；而同期两个非该源的星期三候选在 30 秒窗口里逻辑字节增长为 0。
    使用者另有 10+ 次同源可用记录。
    """

    DD = ("【高清剧集网发布 www.DDHDTV.com】挑情丑闻[全8集][简繁英字幕]"
          ".The.Scandal.S01.2160p.NF.WEB-DL")
    PLAIN = "挑情丑闻[全8集][简繁英字幕].The.Scandal.S01.2160p.NF.WEB-DL"

    def test_preference_adds_a_bounded_bonus_to_the_total(self):
        hit = scored(name=self.DD)
        miss = scored(name=self.PLAIN)
        self.assertEqual(hit["breakdown"]["source_preference"], SCORING.DDHDTV_SOURCE_BONUS)
        self.assertEqual(miss["breakdown"]["source_preference"], 0.0)
        self.assertAlmostEqual(
            hit["score"] - miss["score"], SCORING.DDHDTV_SOURCE_BONUS, places=1)

    def test_preference_does_not_touch_any_dimension(self):
        """加分不能伪装成画质/体验/速度——四个维度必须逐位相同。"""
        self.assertEqual(scored(name=self.DD)["dimensions"], scored(name=self.PLAIN)["dimensions"])

    def test_preference_is_bounded_and_ranked_by_bitrate_not_by_label(self):
        """有界性用「码率达标」的口径测，而不是用分辨率标签。

        踩过的坑：这一条最初写成「720p 命中 vs 2160p 未命中」和「1080p 未命中 vs
        2160p 命中」，两次都失败——因为画质分比的是**码率充分性**：夹具给的是固定
        8.5Mbps，它对 2160p 的 20Mbps 目标只有 0.16，对 1080p 的 5Mbps 目标却是达标。
        所以那些比较实际在测码率，不是测标签；断言照标签写会得出反直觉结论。
        """
        same_plain = scored(name=self.PLAIN)
        same_preferred = scored(name=self.DD)
        self.assertGreater(same_preferred["score"], same_plain["score"])

        # 码率同等充分时，偏好不足以越过分辨率差距（1080p vs 480p，差 6.2 分）。
        two_step_plain = scored(
            name=self.PLAIN.replace("2160p", "1080p"), estimated_bitrate_mbps=8.5)
        two_step_preferred = scored(
            name=self.DD.replace("2160p", "480p"), estimated_bitrate_mbps=8.5)
        self.assertGreater(two_step_plain["score"], two_step_preferred["score"])

        # 反过来，当高分辨率那侧码率严重不达标时，命中源的 1080p 应当胜出——
        # 这是刻意结果：偏好是倾斜，而码率不足本身就是画质缺陷。
        starved_2160 = scored(
            name=self.DD.replace("2160p", "2160p"), estimated_bitrate_mbps=8.5)
        healthy_1080 = scored(
            name=self.PLAIN.replace("2160p", "1080p"), estimated_bitrate_mbps=8.5)
        self.assertLess(starved_2160["score"], healthy_1080["score"])

    def test_preference_matches_the_chinese_publisher_token_too(self):
        self.assertEqual(
            scored(name="高清剧集网发布 某剧 1080p")["breakdown"]["source_preference"],
            SCORING.DDHDTV_SOURCE_BONUS,
        )
