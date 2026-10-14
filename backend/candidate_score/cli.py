"""命令行表面：`--help` 契约与 `--show-weights` 输出。"""

from __future__ import annotations

import argparse

from .policy import DEFAULT_MIN_SCORE, DEFAULT_WEIGHTS


def build_cli_parser():
    """这个模块的 `--help` 入口（它本身是库，没有独立的打分 CLI）。

    改动前 `python3 backend/candidate_score.py --help` 是空过（stdout 0 字节、
    退出码 0），验收口径拿不到任何说明（t9 报告 F7）。这里把它变成可读描述：
    说明维度、阈值与对外接口，`--show-weights` 顺带打印默认口径供人工核对。
    """
    parser = argparse.ArgumentParser(
        prog="candidate_score.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "候选打分库：把四个维度合成一个 0-100 分。\n"
            f"  画质 quality {DEFAULT_WEIGHTS['quality']:.2f}"
            f"｜速度 speed {DEFAULT_WEIGHTS['speed']:.2f}"
            f"｜观看体验 experience {DEFAULT_WEIGHTS['experience']:.2f}"
            f"｜适配成本 fit {DEFAULT_WEIGHTS['fit']:.2f}\n"
            "本文件是库，由 probe_magnets.py / console_server.py 导入使用，\n"
            "没有独立的打分 CLI；--help 只说明这层契约。"
        ),
        epilog=(
            "对外接口：normalize_policy / score_candidate / apply_score / "
            "rank_candidates / structural_reject / score_band / "
            "format_candidate_line"
        ),
    )
    parser.add_argument(
        "--show-weights",
        action="store_true",
        help="打印默认四维权重与分数阈值后退出（离线，便于核对口径）",
    )
    return parser


def parse_args(argv=None):
    return build_cli_parser().parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.show_weights:
        print(
            "默认权重: "
            + " | ".join(f"{name} {value:.2f}" for name, value in DEFAULT_WEIGHTS.items())
        )
        print(
            f"默认阈值: min_score {DEFAULT_MIN_SCORE:g}；"
            f"band ≥75 优秀 / ≥60 良好 / ≥{DEFAULT_MIN_SCORE:g} 可接受"
        )
        return 0
    build_cli_parser().print_help()
    return 0
