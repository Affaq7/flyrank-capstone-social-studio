import logging
import os

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from app.worker import run_poll_once

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    poll_seconds = int(os.environ.get("WORKER_POLL_SECONDS", "10"))
    jobstore = SQLAlchemyJobStore(url=os.environ["DATABASE_URL"], tablename="apscheduler_jobs")
    scheduler = BlockingScheduler(jobstores={"default": jobstore})

    scheduler.add_job(
        run_poll_once,
        trigger="interval",
        seconds=poll_seconds,
        id="poll_due_slots",
        replace_existing=True,
        misfire_grace_time=None,  # always run the backlog, even if very late
    )

    logging.getLogger("worker").info(
        "Worker starting: polling every %ss (job store: Postgres, table 'apscheduler_jobs')",
        poll_seconds,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
