from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from organizer.common import csv_safe_cell, sample_file_hash, truncate_excerpt


class SampleFileHashTests(unittest.TestCase):
    def _temp_file(self, content: bytes, name: str = "sample.pdf") -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / name
        path.write_bytes(content)
        return path

    def test_detects_changes_in_head_and_tail_of_large_file(self) -> None:
        path = self._temp_file(b"a" * 1_000_000)
        original = sample_file_hash(path)
        data = bytearray(path.read_bytes())
        data[10] = ord("b")  # 头部变化
        path.write_bytes(data)
        self.assertNotEqual(original, sample_file_hash(path))
        data[-10] = ord("b")  # 尾部变化
        path.write_bytes(data)
        self.assertNotEqual(original, sample_file_hash(path))

    def test_sampling_blind_spot_in_middle_of_large_file(self) -> None:
        path = self._temp_file(b"a" * 1_000_000)
        original = sample_file_hash(path)
        data = bytearray(path.read_bytes())
        data[500_000] = ord("b")  # 中部变化是采样哈希设计上的盲区
        path.write_bytes(data)
        self.assertEqual(original, sample_file_hash(path))

    def test_small_file_is_hashed_entirely(self) -> None:
        path = self._temp_file(b"x" * 1000)
        original = sample_file_hash(path)
        data = bytearray(path.read_bytes())
        data[500] = ord("y")
        path.write_bytes(data)
        self.assertNotEqual(original, sample_file_hash(path))


class TruncateExcerptTests(unittest.TestCase):
    def test_short_content_is_unchanged(self) -> None:
        self.assertEqual(truncate_excerpt("abc", 10), "abc")

    def test_long_content_keeps_head_and_tail(self) -> None:
        content = "课" * 10000
        excerpt = truncate_excerpt(content, 3000)
        self.assertTrue(excerpt.startswith("课" * 2000))
        self.assertTrue(excerpt.endswith("课" * 1000))
        self.assertLessEqual(len(excerpt), 3000 + 30)


class CsvSafeCellTests(unittest.TestCase):
    def test_formula_prefixes_are_quoted(self) -> None:
        for dangerous in ("=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=1", "\r=1", "  =1"):
            with self.subTest(dangerous=dangerous):
                self.assertTrue(csv_safe_cell(dangerous).startswith("'"))

    def test_normal_text_is_unchanged(self) -> None:
        self.assertEqual(csv_safe_cell("正常名称"), "正常名称")
        self.assertEqual(csv_safe_cell("C:/资料/试卷.pdf"), "C:/资料/试卷.pdf")


if __name__ == "__main__":
    unittest.main()
