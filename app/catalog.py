"""引文目录：CSV 解析、校验、入库与查询。

目录 CSV 每行两列：书目号,引文文字（首行可为表头）。
匹配时忽略空白与 Unicode 标点；规范化后的引文长度须在 4～40 字之间。
规范化后相同的引文若来自多个书目号，整份目录拒收。

目录按版号整份保存：每次成功导入追加一个新版本，旧版本保留给正在核查的
窗口读取；任何一行不合法都不写入，继续使用上一版。
"""

import csv
import io

from .errors import ValidationError
from .normalize import normalize_text

MAX_ROWS = 100_000
MAX_BYTES = 32 * 1024 * 1024
MIN_CITATION_LEN = 4
MAX_CITATION_LEN = 40
SAVE_BATCH_SIZE = 5000

_HEADER_BOOK = {
    "书目号", "書目號", "书目编号", "書目編號", "书目id", "书号", "書號",
    "图书号", "圖書號", "编号", "編號", "book_no", "bookno", "book_id",
    "bookid", "book_number", "booknumber", "bookcode",
}
_HEADER_TEXT = {
    "引文", "引文文字", "引文原文", "引文内容", "引文內容", "原文", "文字",
    "文本", "内容", "內容", "摘录", "摘錄", "引语", "引語", "citation",
    "citation_text", "citationtext", "text", "quote", "quotation", "snippet",
}


def _looks_like_header(row):
    """首行像表头则返回 (书目号列, 引文列)，否则返回 None。

    仅用于第一个非空行。要求两列都像列名，或第二列是常见引文列名
    （如 book,quote 这类只有第二列在词表里的表头）；这样既支持两列
    顺序颠倒，也避免把普通数据首行误当表头。
    """
    if len(row) < 2:
        return None
    first = row[0].strip().lower().replace(" ", "")
    second = row[1].strip().lower().replace(" ", "")
    if first in _HEADER_BOOK and second in _HEADER_TEXT:
        return (0, 1)
    if first in _HEADER_TEXT and second in _HEADER_BOOK:
        return (1, 0)
    if first in _HEADER_BOOK and second:
        return (0, 1)
    if second in _HEADER_TEXT:
        return (0, 1)
    return None


def _trim_empty_tail(row):
    row = list(row)
    while row and not row[-1].strip():
        row.pop()
    return row


def key_grams(norm: str):
    """取首、中、末三个（可能重复的）四字组合，用于索引。

    规范化后长度≥4 的引文必然含四字组合；这三个组合若在页稿中出现，
    整条引文才可能出现，因此可先用它们取候选，无需扫描整份目录。
    用四字而非三字组合是为了让候选更少（四字组合在中文页稿中更少见）。
    """
    last = len(norm) - 4
    positions = {0, last, last // 2}
    return {norm[p:p + 4] for p in positions}


def parse_catalog(csv_bytes: bytes):
    """解析并校验目录 CSV，返回 [(book_no, citation, norm), ...]。

    入参是原始字节（用于严格检查 32 MiB 上限）；任一行不合法即抛
    ValidationError，整份目录拒收。
    """
    if not isinstance(csv_bytes, (bytes, bytearray)):
        raise ValidationError("引文目录必须是 UTF-8 CSV 文件")
    if len(csv_bytes) > MAX_BYTES:
        raise ValidationError(
            f"引文目录 {len(csv_bytes)} 字节，超过 32 MiB 上限")
    try:
        text = bytes(csv_bytes).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError("引文目录不是有效的 UTF-8 文本")

    try:
        reader = csv.reader(io.StringIO(text))
        entries = []
        seen = {}          # 规范化引文 -> 书目号（用于查重与去重）
        columns = (0, 1)   # 书目号列、引文列；表头若倒序可纠正
        data_rows = 0
        first_content_row = True
        for lineno, raw_row in enumerate(reader, 1):
            row = _trim_empty_tail(raw_row)
            if not row or not any(cell.strip() for cell in row):
                continue
            if first_content_row:
                first_content_row = False
                header = _looks_like_header(row)
                if header is not None:
                    columns = header
                    continue   # 首行是表头；10 万行上限按数据行计
            data_rows += 1
            if data_rows > MAX_ROWS:
                raise ValidationError(
                    f"引文目录超过 {MAX_ROWS} 行上限（表头不计）")
            if len(row) != 2:
                raise ValidationError(
                    f"引文目录第 {lineno} 行应为 2 列（书目号,引文文字）")
            book_no = row[columns[0]].strip()
            citation = row[columns[1]].strip()
            if not book_no:
                raise ValidationError(f"引文目录第 {lineno} 行书目号为空")
            if not citation:
                raise ValidationError(f"引文目录第 {lineno} 行引文为空")
            norm = normalize_text(citation)
            if not MIN_CITATION_LEN <= len(norm) <= MAX_CITATION_LEN:
                raise ValidationError(
                    f"引文目录第 {lineno} 行去掉空白和标点后为 {len(norm)} 字，"
                    f"须在 {MIN_CITATION_LEN}～{MAX_CITATION_LEN} 字之间：{citation}")
            if norm in seen:
                if seen[norm] != book_no:
                    raise ValidationError(
                        f"引文“{citation}”去掉空白和标点后与另一书目号的引文相同，"
                        f"整份目录拒收")
                continue   # 同书目号重复收录，保留一条
            seen[norm] = book_no
            entries.append((book_no, citation, norm))
    except csv.Error as exc:
        raise ValidationError(f"引文目录 CSV 格式错误：{exc}") from exc

    if not entries:
        raise ValidationError("引文目录没有有效引文")
    return entries


def save_catalog(conn, entries, source_bytes):
    """在一个事务里写入一整份目录，返回新目录版号。

    SQLite 的写事务提交前，WAL 模式下其他连接仍能读到上一版目录，
    因此替换目录不会挡住正在核查的窗口。任何异常都回滚，旧目录继续生效。
    """
    try:
        cur = conn.execute(
            "INSERT INTO catalog_versions (entry_count, source_bytes) "
            "VALUES (?, ?)", (len(entries), source_bytes))
        version = cur.lastrowid

        # 分批写入：每批先插入条目，再按版号取回这批条目的 id 写四字索引。
        # 一次事务提交，导入过程中别的连接读到的仍是上一版目录。
        batch_len = SAVE_BATCH_SIZE
        for offset in range(0, len(entries), batch_len):
            chunk = entries[offset:offset + batch_len]
            conn.executemany(
                "INSERT INTO catalog_entries (catalog_version, book_no,"
                " citation, norm, norm_len, gram_count)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(version, book_no, citation, norm, len(norm), len(key_grams(norm)))
                 for book_no, citation, norm in chunk])
            stored = conn.execute(
                "SELECT id, norm FROM catalog_entries WHERE catalog_version = ? "
                "ORDER BY id DESC LIMIT ?", (version, len(chunk))).fetchall()
            gram_rows = []
            for row in stored:
                gram_rows.extend(
                    (version, gram, row["id"]) for gram in key_grams(row["norm"]))
            conn.executemany(
                "INSERT INTO catalog_grams (catalog_version, gram, entry_id)"
                " VALUES (?, ?, ?)", gram_rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return version


def current_catalog(conn):
    """当前生效（最新）的完整目录版本，没有则返回 None。"""
    row = conn.execute(
        "SELECT version, created_at, entry_count FROM catalog_versions "
        "ORDER BY version DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def catalog_version_info(conn, version):
    row = conn.execute(
        "SELECT version, created_at, entry_count FROM catalog_versions "
        "WHERE version = ?", (version,)).fetchone()
    return dict(row) if row else None


def list_catalog_versions(conn, limit=50):
    rows = conn.execute(
        "SELECT version, created_at, entry_count, source_bytes "
        "FROM catalog_versions ORDER BY version DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
