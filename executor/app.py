# smart_agent/executor/app.py
import logging
from flask import Flask

from executor.config import *
from executor.controller import api

def create_app() -> Flask:
    sa_executor = Flask(__name__)

    # --- logging ---
    root_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, root_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx / werkzeug
    HTTP_DEBUG = os.getenv("HTTP_DEBUG", "0") == "1"
    logging.getLogger("httpx").setLevel(logging.INFO if HTTP_DEBUG else logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.INFO)

    # Replicate token -> env (для клиента)
    if REPLICATE_API_TOKEN and not os.getenv("REPLICATE_API_TOKEN"):
        os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN

    # маршруты
    sa_executor.register_blueprint(api)

    @sa_executor.get("/")
    def root():
        return {"ok": True, "service": "executor"}, 200

    return sa_executor


if __name__ == "__main__":
    app = create_app()
    app.logger.info("Webhook server started on http://%s:%s", EXECUTOR_HOST, EXECUTOR_PORT)
    app.run(host=EXECUTOR_HOST, port=EXECUTOR_PORT)
