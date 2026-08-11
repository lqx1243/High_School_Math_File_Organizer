from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

INVALID_WINDOWS_PATH_CHARACTERS = set('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
MAX_CATEGORY_NAME_LENGTH = 80
MAX_CATEGORY_DESCRIPTION_LENGTH = 500


def validate_category_name(name: str, line_number: int | None = None) -> None:
    """确保规则名称既可作为 Windows 文件夹名，也不会逃出结果目录。"""
    prefix = f"第 {line_number} 行的" if line_number else "分类"
    if name in {".", ".."}:
        raise ValueError(f"{prefix}分类名称不能是“.”或“..”。")
    if len(name) > MAX_CATEGORY_NAME_LENGTH:
        raise ValueError(f"{prefix}分类名称不能超过 {MAX_CATEGORY_NAME_LENGTH} 个字符。")
    if name.endswith((".", " ")):
        raise ValueError(f"{prefix}分类名称不能以句点或空格结尾。")
    if any(character in INVALID_WINDOWS_PATH_CHARACTERS or ord(character) < 32 for character in name):
        raise ValueError(f"{prefix}分类名称含有 Windows 文件夹不支持的字符：<>:\"/\\|?*。")
    if name.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{prefix}分类名称不能使用 Windows 保留名称：{name}。")


@dataclass(frozen=True)
class CategoryRules:
    """一级分类和二级分类。规则来自老师可直接编辑的 UTF-8 文本文件。"""

    groups: dict[str, tuple[str, ...]]
    primary_descriptions: dict[str, str] = field(default_factory=dict)
    secondary_descriptions: dict[tuple[str, str], str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "CategoryRules":
        source = Path(path)
        if not source.is_file():
            raise ValueError(f"找不到分类标准文件：{source}")

        groups: dict[str, list[str]] = {}
        primary_descriptions: dict[str, str] = {}
        secondary_descriptions: dict[tuple[str, str], str] = {}
        current_primary: str | None = None
        try:
            lines = source.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError as error:
            raise ValueError("分类标准文件必须保存为 UTF-8 编码。") from error

        for number, raw_line in enumerate(lines, 1):
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            is_secondary = raw_line[0].isspace()
            title, separator, description = raw_line.strip().partition("::")
            title = title.strip()
            description = description.strip()
            if separator and not description:
                raise ValueError(f"第 {number} 行的“::”后缺少分类说明。")
            if not title:
                raise ValueError(f"第 {number} 行缺少分类名称。")
            validate_category_name(title, number)
            if len(description) > MAX_CATEGORY_DESCRIPTION_LENGTH:
                raise ValueError(f"第 {number} 行的分类说明不能超过 {MAX_CATEGORY_DESCRIPTION_LENGTH} 个字符。")
            if is_secondary:
                if not current_primary:
                    raise ValueError(f"第 {number} 行的二级分类前没有一级分类。")
                if title in groups[current_primary]:
                    raise ValueError(f"第 {number} 行的二级分类重复：{current_primary} / {title}")
                groups[current_primary].append(title)
                if description:
                    secondary_descriptions[(current_primary, title)] = description
            else:
                if title in groups:
                    raise ValueError(f"第 {number} 行的一级分类重复：{title}")
                groups[title] = []
                if description:
                    primary_descriptions[title] = description
                current_primary = title

        if not groups:
            raise ValueError("分类标准文件没有有效分类。")
        return cls({name: tuple(children) for name, children in groups.items()}, primary_descriptions, secondary_descriptions)

    def as_prompt(self) -> str:
        lines: list[str] = []
        for primary, secondary_list in self.groups.items():
            lines.append(self._prompt_line(primary, self.primary_descriptions.get(primary, "")))
            lines.extend(f"  {self._prompt_line(secondary, self.secondary_descriptions.get((primary, secondary), ''))}" for secondary in secondary_list)
        return "\n".join(lines)

    def primary_prompt(self) -> str:
        return "\n".join(self._prompt_line(primary, self.primary_descriptions.get(primary, "")) for primary in self.groups)

    def secondary_prompt(self, primary: str) -> str:
        return "\n".join(self._prompt_line(secondary, self.secondary_descriptions.get((primary, secondary), "")) for secondary in self.groups.get(primary, ()))

    @staticmethod
    def _prompt_line(name: str, description: str) -> str:
        return f"- {name}：{description}" if description else f"- {name}"

    def has_primary(self, name: str | None) -> bool:
        return bool(name and name in self.groups)

    def has_secondary(self, primary: str | None, secondary: str | None) -> bool:
        return bool(primary and secondary and secondary in self.groups.get(primary, ()))
