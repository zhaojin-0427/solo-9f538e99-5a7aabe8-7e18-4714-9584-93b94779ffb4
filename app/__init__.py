import os

from flask import Flask

from . import db
from .routes import bp


def create_app(database_path=None):
    app = Flask(__name__)
    app.config["DATABASE_PATH"] = (
        database_path
        or os.environ.get("DATABASE_PATH")
        or os.path.join(app.instance_path, "gazetteer.db")
    )
    # 目录最多 32 MiB；请求体可能因表单封装或 JSON 转义（非 ASCII 最多 6 倍）
    # 而变大，这里放宽到 96 MiB，真正的 32 MiB 上限由 catalog.parse_catalog
    # 按原始字节校验。
    app.config["MAX_CONTENT_LENGTH"] = 96 * 1024 * 1024
    app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
    db.init_db(app.config["DATABASE_PATH"])
    app.teardown_appcontext(db.close_db)
    app.register_blueprint(bp)
    return app
