import os
from app.core.logging_config import setup_logging, set_debug_mode

worker_class = "uvicorn.workers.UvicornWorker"

# jumlah worker sesuai CPU-mu
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
bind = os.getenv("BIND", "0.0.0.0:8080")

# Tulis juga ke stdout/stderr (biar kelihatan di journalctl/docker logs)
accesslog = "-"
errorlog  = "-"
loglevel  = "info"
capture_output = True

def post_fork(server, worker):
    # re-init logging di worker (penting!)
    setup_logging()
    if os.getenv("APP_DEBUG", "0").lower() in ("1", "true", "yes"):
        set_debug_mode(True)
