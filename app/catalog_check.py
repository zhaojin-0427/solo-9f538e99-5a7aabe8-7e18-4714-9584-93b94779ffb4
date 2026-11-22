"""在已发布页稿中核查引文目录的命中。

核查流程：
  1. 钉住一份完整目录（调用方给出目录版号；替换目录只是新增版本，
     已钉住的版本继续可读）；
  2. 把页稿规范化（去掉空白与 Unicode 标点），同时记下每个规范化字符
     在原稿中的下标；
  3. 用目录的四字索引取出“可能命中”的少数条目，不逐条扫描整份目录；
  4. 对候选条目在规范化页稿中找出全部出现位置（含重叠位置），
     再映射回原稿区间：[首个匹配字符下标, 末个匹配字符下标+1)；
  5. 本版已定位引文完整覆盖的命中区间跳过，其余全部列出。
"""

from .normalize import normalized_with_offsets

_QUERY_CHUNK = 500


def _chunks(items, size=_QUERY_CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _placeholders(count):
    return ",".join("?" * count)


def candidate_entries(conn, catalog_version, norm_page):
    """用四字索引取出可能命中的目录条目（不扫描整份目录）。

    每条目在建目录时只索引首、中、末三个四字组合；条目若出现在页稿中，
    这三个组合必然都出现在页稿里。先按页稿的组合取候选，再在 Python 中
    核对三个组合是否齐全，最后交给调用方逐条验证。
    """
    if len(norm_page) < 4:
        return []
    page_grams = {norm_page[i:i + 4] for i in range(len(norm_page) - 3)}
    if not page_grams:
        return []

    counts = {}
    for chunk in _chunks(sorted(page_grams)):
        rows = conn.execute(
            "SELECT entry_id FROM catalog_grams "
            "WHERE catalog_version = ? AND gram IN (%s)" % _placeholders(len(chunk)),
            (catalog_version, *chunk)).fetchall()
        for row in rows:
            entry_id = row[0]
            counts[entry_id] = counts.get(entry_id, 0) + 1
    if not counts:
        return []

    entries = []
    ids = sorted(counts)
    for chunk in _chunks(ids):
        rows = conn.execute(
            "SELECT id, book_no, citation, norm, gram_count FROM catalog_entries "
            "WHERE catalog_version = ? AND id IN (%s)" % _placeholders(len(chunk)),
            (catalog_version, *chunk)).fetchall()
        for row in rows:
            if counts[row["id"]] == row["gram_count"]:
                entries.append(dict(row))
    return entries


def _occurrences(haystack, needle):
    pos = haystack.find(needle)
    while pos != -1:
        yield pos
        pos = haystack.find(needle, pos + 1)   # +1 保留重叠出现


def check_page(conn, page_text, catalog_version, located_intervals=()):
    """核查一页。

    返回 (hits, skipped_count)。hits 每项含 book_no、citation（目录原文）、
    start/end（原稿区间，0 起算、右端不含）、excerpt（原稿命中文字）。
    已定位引文完整覆盖的命中被跳过并计数。
    """
    norm_page, offsets = normalized_with_offsets(page_text)
    entries = candidate_entries(conn, catalog_version, norm_page)

    intervals = list(located_intervals)
    hits = []
    skipped = 0
    for entry in entries:
        norm = entry["norm"]
        for pos in _occurrences(norm_page, norm):
            start = offsets[pos]
            end = offsets[pos + len(norm) - 1] + 1
            if any(c_start <= start and end <= c_end
                   for c_start, c_end in intervals):
                skipped += 1
                continue
            hits.append({
                "book_no": entry["book_no"],
                "citation": entry["citation"],
                "start": start,
                "end": end,
                "excerpt": page_text[start:end],
            })

    hits.sort(key=lambda h: (h["start"], h["end"], h["book_no"], h["citation"]))
    return hits, skipped
