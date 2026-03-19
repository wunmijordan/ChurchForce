"""
core/management/commands/start_scheduler.py

Management command that starts the APScheduler and keeps it running.
Used by the Procfile 'scheduler' process on Railway / Render / Heroku.

Usage:
    python manage.py start_scheduler

This is separate from the scheduler that CoreConfig.ready() starts inside
the web process — this command is for a dedicated scheduler dyno so
scheduled jobs don't compete with request handling on the web workers.

When running both web and scheduler processes (recommended for production),
set SCHEDULER_PROCESS=true in the scheduler dyno's environment so
CoreConfig.ready() skips starting the embedded scheduler.
"""

import logging
import signal
import time
import os

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Start the APScheduler background process (for the Procfile scheduler dyno)."

    def handle(self, *args, **options):
        from core.scheduler import start, get_scheduler

        self.stdout.write("🕐 Starting ChurchForce scheduler process...")
        logger.info("Scheduler process starting.")

        scheduler = start()

        if not scheduler:
            self.stderr.write("Failed to start scheduler. Exiting.")
            return

        self.stdout.write(self.style.SUCCESS("✅ Scheduler running. Press Ctrl+C to stop."))

        # Graceful shutdown on SIGTERM (Railway sends this before killing the process)
        def _shutdown(signum, frame):
            self.stdout.write("\nSIGTERM received — shutting down scheduler gracefully...")
            scheduler.shutdown(wait=True)
            logger.info("Scheduler process stopped.")

        signal.signal(signal.SIGTERM, _shutdown)

        try:
            while True:
                time.sleep(30)
        except KeyboardInterrupt:
            self.stdout.write("\nKeyboardInterrupt — stopping scheduler...")
            scheduler.shutdown(wait=True)
            logger.info("Scheduler process stopped by KeyboardInterrupt.")