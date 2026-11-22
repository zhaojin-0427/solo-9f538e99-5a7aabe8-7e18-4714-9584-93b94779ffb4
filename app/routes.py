"""网页与 JSON 接口。"""

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)

from . import db, service
from .service import ValidationError

bp = Blueprint("main", __name__)


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


# ---------- 网页 ----------

@bp.route("/")
def index():
    conn = db.get_db()
    latest = service.latest_version(conn)
    if latest is None:
        return render_template("version.html", view=None,
                               versions=service.list_versions(conn))
    return redirect(url_for("main.show_version", version=latest))


@bp.route("/pages/<int:version>")
def show_version(version):
    conn = db.get_db()
    view = service.get_version_view(conn, version)
    if view is None:
        return render_template("version.html", view=None,
                               versions=service.list_versions(conn),
                               error=f"第 {version} 版不存在"), 404
    return render_template("version.html", view=view,
                           versions=service.list_versions(conn))


@bp.route("/pages", methods=["POST"])
def upload_page():
    conn = db.get_db()
    try:
        text, csv_text = _extract_upload()
        new_version, results = service.publish_page(conn, text, csv_text)
    except ValidationError as exc:
        flash(f"发布失败：{exc}", "error")
        return redirect(url_for("main.index"))
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
    return jsonify(service.get_version_view(conn, latest))


@bp.get("/api/pages/<int:version>")
def api_version(version):
    view = service.get_version_view(db.get_db(), version)
    if view is None:
        return jsonify({"error": f"第 {version} 版不存在"}), 404
    return jsonify(view)


@bp.post("/api/pages")
def api_publish():
    conn = db.get_db()
    try:
        text, csv_text = _extract_upload()
        new_version, results = service.publish_page(conn, text, csv_text)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        current_app.logger.exception("发布失败")
        return jsonify({"error": "发布失败，已回滚，上一版继续有效"}), 500
    located = sum(1 for r in results if r["status"] == "located")
    pending = sum(1 for r in results if r["status"] == "pending")
    return jsonify({"version": new_version, "located": located,
                    "pending": pending, "results": results}), 201


@bp.get("/healthz")
def healthz():
    return "ok"
