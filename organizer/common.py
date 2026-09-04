"""无第三方依赖的通用工具：文件采样哈希、正文首尾节选与 CSV 单元格转义。"""
from __future__ import annotations

import hashlib
from pathlib import Path

SAMPLE_HASH_HEAD_BYTES = 64 * 1024
SAMPLE_HASH_TAIL_BYTES = 64 * 1024
SAMPLE_HASH_READ_CHUNK = 1024 * 1024


def sample_file_hash(file: Path) -> str:
    """文件内容的低成本采样哈希：小于采样窗口时全量读取，否则取头部与尾部。

    用于扫描缓存指纹，检测“内容已变但大小与修改时间未变”的文件。
    """
    size = file.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    if size <= SAMPLE_HASH_HEAD_BYTES + SAMPLE_HASH_TAIL_BYTES:
        with file.open("rb") as stream:
            while chunk := stream.read(SAMPLE_HASH_READ_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()
    with file.open("rb") as stream:
        digest.update(stream.read(SAMPLE_HASH_HEAD_BYTES))
        stream.seek(size - SAMPLE_HASH_TAIL_BYTES)
        digest.update(stream.read(SAMPLE_HASH_TAIL_BYTES))
    return digest.hexdigest()


def truncate_excerpt(content: str, limit: int) -> str:
    """正文节选：不超过 limit 时全文返回，否则取开头三分之二与结尾三分之一。"""
    if len(content) <= limit:
        return content
    head = limit * 2 // 3
    tail = limit - head
    return f"{content[:head]}\n……（正文较长，已节选开头与结尾）……\n{content[-tail:]}"


def csv_safe_cell(value: object) -> str:
    """防止电子表格公式注入：以 = + - @ 或制表符开头的单元格加 ' 前缀。"""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text
