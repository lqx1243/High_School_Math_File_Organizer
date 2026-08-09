from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CategoryRules:
    """一级分类和二级分类。规则来自老师可直接编辑的 UTF-8 文本文件。"""

    groups: dict[str, tuple[str, ...]]

    @classmethod
    def load(cls, path: str | Path) -> "CategoryRules":
        source = Path(path)
        if not source.is_file():
            raise ValueError(f"找不到分类标准文件：{source}")

        groups: dict[str, list[str]] = {}
        current_primary: str | None = None
        try:
            lines = source.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError as error:
            raise ValueError("分类标准文件必须保存为 UTF-8 编码。") from error

        for number, raw_line in enumerate(lines, 1):
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            is_secondary = raw_line[0].isspace()
            title = raw_line.strip()
            if is_secondary:
                if not current_primary:
                    raise ValueError(f"第 {number} 行的二级分类前没有一级分类。")
                if title in groups[current_primary]:
                    raise ValueError(f"第 {number} 行的二级分类重复：{current_primary} / {title}")
                groups[current_primary].append(title)
            else:
                if title in groups:
                    raise ValueError(f"第 {number} 行的一级分类重复：{title}")
                groups[title] = []
                current_primary = title

        if not groups:
            raise ValueError("分类标准文件没有有效分类。")
        return cls({name: tuple(children) for name, children in groups.items()})

    def as_prompt(self) -> str:
        lines: list[str] = []
        for primary, secondary_list in self.groups.items():
            lines.append(f"- {primary}")
            lines.extend(f"  - {secondary}" for secondary in secondary_list)
        return "\n".join(lines)

    def has_primary(self, name: str | None) -> bool:
        return bool(name and name in self.groups)

    def has_secondary(self, primary: str | None, secondary: str | None) -> bool:
        return bool(primary and secondary and secondary in self.groups.get(primary, ()))
