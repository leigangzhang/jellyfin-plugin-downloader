"""`write_task_magent` 的短路必须按 info_hash，不能按目录。

真机故障（2026-10-15）：297「一战再战 国英多音轨」与 299「一战再战 中文字幕」被提交进
**同一个** `一战再战 (2025)/` 目录。297 先落盘一份 `.magent_*.torrent`，299 走同一个
`write_task_magent` 时看到「目录里已有非空 .torrent」就短路返回，**自己的 metainfo
从未落盘** —— 于是 299 一暂停再点「继续下载」就报
「获取种子文件失败，无法继续下载，请检查下载过程中的文件是否丢失」。

判定必须落到「这一份是不是**本任务**（info_hash）的」上：目录里躺着别人的 metainfo
不算数。迅雷自己写的是 `.magent_<UUID>.torrent`（名字里没有 info_hash），所以除了
认名字还要能解析 bencode 取 `info` 段的原始字节算 sha1 来认领。

用例全部离线：只碰临时目录，不连迅雷、不碰真实下载盘。
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import media_download_lib as mdl  # noqa: E402

# 迅雷自己的命名（名字里没有 info_hash，只能靠内容认）
THUNDER_NAME = ".magent_7ED80D7D-5ABE-4E0A-94FF-2450B106EBE3.torrent"


def bencode(value):
    """最小 bencode 编码器：只够造出单文件种子。"""
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda pair: pair[0])
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in items) + b"e"
    raise TypeError(f"bencode 不支持的类型：{type(value)!r}")


def make_torrent(name, length):
    """造一份单文件种子；返回 `(种子字节, info_hash)`，二者由同一份 info 字节算出。"""
    info = {
        b"length": length,
        b"name": name,
        b"piece length": 16384,
        b"pieces": bytes(range(20)),
    }
    info_hash = hashlib.sha1(bencode(info)).hexdigest().upper()
    torrent = bencode(
        {
            b"announce": b"http://tracker.invalid/announce",
            b"info": info,
        }
    )
    return torrent, info_hash


def info_hash_of(path):
    """从磁盘上的种子文件独立算出 info_hash（不借助被测代码）。"""
    return hashlib.sha1(raw_info_bytes(Path(path).read_bytes())).hexdigest().upper()


def raw_info_bytes(blob, offset=0):
    """从 bencode 里取出 `info` 段的**原始字节**（重编码可能不一致，必须取原样）。"""
    assert blob[offset : offset + 1] == b"d", "顶层不是字典"
    index = offset + 1
    while blob[index : index + 1] != b"e":
        key, index = _read_bytes(blob, index)
        start = index
        index = _skip_value(blob, index)
        if key == b"info":
            return blob[start:index]
    raise AssertionError("种子里没有 info 段")


def _read_bytes(blob, index):
    colon = blob.index(b":", index)
    length = int(blob[index:colon])
    start = colon + 1
    return blob[start : start + length], start + length


def _skip_value(blob, index):
    head = blob[index : index + 1]
    if head == b"i":
        return blob.index(b"e", index) + 1
    if head == b"l" or head == b"d":
        index += 1
        while blob[index : index + 1] != b"e":
            if head == b"d":
                _, index = _read_bytes(blob, index)
            index = _skip_value(blob, index)
        return index + 1
    _, index = _read_bytes(blob, index)
    return index


class MagentTestCase(unittest.TestCase):
    """公共夹具：临时任务目录 + 两份内容不同的种子。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        self.task_dir = self.tmp / "一战再战 (2025)"

        self.blob_a, self.hash_a = make_torrent(b"Movie.A.2025.mkv", 1024)
        self.blob_b, self.hash_b = make_torrent(b"Movie.B.2025.mkv", 2048)
        self.assertNotEqual(self.hash_a, self.hash_b)

        self.src_a = self.tmp / "a.torrent"
        self.src_a.write_bytes(self.blob_a)
        self.src_b = self.tmp / "b.torrent"
        self.src_b.write_bytes(self.blob_b)

    def magents(self):
        return mdl.task_magent_paths(str(self.task_dir))


class SameDirectoryTwoTasksTests(MagentTestCase):
    """核心回归：同一目录里的第二个任务必须拿到自己那份 metainfo。

    修复前 `SameDirectoryTwoTasksTests` 这几条是红的：第二条走的是
    `existing = [...]; if existing: return {...existed: True}`，直接短路。
    """

    def test_second_task_in_the_same_directory_still_gets_its_own_metainfo(self):
        first = mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        self.assertTrue(first["written"])

        second = mdl.write_task_magent(str(self.task_dir), self.hash_b, source=str(self.src_b))
        # 旧实现：written=False / existed=True（目录里已有 a 那份）—— 299 就是这么丢的
        self.assertTrue(second["written"], f"第二个任务没有被写入：{second}")
        self.assertFalse(second["existed"])
        self.assertNotEqual(first["path"], second["path"])

        files = self.magents()
        self.assertEqual(len(files), 2, f"两份 metainfo 应都在场：{files}")
        self.assertEqual({info_hash_of(path) for path in files}, {self.hash_a, self.hash_b})

    def test_each_metainfo_hashes_to_its_own_info_hash(self):
        mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        mdl.write_task_magent(str(self.task_dir), self.hash_b, source=str(self.src_b))
        by_hash = {info_hash_of(path): path for path in self.magents()}
        self.assertEqual(set(by_hash), {self.hash_a, self.hash_b})
        # 字节与源种子一致（原样搬运，没有重新编码）
        self.assertEqual(Path(by_hash[self.hash_a]).read_bytes(), self.blob_a)
        self.assertEqual(Path(by_hash[self.hash_b]).read_bytes(), self.blob_b)

    def test_another_tasks_file_already_in_place_does_not_satisfy_us(self):
        """目录里先躺好 b 那份（模拟 297 已落盘），再写 a —— a 必须照写。"""
        self.task_dir.mkdir(parents=True)
        (self.task_dir / ".magent_AAAA.torrent").write_bytes(self.blob_b)

        result = mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        self.assertTrue(result["written"], f"被别人的 metainfo 短路了：{result}")
        self.assertEqual(len(self.magents()), 2)

    def test_written_name_carries_the_info_hash(self):
        """命名要能人工认领：文件里带本任务的 info_hash。"""
        result = mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        self.assertIn(self.hash_a, os.path.basename(result["path"]).upper())

    def test_thunder_named_file_is_claimed_by_content(self):
        """迅雷自己写的是 `.magent_<UUID>.torrent`：名字里没有 hash，靠 bencode 内容认领。"""
        self.task_dir.mkdir(parents=True)
        (self.task_dir / THUNDER_NAME).write_bytes(self.blob_a)

        result = mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        self.assertFalse(result["written"])
        self.assertTrue(result["existed"])
        self.assertEqual(os.path.basename(result["path"]), THUNDER_NAME)
        self.assertEqual(len(self.magents()), 1, "认领到了就不该再写一份")


class IdempotenceTests(MagentTestCase):
    """同一 info_hash 重复写：不产生第二份、不覆盖。"""

    def test_repeat_write_is_idempotent_and_does_not_overwrite(self):
        first = mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        before = Path(first["path"]).read_bytes()
        mtime_before = os.path.getmtime(first["path"])

        second = mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        self.assertFalse(second["written"])
        self.assertTrue(second["existed"])
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(len(self.magents()), 1)
        self.assertEqual(Path(first["path"]).read_bytes(), before)
        self.assertEqual(os.path.getmtime(first["path"]), mtime_before)

    def test_three_tasks_in_one_directory_all_land(self):
        """多任务同目录（>2）也要各自落盘——不只是在两条时修好。"""
        blobs = [self.blob_a, self.blob_b]
        third_blob, third_hash = make_torrent(b"Movie.C.2025.mkv", 4096)
        blobs.append(third_blob)
        sources = []
        for index, blob in enumerate(blobs):
            path = self.tmp / f"src{index}.torrent"
            path.write_bytes(blob)
            sources.append(path)

        for index, source in enumerate(sources):
            result = mdl.write_task_magent(str(self.task_dir), info_hash_of(source), source=str(source))
            self.assertTrue(result["written"], f"第 {index + 1} 个任务没写进去：{result}")

        hashes = {info_hash_of(path) for path in self.magents()}
        self.assertEqual(hashes, {self.hash_a, self.hash_b, third_hash})


class ReturnShapeTests(MagentTestCase):
    """向后兼容：调用方（submit_xunlei:697）读 path/written/bytes。"""

    def test_success_keys_are_unchanged(self):
        result = mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        self.assertEqual(set(result), {"path", "written", "existed", "bytes"})
        self.assertIsInstance(result["bytes"], int)
        self.assertGreater(result["bytes"], 0)

    def test_missing_source_still_reports_the_same_error_shape(self):
        result = mdl.write_task_magent(
            str(self.tmp / "nowhere"), self.hash_a, source=str(self.tmp / "missing.torrent")
        )
        self.assertEqual(result["path"], "")
        self.assertFalse(result["written"])
        self.assertFalse(result["existed"])
        self.assertIn("没有可用的 metainfo", result["error"])

    def test_empty_existing_file_is_not_usable(self):
        self.task_dir.mkdir(parents=True)
        (self.task_dir / ".magent_EMPTY.torrent").write_bytes(b"")
        result = mdl.write_task_magent(str(self.task_dir), self.hash_a, source=str(self.src_a))
        self.assertTrue(result["written"])

    def test_default_source_path_is_unchanged(self):
        """不传 source 时仍找 /tmp/<HASH>.torrent（大小写按原来的口径）。"""
        expected = Path(f"/tmp/{self.hash_a.upper()}.torrent")
        result = mdl.write_task_magent(str(self.task_dir), self.hash_a)
        if expected.is_file():
            self.assertTrue(result["path"])
        else:
            self.assertEqual(result["path"], "")
            self.assertIn("没有可用的 metainfo", result["error"])


if __name__ == "__main__":
    unittest.main()
