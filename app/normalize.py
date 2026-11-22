"""引文匹配用的文本规范化。

匹配时两边都忽略空白和 Unicode 标点（Unicode 类别 P*）。
这里同时提供“规范化文本 -> 原稿下标”的映射，用来把命中位置还原成
原稿中的连续区间。
"""

import unicodedata


def is_ignored(ch: str) -> bool:
    """该字符是否在匹配时忽略：空白或 Unicode 标点。"""
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def normalize_text(text: str) -> str:
    """去掉空白和 Unicode 标点。"""
    return "".join(ch for ch in text if not is_ignored(ch))


def normalized_with_offsets(text: str):
    """返回 (规范化文本, 每个规范化字符在原稿中的下标)。

    命中原稿区间 [start, end) 时：
      start = offsets[首个匹配字符在规范化文本中的位置]
      end   = offsets[末个匹配字符在规范化文本中的位置] + 1
    区间内被忽略的字符（如标点、空白）会被完整包含，区间两端不会多出
    被忽略的字符。
    """
    chars = []
    offsets = []
    for index, ch in enumerate(text):
        if not is_ignored(ch):
            chars.append(ch)
            offsets.append(index)
    return "".join(chars), offsets
