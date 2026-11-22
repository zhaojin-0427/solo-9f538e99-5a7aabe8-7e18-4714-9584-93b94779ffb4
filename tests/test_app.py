"""自测：引文迁移、重复句歧义、发布失败回滚、历史版查询。"""

import pytest

from app import create_app
from app.align import PageAlignment

TEXT1 = "子曰：学而时习之，不亦说乎。"
#          0  1  2  3  4  5  6  7  8  9 10 11 12 13
CSV1 = "citation_id,start,end\nq1,3,8\n"  # q1 = “学而时习之”


@pytest.fixture()
def app(tmp_path):
    application = create_app(str(tmp_path / "test.db"))
    application.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def post_page(client, text, csv_text=""):
    return client.post("/api/pages", json={"text": text, "citations_csv": csv_text})


def get_citation(view, cid):
    for c in view["citations"]:
        if c["citation_id"] == cid:
            return c
    raise AssertionError(f"引用 {cid} 不在结果中")


# ---------- 整页比对单元测试 ----------

class TestPageAlignment:
    def test_identical_texts(self):
        al = PageAlignment("学而时习之", "学而时习之")
        assert al.distance == 0
        assert al.relocate(0, 5) == (0, 5)

    def test_insertion_before(self):
        al = PageAlignment("学而时习之", "孔子曰学而时习之")
        assert al.distance == 3
        assert al.relocate(0, 5) == (3, 8)

    def test_repeated_chars_ambiguous(self):
        # “aaaa” 删一个 a 得 “aaa”：每个 a 都可能在某种最优对齐里被删
        al = PageAlignment("aaaa", "aaa")
        assert al.distance == 1
        assert al.relocate(0, 2) is None

    def test_substitution_inside_span(self):
        al = PageAlignment("学而时习之", "学而时习焉")
        assert al.relocate(0, 5) is None  # “之”被替换，无法逐字对上

    def test_bounds(self):
        al = PageAlignment("abc", "abc")
        with pytest.raises(ValueError):
            al.relocate(2, 9)


# ---------- 发布与迁移 ----------

class TestPublish:
    def test_first_publish_and_latest(self, client):
        resp = post_page(client, TEXT1, CSV1)
        assert resp.status_code == 201
        assert resp.get_json()["version"] == 1

        view = client.get("/api/pages/latest").get_json()
        assert view["version"] == 1
        assert view["char_count"] == len(TEXT1)
        q1 = get_citation(view, "q1")
        assert q1["status"] == "located"
        assert (q1["start"], q1["end"]) == (3, 8)
        assert q1["located_version"] == 1
        assert q1["excerpt"] == "学而时习之"

    def test_relocate_after_insertion_before(self, client):
        post_page(client, TEXT1, CSV1)
        resp = post_page(client, "孔" + TEXT1)  # 句首插入“孔”
        assert resp.status_code == 201
        view = client.get("/api/pages/latest").get_json()
        q1 = get_citation(view, "q1")
        assert q1["status"] == "located"
        assert (q1["start"], q1["end"]) == (4, 9)
        assert q1["located_version"] == 2
        assert q1["excerpt"] == "学而时习之"

    def test_relocate_after_deletion_before(self, client):
        post_page(client, TEXT1, CSV1)
        post_page(client, TEXT1[3:])  # 删掉“子曰：”
        view = client.get("/api/pages/latest").get_json()
        q1 = get_citation(view, "q1")
        assert q1["status"] == "located"
        assert (q1["start"], q1["end"]) == (0, 5)
        assert q1["excerpt"] == "学而时习之"

    def test_substitution_outside_citation_still_located(self, client):
        post_page(client, TEXT1, CSV1)
        post_page(client, "子曰：学而时习之，不亦乐乎。")  # “说”→“乎”，在引文之外
        view = client.get("/api/pages/latest").get_json()
        q1 = get_citation(view, "q1")
        assert q1["status"] == "located"
        assert (q1["start"], q1["end"]) == (3, 8)

    def test_substitution_inside_citation_pending(self, client):
        post_page(client, TEXT1, CSV1)
        post_page(client, "子曰：学而时习焉，不亦说乎。")  # “之”→“焉”，在引文之内
        view = client.get("/api/pages/latest").get_json()
        q1 = get_citation(view, "q1")
        assert q1["status"] == "pending"
        # 待核对：显示最近一次定位成功的版号和位置
        assert q1["located_version"] == 1
        assert (q1["start"], q1["end"]) == (3, 8)
        assert q1["excerpt"] == "学而时习之"

    def test_pending_then_text_restored_locates_again(self, client):
        post_page(client, TEXT1, CSV1)
        post_page(client, "子曰：学而时习焉，不亦说乎。")  # v2：引文被改 → 待核对
        post_page(client, TEXT1)                            # v3：恢复原文
        view = client.get("/api/pages/3").get_json()
        q1 = get_citation(view, "q1")
        # 以最近一次成功定位的第 1 版为源稿重新定位
        assert q1["status"] == "located"
        assert (q1["start"], q1["end"]) == (3, 8)
        assert q1["located_version"] == 3

    def test_pending_carries_forward(self, client):
        post_page(client, TEXT1, CSV1)
        post_page(client, "子曰：学而时习焉，不亦说乎。")  # v2：待核对
        post_page(client, "子曰：学而时习焉，不亦说乎！")  # v3：又改标点，仍对不上
        view = client.get("/api/pages/3").get_json()
        q1 = get_citation(view, "q1")
        assert q1["status"] == "pending"
        assert q1["located_version"] == 1
        assert (q1["start"], q1["end"]) == (3, 8)


# ---------- 重复句 ----------

class TestRepeatedSentences:
    def test_repeated_sentence_deletion_is_ambiguous(self, client):
        # 两个相同句子，新版删掉其中一句：两种最优对齐，引文落点不唯一
        resp = post_page(client, "日出。日出。", "q1,0,2\nq2,3,5\n")
        assert resp.status_code == 201
        post_page(client, "日出。")
        view = client.get("/api/pages/latest").get_json()
        for cid in ("q1", "q2"):
            c = get_citation(view, cid)
            assert c["status"] == "pending"
            assert c["located_version"] == 1

    def test_repeated_sentence_with_unique_context_located(self, client):
        # 重复句但改动在两处之外：落点唯一，照常登记
        post_page(client, "甲乙。丙丁。甲乙。", "q1,0,2\nq2,6,8\n")
        post_page(client, "序：甲乙。丙丁。甲乙。")  # 句首插入“序：”
        view = client.get("/api/pages/latest").get_json()
        q1 = get_citation(view, "q1")
        q2 = get_citation(view, "q2")
        assert q1["status"] == "located" and (q1["start"], q1["end"]) == (2, 4)
        assert q2["status"] == "located" and (q2["start"], q2["end"]) == (8, 10)
        assert q1["excerpt"] == q2["excerpt"] == "甲乙"

    def test_repeated_sentence_one_occurrence_edited(self, client):
        # 重复句中第二处被改：第一处照常定位，被改的待核对
        post_page(client, "甲乙。丙。甲乙。", "q1,0,2\nq2,5,7\n")
        post_page(client, "甲乙。丙。甲丁。")  # 第二处“甲乙”→“甲丁”
        view = client.get("/api/pages/latest").get_json()
        q1 = get_citation(view, "q1")
        q2 = get_citation(view, "q2")
        assert q1["status"] == "located" and (q1["start"], q1["end"]) == (0, 2)
        assert q2["status"] == "pending" and q2["located_version"] == 1


# ---------- 发布失败：继续提供上一版 ----------

class TestPublishFailure:
    def test_invalid_inputs_rejected_and_previous_kept(self, client):
        post_page(client, TEXT1, CSV1)

        bad_cases = [
            ("x" * 1201, ""),                 # 超过 1200 字
            ("", ""),                          # 空文本
            ("新的页稿", "q9,0,99"),            # 下标越界
            ("新的页稿", "q9,2,1"),             # 起始不小于结束
            ("新的页稿", "q9,0,1\nq9,1,2"),     # 引用号重复
            ("新的页稿", "q9,甲,1"),            # 下标不是整数
            ("新的页稿", "q9,0"),               # 列数不足
        ]
        for text, csv_text in bad_cases:
            resp = post_page(client, text, csv_text)
            assert resp.status_code == 400, (text, csv_text)

        versions = client.get("/api/versions").get_json()
        assert versions["versions"] == [1]
        view = client.get("/api/pages/latest").get_json()
        assert view["version"] == 1
        assert view["text"] == TEXT1

    def test_mid_transaction_failure_rolls_back(self, client):
        post_page(client, TEXT1, CSV1)
        # 引用号含孤立代理字符：页稿能入库、引文写入时报错，
        # 用来模拟“发布到一半失败”，整个事务必须回滚。
        resp = post_page(client, "一页正常的新校样", "\ud800坏引用,0,2\n")
        assert resp.status_code == 500

        versions = client.get("/api/versions").get_json()
        assert versions["versions"] == [1]          # 新版没有留下
        view = client.get("/api/pages/latest").get_json()
        assert view["version"] == 1                  # 继续提供上一版
        assert view["text"] == TEXT1
        # 失败之后还能正常发布
        resp = post_page(client, "子曰：学而时习之，不亦乐乎。")
        assert resp.status_code == 201
        assert resp.get_json()["version"] == 2


# ---------- 历史版查询 ----------

class TestHistory:
    def test_versions_queryable_by_number(self, client):
        post_page(client, TEXT1, CSV1)
        post_page(client, "孔" + TEXT1)
        post_page(client, "孟子曰：学而时习之，不亦说乎。")

        assert client.get("/api/versions").get_json()["versions"] == [1, 2, 3]

        v1 = client.get("/api/pages/1").get_json()
        assert v1["text"] == TEXT1
        q1 = get_citation(v1, "q1")
        assert (q1["start"], q1["end"]) == (3, 8)  # 历史快照不被后续改动影响

        v3 = client.get("/api/pages/3").get_json()
        q1 = get_citation(v3, "q1")
        assert q1["status"] == "located"
        assert (q1["start"], q1["end"]) == (4, 9)  # “孟子曰：”占 4 字
        assert q1["excerpt"] == "学而时习之"

        assert client.get("/api/pages/99").status_code == 404

    def test_csv_can_declare_on_later_version(self, client):
        post_page(client, TEXT1, CSV1)
        # 第二版用 CSV 直接登记一条新引用（下标按新稿计）
        post_page(client, TEXT1 + "三人行，必有我师焉。", "q2,14,18\n")
        view = client.get("/api/pages/2").get_json()
        q1 = get_citation(view, "q1")
        q2 = get_citation(view, "q2")
        assert q1["status"] == "located" and (q1["start"], q1["end"]) == (3, 8)
        assert q2["status"] == "located" and (q2["start"], q2["end"]) == (14, 18)
        assert q2["located_version"] == 2
        assert q2["excerpt"] == "三人行，"


# ---------- 同一对稿件共用整页比对 ----------

class TestSharedAlignment:
    def test_one_alignment_per_source_pair(self, client, monkeypatch):
        from app import service

        created = []
        real = service.PageAlignment

        class Counting(real):
            def __init__(self, old, new):
                created.append((old, new))
                super().__init__(old, new)

        monkeypatch.setattr(service, "PageAlignment", Counting)
        post_page(client, TEXT1, "q1,3,8\nq2,9,14\n")
        assert created == []                      # 首版无需比对
        post_page(client, "孔" + TEXT1)           # 两条引用同出第 1 版
        assert len(created) == 1                  # 整页只比对一次


# ---------- 网页 ----------

class TestHtml:
    def test_pages_render(self, client):
        assert client.get("/").status_code == 200  # 空库时的上传页
        post_page(client, TEXT1, CSV1)
        resp = client.get("/")
        assert resp.status_code == 302
        resp = client.get("/pages/1")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "学而时习之" in html
        assert "已定位" in html
        post_page(client, "子曰：学而时习焉，不亦说乎。")
        html = client.get("/pages/2").get_data(as_text=True)
        assert "待核对" in html
        assert client.get("/pages/99").status_code == 404

    def test_multipart_upload(self, client):
        import io
        data = {
            "text": (io.BytesIO(TEXT1.encode("utf-8")), "page.txt"),
            "citations": (io.BytesIO(CSV1.encode("utf-8")), "citations.csv"),
        }
        resp = client.post("/pages", data=data,
                           content_type="multipart/form-data")
        assert resp.status_code == 302
        view = client.get("/api/pages/latest").get_json()
        assert view["version"] == 1
        assert get_citation(view, "q1")["status"] == "located"
