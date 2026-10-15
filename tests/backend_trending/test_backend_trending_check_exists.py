"""A8：目录里同时有 8KB 占位与真视频时 `check_exists` 仍判 ALREADY_EXISTS。

被测的是下载链路的既有脚本（`backend/download/check_exists.py`），以**子进程**跑，
`JMD_MEDIA_ROOT` 指向临时媒体根 —— 这条线路跨进程、跨环境变量，只有真跑一次才算
验过（占位是否被识破取决于 `st_blocks`，所以那个 100MB+ 的文件必须真写下去，
不能用稀疏 `truncate`）。

「只有占位」那一轮是负控制：同一套装置下输出必须是 NEEDS_DOWNLOAD，否则上面那条
断言证明不了「占位没被当成真视频」。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

from tests.backend_trending import ROOT

CHECK_EXISTS = "backend/download/check_exists.py"
CATEGORY = "Movies"
DIR_NAME = "Trending Anchor Test"
PLACEHOLDER_BYTES = 8 * 1024
REAL_BYTES = 101 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
MIN_VALID_BYTES = 100 * 1024 * 1024


def allocated_bytes(path):
    return getattr(os.stat(path), "st_blocks", 0) * 512


def write_real_video(path):
    """真写 101MB：`check_exists` 按已分配块数判真假，稀疏文件会被判成占位。"""
    with open(path, "wb") as handle:
        chunk = b"V" * CHUNK
        remaining = REAL_BYTES
        while remaining > 0:
            size = min(CHUNK, remaining)
            handle.write(chunk[:size])
            remaining -= size
        handle.flush()
        os.fsync(handle.fileno())


def run_check_exists(media_root, name):
    env = dict(os.environ)
    env["JMD_MEDIA_ROOT"] = media_root
    return subprocess.run(
        [sys.executable, CHECK_EXISTS, name, CATEGORY],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


class CheckExistsPlaceholderTests(unittest.TestCase):
    def _media_root(self, root):
        target = os.path.join(root, CATEGORY, DIR_NAME)
        os.makedirs(target, exist_ok=True)
        return target

    def test_placeholder_and_real_video_together_still_report_already_exists(self):
        with tempfile.TemporaryDirectory(prefix="jmd-media-") as root:
            target = self._media_root(root)
            with open(os.path.join(target, "placeholder.mp4"), "wb") as handle:
                handle.write(b"P" * PLACEHOLDER_BYTES)
            real = os.path.join(target, "real.mkv")
            write_real_video(real)

            # 前提：这个文件真的分配了足够多的块（否则测的是文件系统而不是产品）
            self.assertGreaterEqual(os.path.getsize(real), MIN_VALID_BYTES)
            self.assertGreaterEqual(
                allocated_bytes(real),
                os.path.getsize(real) * 0.95,
                "临时目录把真写的数据报成了稀疏，A8 的前提不成立",
            )

            result = run_check_exists(root, DIR_NAME)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ALREADY_EXISTS", result.stdout)
            self.assertIn("[HAS_VIDEO]", result.stdout)

    def test_only_a_placeholder_reports_needs_download(self):
        with tempfile.TemporaryDirectory(prefix="jmd-media-") as root:
            target = self._media_root(root)
            with open(os.path.join(target, "placeholder.mp4"), "wb") as handle:
                handle.write(b"P" * PLACEHOLDER_BYTES)

            result = run_check_exists(root, DIR_NAME)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("NEEDS_DOWNLOAD", result.stdout)
            self.assertNotIn("ALREADY_EXISTS", result.stdout)

    def test_missing_category_directory_is_reported(self):
        with tempfile.TemporaryDirectory(prefix="jmd-media-") as root:
            result = run_check_exists(root, DIR_NAME)
            self.assertEqual(result.returncode, 1)
            self.assertIn("CATEGORY_DIR_MISSING", result.stdout)


if __name__ == "__main__":
    unittest.main()
