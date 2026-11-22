"""网页与 JSON 接口。"""

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)
from werkzeug.exceptions import RequestEntityTooLarge

from . import catalog, catalog_check, db, service
from .errors import ValidationError

bp = Blueprint("main", __name__)


@bp.app_errorhandler(413)
def too_large(_exc):
    message = "上传内容过大：引文目录最多 32 MiB"
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), 413
    flash(message, "error")
    target = "main.catalog_page" if request.path.startswith("/catalog") \
        else "main.index"
    return redirect(url_for(target))


def _read_upload(file_storage):
    try:
        return file_storage.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError("上传文件不是有效的 UTF-8 文本")


def _extract_upload():
    """从 JSON 或 multipart 表单中取出页稿文本和引用 CSV。"""
    if request.is_json:
        data = request.get_json(silent=True) or {}
        return data.get("text") or "", data.get("citations_csv") or ""
    text = ""
    f = request.files.get("text")
    if f and f.filename:
        text = _read_upload(f)
    elif request.form.get("text"):
        text = request.form["text"]
    csv_text = ""
    f = request.files.get("citations")
    if f and f.filename:
        csv_text = _read_upload(f)
    elif request.form.get("citations_csv"):
        csv_text = request.form["citations_csv"]
    return text, csv_text


def _extract_catalog_bytes():
    """从 multipart、表单或 JSON 中取出目录 CSV 的原始字节。"""
    if request.is_json:
        data = request.get_json(silent=True) or {}
        for key in ("catalog_csv", "catalog", "csv", "content", "data"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value.encode("utf-8")
        raise ValidationError("JSON 请求需要提供 catalog_csv 字符串")
    for field in ("catalog", "catalog_file", "catalog_csv", "citations",
                  "csv", "file", "upload", "data"):
        f = request.files.get(field)
        if f and f.filename:
            return f.read()
    for field in ("catalog_csv", "citations_csv", "csv_text", "text"):
        if request.form.get(field) is not None:
            return request.form[field].encode("utf-8")
    if request.data:
        # 允许直接提交 text/csv 正文。
        return request.get_data(cache=False)
    raise ValidationError("请选择要上传的引文目录 CSV 文件")


def _located_intervals(view):
    return [(c["start"], c["end"]) for c in view["citations"]
            if c["status"] == "located"]


def _resolve_catalog(conn, requested=None):
    """确定本次核查使用哪一版目录。

    默认用最新生效版；也可显式钉住某一版。整次核查只用这一版，
    即使期间目录被替换，本次结果也保持一致。
    """
    if requested is None:
        return catalog.current_catalog(conn)
    return catalog.catalog_version_info(conn, requested)


def _check_view(conn, view, requested_catalog=None):
    """用一份生效目录核查一版，供详情页和独立核查页共用。"""
    catalog_info = _resolve_catalog(conn, requested_catalog)
    if view is None or catalog_info is None:
        return catalog_info, [], 0
    hits, skipped = catalog_check.check_page(
        conn, view["text"], catalog_info["version"],
        _located_intervals(view))
    return catalog_info, hits, skipped


def _attach_catalog_info(conn, view):
    info = catalog.current_catalog(conn)
    view["catalog_version"] = info["version"] if info else None
    view["catalog_entry_count"] = info["entry_count"] if info else 0
    return view


# ---------- 网页 ----------

@bp.route("/")
def index():
    conn = db.get_db()
    latest = service.latest_version(conn)
    if latest is None:
        return render_template("version.html", view=None, hits=[], skipped=0,
                               versions=service.list_versions(conn),
                               catalog=catalog.current_catalog(conn))
    return redirect(url_for("main.show_version", version=latest))


@bp.route("/pages/<int:version>")
def show_version(version):
    conn = db.get_db()
    view = service.get_version_view(conn, version)
    if view is None:
        return render_template("version.html", view=None, hits=[], skipped=0,
                               versions=service.list_versions(conn),
                               catalog=catalog.current_catalog(conn),
                               error=f"第 {version} 版不存在"), 404
    catalog_info, hits, skipped = _check_view(conn, view)
    return render_template("version.html", view=view, hits=hits,
                           skipped=skipped,
                           versions=service.list_versions(conn),
                           catalog=catalog_info)


@bp.route("/pages/<int:version>/check")
def check_version(version):
    conn = db.get_db()
    view = service.get_version_view(conn, version)
    if view is None:
        return render_template("check.html", view=None, hits=[], skipped=0,
                               versions=service.list_versions(conn),
                               catalog=None,
                               error=f"第 {version} 版不存在"), 404
    requested = request.args.get("catalog_version", type=int)
    catalog_info, hits, skipped = _check_view(conn, view, requested)
    if catalog_info is None:
        error = ("指定的目录版本不存在" if requested is not None
                 else "还没有引文目录，请先导入引文目录")
    else:
        error = None
    return render_template("check.html", view=view, hits=hits, skipped=skipped,
                           versions=service.list_versions(conn),
                           catalog=catalog_info,
                           catalog_versions=catalog.list_catalog_versions(conn),
                           error=error)


@bp.route("/catalog")
def catalog_page():
    conn = db.get_db()
    return render_template(
        "catalog.html",
        catalog=catalog.current_catalog(conn),
        catalog_versions=catalog.list_catalog_versions(conn),
        versions=service.list_versions(conn))


@bp.route("/catalog/import", methods=["POST"])
@bp.route("/catalog", methods=["POST"])
def upload_catalog():
    conn = db.get_db()
    try:
        raw = _extract_catalog_bytes()
        entries = catalog.parse_catalog(raw)
        version = catalog.save_catalog(conn, entries, len(raw))
    except ValidationError as exc:
        flash(f"导入引文目录失败：{exc}。继续使用原有目录。", "error")
        return redirect(url_for("main.catalog_page"))
    except RequestEntityTooLarge:
        raise
    except Exception:
        current_app.logger.exception("导入引文目录失败")
        flash("导入引文目录失败：内部错误，已回滚，原有目录继续有效。", "error")
        return redirect(url_for("main.catalog_page"))
    flash(f"引文目录第 {version} 版导入成功：{len(entries)} 条", "ok")
    return redirect(url_for("main.catalog_page"))


@bp.route("/pages", methods=["POST"])
def upload_page():
    conn = db.get_db()
    try:
        text, csv_text = _extract_upload()
        new_version, results = service.publish_page(conn, text, csv_text)
    except ValidationError as exc:
        flash(f"发布失败：{exc}", "error")
        return redirect(url_for("main.index"))
    except RequestEntityTooLarge:
        raise
    except Exception:
        current_app.logger.exception("发布失败")
        flash("发布失败：内部错误，已回滚，上一版继续有效", "error")
        return redirect(url_for("main.index"))
    located = sum(1 for r in results if r["status"] == "located")
    pending = sum(1 for r in results if r["status"] == "pending")
    flash(f"第 {new_version} 版发布成功：已定位 {located} 条，待核对 {pending} 条", "ok")
    return redirect(url_for("main.show_version", version=new_version))


# ---------- JSON 接口 ----------

@bp.get("/api/versions")
def api_versions():
    versions = service.list_versions(db.get_db())
    return jsonify({"versions": [v["version"] for v in versions],
                    "latest": versions[-1]["version"] if versions else None})


@bp.get("/api/pages/latest")
def api_latest():
    conn = db.get_db()
    latest = service.latest_version(conn)
    if latest is None:
        return jsonify({"error": "还没有任何版本"}), 404
    view = service.get_version_view(conn, latest)
    _attach_catalog_info(conn, view)
    return jsonify(view)


@bp.get("/api/pages/<int:version>")
def api_version(version):
    conn = db.get_db()
    view = service.get_version_view(conn, version)
    if view is None:
        return jsonify({"error": f"第 {version} 版不存在"}), 404
    _attach_catalog_info(conn, view)
    return jsonify(view)


@bp.post("/api/pages")
def api_publish():
    conn = db.get_db()
    try:
        text, csv_text = _extract_upload()
        new_version, results = service.publish_page(conn, text, csv_text)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except RequestEntityTooLarge:
        raise
    except Exception:
        current_app.logger.exception("发布失败")
        return jsonify({"error": "发布失败，已回滚，上一版继续有效"}), 500
    located = sum(1 for r in results if r["status"] == "located")
    pending = sum(1 for r in results if r["status"] == "pending")
    return jsonify({"version": new_version, "located": located,
                    "pending": pending, "results": results}), 201


@bp.get("/api/catalogs")
@bp.get("/api/catalog")
def api_catalog():
    conn = db.get_db()
    current = catalog.current_catalog(conn)
    if current is None:
        return jsonify({"catalog": None, "versions": []})
    return jsonify({"catalog": current,
                    "versions": catalog.list_catalog_versions(conn)})


@bp.post("/api/catalogs")
@bp.post("/api/catalog")
@bp.post("/api/catalog/import")
def api_upload_catalog():
    conn = db.get_db()
    try:
        raw = _extract_catalog_bytes()
        entries = catalog.parse_catalog(raw)
        version = catalog.save_catalog(conn, entries, len(raw))
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except RequestEntityTooLarge:
        raise
    except Exception:
        current_app.logger.exception("导入引文目录失败")
        return jsonify({"error": "导入失败，已回滚，原有目录继续有效"}), 500
    return jsonify({"catalog_version": version,
                    "entry_count": len(entries)}), 201


@bp.get("/api/pages/<int:version>/check")
def api_check(version):
    conn = db.get_db()
    view = service.get_version_view(conn, version)
    if view is None:
        return jsonify({"error": f"第 {version} 版不存在"}), 404
    requested = request.args.get("catalog_version", type=int)
    catalog_info = _resolve_catalog(conn, requested)
    if catalog_info is None:
        if requested is not None:
            return jsonify({"error": f"目录第 {requested} 版不存在"}), 404
        return jsonify({"error": "还没有引文目录，请先导入引文目录"}), 409
    hits, skipped = catalog_check.check_page(
        conn, view["text"], catalog_info["version"], _located_intervals(view))
    return jsonify({
        "version": version,
        "catalog_version": catalog_info["version"],
        "catalog_entry_count": catalog_info["entry_count"],
        "located_count": len(_located_intervals(view)),
        "skipped": skipped,
        "hits": hits,
    })


@bp.get("/healthz")
def healthz():
    return "ok"
