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
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB，页稿上限 1200 字足够
    app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
    db.init_db(app.config["DATABASE_PATH"])
    app.teardown_appcontext(db.close_db)
    app.register_blueprint(bp)
    return app
