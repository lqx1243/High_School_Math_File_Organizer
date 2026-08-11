from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from organizer.category_rules import CategoryRules


class CategoryRulesSafetyTests(unittest.TestCase):
    def _rules_file(self, content: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "rules.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def test_rejects_directory_escape_and_windows_invalid_characters(self) -> None:
        for name in ("..", "分类/资料", "CON", "期末复习."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                CategoryRules.load(self._rules_file(f"{name}\n"))

    def test_accepts_normal_chinese_rule_names(self) -> None:
        rules = CategoryRules.load(self._rules_file("函数与导数 :: 函数图像与导数应用\n    导数及其应用\n"))
        self.assertTrue(rules.has_secondary("函数与导数", "导数及其应用"))


if __name__ == "__main__":
    unittest.main()
