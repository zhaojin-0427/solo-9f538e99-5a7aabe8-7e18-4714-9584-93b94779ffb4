"""自测：引文目录导入、版本控制、忽略空白标点的核查与覆盖跳过。"""

import pytest

from app import create_app, catalog, catalog_check
from app.catalog import parse_catalog
from app.errors import ValidationError

PAGE = "子曰：学而时习之，不亦说乎？有朋自远方来，不亦乐乎！"


@pytest.fixture()
def app(tmp_path):
    application = create_app(str(tmp_path / "test.db"))
    application.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def upload_catalog(client, csv_text):
    return client.post("/api/catalog", json={"catalog_csv": csv_text})


def publish(client, text, csv_text=""):
    return client.post("/api/pages", json={"text": text, "citations_csv": csv_text})


def check(client, version):
    resp = client.get(f"/api/pages/{version}/check")
    return resp.status_code, resp.get_json()


# ---------- 目录解析与校验 ----------

class TestCatalogParsing:
    def test_ignores_whitespace_and_punctuation_in_catalog(self):
        entries = parse_catalog(
            "B1, 子曰：“学而时习之” \n".encode("utf-8"))
        assert entries == [("B1", "子曰：“学而时习之”", "子曰学而时习之")]

    def test_header_rows_are_skipped(self):
        entries = parse_catalog(
            "书目号,引文文字\nB1,甲乙丙丁\nB2,戊己庚辛\n".encode("utf-8"))
        assert [e[0] for e in entries] == ["B1", "B2"]

    def test_reversed_header_columns(self):
        entries = parse_catalog(
            "引文文字,书目号\n甲乙丙丁,B1\n".encode("utf-8"))
        assert entries == [("B1", "甲乙丙丁", "甲乙丙丁")]

    def test_too_short_after_normalizing_rejected(self):
        for citation in ("甲乙丙", "甲 乙，丙。"):
            with pytest.raises(ValidationError):
                parse_catalog(f"B1,{citation}\n".encode("utf-8"))

    def test_too_long_after_normalizing_rejected(self):
        with pytest.raises(ValidationError):
            parse_catalog(("B1," + "甲" * 41 + "\n").encode("utf-8"))

    def test_length_boundaries_accepted(self):
        entries = parse_catalog(("B1," + "甲" * 40 + "\n").encode("utf-8"))
        assert entries[0][2] == "甲" * 40
        assert len(parse_catalog("B1,甲乙 丙丁\n".encode("utf-8"))) == 1

    def test_same_normalized_citation_two_book_numbers_rejected(self):
        csv_text = "B1,子曰学而时习之\nB2,子曰，学而 时习之！\n"
        with pytest.raises(ValidationError):
            parse_catalog(csv_text.encode("utf-8"))

    def test_same_book_number_duplicate_deduplicated(self):
        entries = parse_catalog(
            "B1,子曰学而时习之\nB1,子曰，学而时习之\n".encode("utf-8"))
        assert len(entries) == 1

    def test_not_utf8_rejected(self):
        with pytest.raises(ValidationError):
            parse_catalog(b"B1,\xff\xfe\n")

    def test_wrong_column_count_rejected(self):
        with pytest.raises(ValidationError):
            parse_catalog(b"B1,abcd,extra\n")

    def test_byte_limit(self):
        original = catalog.MAX_BYTES
        catalog.MAX_BYTES = 10
        try:
            with pytest.raises(ValidationError):
                parse_catalog(b"B1,abcdefghij\n")
        finally:
            catalog.MAX_BYTES = original

    def test_row_limit(self):
        original = catalog.MAX_ROWS
        catalog.MAX_ROWS = 3
        try:
            with pytest.raises(ValidationError):
                parse_catalog(b"B1,abcd\nB2,efgh\nB3,ijkl\nB4,mnop\n")
        finally:
            catalog.MAX_ROWS = original


# ---------- 导入事务与版本 ----------

class TestCatalogImport:
    def test_failed_import_keeps_previous_catalog(self, client):
        assert upload_catalog(client, "B1,甲乙丙丁\n").status_code == 201
        bad = upload_catalog(client, "B1,甲乙丙丁\nB2,甲乙丙丁\n")  # 同文异号
        assert bad.status_code == 400
        data = client.get("/api/catalog").get_json()
        assert data["catalog"]["version"] == 1
        assert data["catalog"]["entry_count"] == 1

    def test_successful_replacement_appends_version(self, client):
        upload_catalog(client, "B1,甲乙丙丁\n")
        resp = upload_catalog(client, "B2,戊己庚辛\nB3,壬癸子丑\n")
        assert resp.status_code == 201
        assert resp.get_json()["catalog_version"] == 2
        data = client.get("/api/catalog").get_json()
        assert data["catalog"]["version"] == 2
        assert data["catalog"]["entry_count"] == 2
        assert [v["version"] for v in data["versions"]] == [2, 1]

    def test_mid_transaction_failure_rolls_back(self, client, monkeypatch):
        upload_catalog(client, "B1,甲乙丙丁\n")
        real = catalog.key_grams

        def broken(norm):
            if norm == "戊己庚辛":
                raise RuntimeError("模拟写入失败")
            return real(norm)

        monkeypatch.setattr(catalog, "key_grams", broken)
        resp = upload_catalog(client, "B2,戊己庚辛\n")
        assert resp.status_code == 500
        monkeypatch.undo()
        data = client.get("/api/catalog").get_json()
        assert data["catalog"]["version"] == 1
        assert data["catalog"]["entry_count"] == 1

    def test_catalog_page_renders_with_version(self, client):
        upload_catalog(client, "B1,甲乙丙丁\n")
        html = client.get("/catalog").get_data(as_text=True)
        assert "第 1 版" in html
        assert "1 条" in html

    def test_multipart_file_upload(self, client):
        import io
        data = {"catalog": (io.BytesIO("B1,甲乙丙丁\n".encode("utf-8")),
                            "catalog.csv")}
        resp = client.post("/catalog", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 302
        data = client.get("/api/catalog").get_json()
        assert data["catalog"]["entry_count"] == 1

    def test_oversized_catalog_rejected(self, client):
        original = catalog.MAX_BYTES
        catalog.MAX_BYTES = 10
        try:
            resp = upload_catalog(client, "B1,甲乙丙丁\n")
        finally:
            catalog.MAX_BYTES = original
        assert resp.status_code == 400
        assert "32 MiB" in resp.get_json()["error"]


# ---------- 核查 ----------

class TestCheck:
    def setup_page(self, client):
        publish(client, PAGE)

    def test_hits_ignore_whitespace_and_punctuation(self, client):
        self.setup_page(client)
        upload_catalog(client, "B1,子曰：“学而时习之”\nB2,有朋自远方来 不亦乐乎\n")
        status, data = check(client, 1)
        assert status == 200
        hits = {(h["book_no"], h["start"], h["end"]) for h in data["hits"]}
        assert ("B1", 0, 8) in hits
        assert ("B2", 14, 25) in hits
        b1 = next(h for h in data["hits"] if h["book_no"] == "B1")
        assert b1["citation"] == "子曰：“学而时习之”"
        assert b1["excerpt"] == "子曰：学而时习之"

    def test_multiple_occurrences_all_listed(self, client):
        publish(client, "学而时习之，学而时习之。")
        upload_catalog(client, "B1,学而时习之\n")
        _, data = check(client, 1)
        spans = [(h["start"], h["end"]) for h in data["hits"]]
        assert spans == [(0, 5), (6, 11)]

    def test_overlapping_hits_kept(self, client):
        publish(client, "甲乙丙丁戊")
        upload_catalog(client, "B1,甲乙丙丁\nB2,乙丙丁戊\n")
        _, data = check(client, 1)
        spans = {(h["book_no"], h["start"], h["end"]) for h in data["hits"]}
        assert spans == {("B1", 0, 4), ("B2", 1, 5)}

    def test_overlapping_occurrences_of_same_entry(self, client):
        upload_catalog(client, "B1,甲乙甲乙甲\n")
        publish(client, "甲乙甲乙甲乙甲")
        _, data = check(client, 1)
        spans = [(h["start"], h["end"]) for h in data["hits"]]
        assert spans == [(0, 5), (2, 7)]

    def test_interval_excludes_trailing_punctuation_inside_ignored_run(self, client):
        # “子曰” 前有引号、“而”后有逗号；区间从首个匹配字符到末个匹配字符。
        publish(client, "『子曰』学而，时习之。")
        upload_catalog(client, "B1,子曰学而时习之\n")
        _, data = check(client, 1)
        assert data["hits"][0]["start"] == 1
        assert data["hits"][0]["end"] == 10
        assert data["hits"][0]["excerpt"] == "子曰』学而，时习之"

    def test_hit_covered_by_located_citation_is_skipped(self, client):
        # 本版已登记一条引文完整覆盖第一处命中，应跳过；第二处照列。
        publish(client, "学而时习之。学而时习之。", "q1,0,5\n")
        upload_catalog(client, "B1,学而时习之\n")
        _, data = check(client, 1)
        assert data["skipped"] == 1
        assert [(h["start"], h["end"]) for h in data["hits"]] == [(6, 11)]

    def test_partial_coverage_does_not_skip(self, client):
        publish(client, "学而时习之。", "q1,1,4\n")   # 只覆盖中间三字
        upload_catalog(client, "B1,学而时习之\n")
        _, data = check(client, 1)
        assert data["skipped"] == 0
        assert [(h["start"], h["end"]) for h in data["hits"]] == [(0, 5)]

    def test_requires_catalog(self, client):
        publish(client, PAGE)
        status, data = check(client, 1)
        assert status == 409

    def test_catalog_version_is_reported(self, client):
        publish(client, PAGE)
        upload_catalog(client, "B1,学而时习之\n")
        upload_catalog(client, "B2,有朋自远方来\n")
        _, data = check(client, 1)
        assert data["catalog_version"] == 2
        assert data["catalog_entry_count"] == 1

    def test_can_pin_older_catalog_version(self, client):
        publish(client, PAGE)
        upload_catalog(client, "B1,学而时习之\n")
        upload_catalog(client, "B2,有朋自远方来\n")
        resp = client.get("/api/pages/1/check?catalog_version=1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["catalog_version"] == 1
        assert [h["book_no"] for h in data["hits"]] == ["B1"]

    def test_detail_page_lists_hits_and_version(self, client):
        publish(client, PAGE)
        upload_catalog(client, "B1,学而时习之\n")
        html = client.get("/pages/1").get_data(as_text=True)
        assert "B1" in html
        assert "[3, 8)" in html
        assert "目录第 1 版" in html

    def test_detail_page_without_catalog_prompts_import(self, client):
        publish(client, PAGE)
        html = client.get("/pages/1").get_data(as_text=True)
        assert "还没有引文目录" in html


# ---------- 候选检索只碰相关条目 ----------

class TestCandidateIndex:
    def test_candidate_lookup_does_not_load_unrelated_entries(self, app, monkeypatch):
        rng = __import__("random").Random(7)
        rows = []
        alphabet = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏"
        for i in range(2000):
            text = "".join(rng.choice(alphabet) for _ in range(12))
            rows.append(f"B{i},{text}")
        csv_text = "\n".join(rows) + "\nBtarget,学而时习之\n"
        client = app.test_client()
        publish(client, PAGE)
        assert upload_catalog(client, csv_text).status_code == 201

        loaded = []
        real = catalog_check.candidate_entries

        def spy(conn, version, norm_page):
            result = real(conn, version, norm_page)
            loaded.extend(result)
            return result

        monkeypatch.setattr(catalog_check, "candidate_entries", spy)
        _, data = check(client, 1)
        assert [h["book_no"] for h in data["hits"]] == ["Btarget"]
        assert loaded, "应当至少返回目标候选"
        assert len(loaded) < 50, "不应把整份目录都载入内存"


# ---------- 并发：替换目录时另一窗口仍可核查 ----------

class TestConcurrentReplacement:
    def test_reader_uses_pinned_complete_catalog(self, tmp_path):
        import sqlite3

        path = str(tmp_path / "concurrent.db")
        app = create_app(path)
        client = app.test_client()
        publish(client, PAGE)
        upload_catalog(client, "B1,学而时习之\n")

        # 模拟“另一窗口”在替换前钉住目录第 1 版，持有独立连接。
        reader = sqlite3.connect(path)
        reader.row_factory = sqlite3.Row
        try:
            upload_catalog(client, "B2,有朋自远方来\n")
            hits, _ = catalog_check.check_page(reader, PAGE, 1, [])
            assert [h["book_no"] for h in hits] == ["B1"]
        finally:
            reader.close()

    def test_reader_during_uncommitted_replacement_gets_old_catalog(self, tmp_path):
        """目录写入事务尚未提交时，其他连接读到的仍是上一份完整目录。"""
        import sqlite3

        path = str(tmp_path / "during.db")
        app = create_app(path)
        client = app.test_client()
        publish(client, PAGE)
        upload_catalog(client, "B1,学而时习之\n")

        reader = sqlite3.connect(path)
        reader.row_factory = sqlite3.Row
        writer = sqlite3.connect(path)
        writer.row_factory = sqlite3.Row
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "INSERT INTO catalog_versions (entry_count, source_bytes) "
                "VALUES (2, 999)")   # 新目录版本已写入但未提交
            hits, _ = catalog_check.check_page(reader, PAGE, 1, [])
            assert [h["book_no"] for h in hits] == ["B1"]
            # 未提交的新版对读者不可见，当前生效版仍是第 1 版。
            row = reader.execute(
                "SELECT MAX(version) FROM catalog_versions").fetchone()
            assert row[0] == 1
            writer.rollback()
        finally:
            reader.close()
            writer.close()

    def test_incomplete_replacement_never_becomes_visible(self, client, monkeypatch):
        upload_catalog(client, "B1,学而时习之\n")
        real = catalog.key_grams

        def broken(norm):
            if norm == "戊己庚辛":
                raise RuntimeError("导入中途失败")
            return real(norm)

        monkeypatch.setattr(catalog, "key_grams", broken)
        resp = upload_catalog(client, "B2,戊己庚辛\n")
        assert resp.status_code == 500
        monkeypatch.undo()
        data = client.get("/api/catalog").get_json()
        assert data["catalog"]["version"] == 1
        assert data["catalog"]["entry_count"] == 1
