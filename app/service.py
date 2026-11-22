"""发布新版页稿并核对引文位置。

发布流程（上传校样）：
  1. 校验页稿（非空、不超过 1200 字）与引用 CSV（引用号唯一、下标在界内）；
  2. 对上一版已有的每条引用，以它最近一次成功定位的页稿为源稿，
     与新校样做整页比对（同一对稿件只比对一次，多条引用共用）；
  3. 所有改动最少的对齐都把引文逐字对到新稿同一段连续位置 → 登记新位置；
     否则标为“待核对”，保留最近一次定位成功的版号和位置；
  4. CSV 中直接给出的引用按所给下标登记到新版（用于新引用或人工修正）；
  5. 新版页稿与全部核对结果在一个事务里写入；任何一步失败都回滚，
     上一版继续对外提供。
"""

import csv
import io

from .align import PageAlignment
from .errors import ValidationError

MAX_TEXT_LEN = 1200


def parse_citations_csv(csv_text: str):
    """解析引用 CSV：唯一引用号,起始下标,结束下标（从 0 起算，右端不含）。

    首行可以是表头；返回 [(citation_id, start, end), ...]。
    """
    rows = [row for row in csv.reader(io.StringIO(csv_text.lstrip("\ufeff")))
            if row and any(cell.strip() for cell in row)]
    items = []
    seen = set()
    for lineno, row in enumerate(rows, 1):
        if len(row) < 3:
            raise ValidationError(f"CSV 第 {lineno} 行不足 3 列（引用号,起始下标,结束下标）")
        try:
            start = int(row[1].strip())
            end = int(row[2].strip())
        except ValueError:
            if lineno == 1 and len(rows) > 1:
                continue  # 首行是表头
            raise ValidationError(f"CSV 第 {lineno} 行的起止下标不是整数")
        cid = row[0].strip()
        if not cid:
            raise ValidationError(f"CSV 第 {lineno} 行引用号为空")
        if cid in seen:
            raise ValidationError(f"CSV 引用号重复：{cid}")
        seen.add(cid)
        items.append((cid, start, end))
    return items


def publish_page(conn, text: str, csv_text: str = ""):
    """发布新版页稿。

    成功返回 (新版号, 核对结果列表)；输入不合法抛 ValidationError；
    其它异常向上抛（事务已回滚，上一版继续对外提供）。
    """
    text = text or ""
    csv_text = csv_text or ""
    if not isinstance(text, str) or not isinstance(csv_text, str):
        raise ValidationError("页稿文本和引用 CSV 都必须是字符串")
    if not text.strip():
        raise ValidationError("页稿文本为空")
    if len(text) > MAX_TEXT_LEN:
        raise ValidationError(f"页稿 {len(text)} 字，超过上限 {MAX_TEXT_LEN} 字")

    declared = parse_citations_csv(csv_text) if csv_text and csv_text.strip() else []
    for cid, start, end in declared:
        if not 0 <= start < end <= len(text):
            raise ValidationError(
                f"引用 {cid} 的下标 [{start}, {end}) 超出页稿范围（0~{len(text)}）")

    row = conn.execute("SELECT MAX(version) AS v FROM pages").fetchone()
    current = row["v"] or 0
    new_version = current + 1

    declared_ids = {cid for cid, _, _ in declared}
    results = []

    if current:
        existing = conn.execute(
            "SELECT citation_id, start_pos, end_pos, located_version FROM citations "
            "WHERE version = ? ORDER BY citation_id", (current,)).fetchall()
    else:
        existing = []

    alignments = {}  # 源版号 → 整页比对（同一对稿件只算一次，多条引用共用）

    def alignment_for(src_version):
        if src_version not in alignments:
            src = conn.execute("SELECT text FROM pages WHERE version = ?",
                               (src_version,)).fetchone()
            alignments[src_version] = PageAlignment(src["text"], text)
        return alignments[src_version]

    for r in existing:
        cid = r["citation_id"]
        if cid in declared_ids:
            continue  # CSV 直接登记的优先
        src = r["located_version"]  # 以最近一次成功定位的页稿为准
        span = alignment_for(src).relocate(r["start_pos"], r["end_pos"])
        if span is None:
            results.append({"citation_id": cid, "status": "pending",
                            "start": r["start_pos"], "end": r["end_pos"],
                            "located_version": src})
        else:
            results.append({"citation_id": cid, "status": "located",
                            "start": span[0], "end": span[1],
                            "located_version": new_version})

    for cid, start, end in declared:
        results.append({"citation_id": cid, "status": "located",
                        "start": start, "end": end, "located_version": new_version})

    try:
        conn.execute("INSERT INTO pages (version, text) VALUES (?, ?)",
                     (new_version, text))
        conn.executemany(
            "INSERT INTO citations (version, citation_id, status, start_pos, end_pos,"
            " located_version) VALUES (?, ?, ?, ?, ?, ?)",
            [(new_version, r["citation_id"], r["status"], r["start"], r["end"],
              r["located_version"]) for r in results])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new_version, results


def latest_version(conn):
    row = conn.execute("SELECT MAX(version) AS v FROM pages").fetchone()
    return row["v"]


def list_versions(conn):
    rows = conn.execute(
        "SELECT version, created_at FROM pages ORDER BY version").fetchall()
    return [dict(r) for r in rows]


def get_version_view(conn, version):
    """按版号查询某一版：页稿全文 + 该版引文核对结果（含引文原文）。"""
    page = conn.execute(
        "SELECT version, text, created_at FROM pages WHERE version = ?",
        (version,)).fetchone()
    if page is None:
        return None
    rows = conn.execute(
        "SELECT citation_id, status, start_pos, end_pos, located_version FROM citations "
        "WHERE version = ? ORDER BY citation_id", (version,)).fetchall()
    texts = {version: page["text"]}
    citations = []
    for r in rows:
        lv = r["located_version"]
        if lv not in texts:
            t = conn.execute("SELECT text FROM pages WHERE version = ?", (lv,)).fetchone()
            texts[lv] = t["text"] if t else ""
        src_text = texts[lv]
        citations.append({
            "citation_id": r["citation_id"],
            "status": r["status"],
            "start": r["start_pos"],
            "end": r["end_pos"],
            "located_version": lv,
            "excerpt": src_text[r["start_pos"]:r["end_pos"]],
        })
    return {
        "version": page["version"],
        "created_at": page["created_at"],
        "char_count": len(page["text"]),
        "text": page["text"],
        "citations": citations,
    }
