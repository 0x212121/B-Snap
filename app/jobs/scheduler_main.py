import logging
import os
from app.core.logging_config import setup_logging
from app.jobs.scheduler import start_scheduler, update_scheduler_config
import time


# Check file flag for reload scheduler config every 60 seconds
if __name__ == "__main__":
    setup_logging()
    logger = logging.getLogger("scheduler")

    start_scheduler()
    logger.info("Scheduler started in the background.")

    try:
        while True:
            if os.path.exists("/tmp/shared/reload_scheduler.flag"):
                update_scheduler_config()
                os.remove("/tmp/shared/reload_scheduler.flag")
                logger.info("✅ Scheduler config reloaded from trigger.")

            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        print("❌ Scheduler stopped.")
