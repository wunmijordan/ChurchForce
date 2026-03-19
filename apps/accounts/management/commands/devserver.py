"""
accounts/management/commands/devserver.py

Single-command dev launcher:
    python manage.py devserver

Starts in parallel:
  1. Uvicorn (ASGI — HTTP + WebSocket, auto-reload)
  2. APScheduler via start_scheduler management command

Redis must be running separately (redis-server or systemd service).
"""

import subprocess
import sys
import threading
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand


def _run_scheduler():
    """Run the APScheduler in this thread (blocks until Ctrl+C)."""
    try:
        call_command("start_scheduler")
    except Exception as exc:
        print(f"[scheduler] stopped: {exc}")


class Command(BaseCommand):
    help = "Start Uvicorn (ASGI) + APScheduler for local development."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", default="8000")
        parser.add_argument(
            "--no-scheduler",
            action="store_true",
            help="Skip starting the APScheduler (useful when you want a clean console).",
        )

    def handle(self, *args, **options):
        host = options["host"]
        port = options["port"]

        self.stdout.write(self.style.SUCCESS(f"🚀  ChurchForce dev server → http://{host}:{port}"))
        self.stdout.write(self.style.WARNING("    WebSocket: ws://{host}:{port}/ws/"))
        self.stdout.write("")

        # ── Optional: scheduler in background thread ──────────────────
        if not options["no_scheduler"]:
            sched_thread = threading.Thread(target=_run_scheduler, daemon=True)
            sched_thread.start()
            self.stdout.write("📅  APScheduler started in background thread")
            time.sleep(0.5)   # let scheduler log its startup lines first

        # ── Uvicorn (blocking — stays in foreground) ──────────────────
        try:
            subprocess.run(
                [
                    "uvicorn",
                    "churchforce.asgi:application",
                    "--reload",
                    "--host", host,
                    "--port", port,
                    "--reload-dir", "apps",        # only watch apps/ — avoids spurious reloads
                    "--reload-dir", "templates",
                    "--log-level", "info",
                ],
                check=False,
            )
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\n🛑  Dev server stopped (Ctrl+C)"))
            sys.exit(0)