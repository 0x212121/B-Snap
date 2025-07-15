import logging
from app.core.logging_config import setup_logging
from app.jobs.scheduler import start_scheduler
import time

if __name__ == "__main__":
    setup_logging()
    logger = logging.getLogger("main")

    start_scheduler()
    logger.info("Scheduler started in the background.")

    # biar nggak langsung exit
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("❌ Scheduler stopped.")
