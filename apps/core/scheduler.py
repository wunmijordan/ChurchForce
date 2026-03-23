"""
core/scheduler.py

The single APScheduler instance for the entire application.
All apps register their jobs here — there is only ever one scheduler
process running, regardless of how many apps have scheduled work.

Architecture:
    core/scheduler.py          ← this file: instance + start() + job registry
    workforce/scheduler_jobs.py ← event broadcast + push notification jobs
    billing/renewal_reminders.py ← subscription renewal reminder job
    guests/scheduler_jobs.py    ← flag_committed_guests daily job

start() is called once from core/apps.py CoreConfig.ready().
Each app's job module is imported inside start() — never at module level —
so there are no circular imports and no jobs fire during migrations.

Why APScheduler over Celery for this project:
    - Runs in-process — no separate worker process to manage
    - Redis is already present (Channels) but no broker config needed
    - All scheduled tasks are lightweight (notifications, reminders, DB queries)
    - Celery adds deployment complexity that isn't justified at this scale
"""

import threading
from apscheduler.schedulers.background import BackgroundScheduler
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# Global scheduler instance
# Imported by all job modules: from core.scheduler import scheduler
# ─────────────────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone=timezone.get_current_timezone())


# ─────────────────────────────────────────────────────────────────────────────
# Start — idempotent, threadsafe
# ─────────────────────────────────────────────────────────────────────────────

def start():
    """
    Start the scheduler in a background thread and register all jobs.

    Called once from core/apps.py CoreConfig.ready(). Safe to call
    multiple times — the _started guard prevents double-starts.

    Job modules are imported inside this function to avoid circular
    imports and to ensure models are available (ready() fires after
    all apps are loaded).
    """
    if getattr(scheduler, "_started", False):
        print("⚠️  [Scheduler] Already running — skipping.")
        return

    def _run():
        try:
            scheduler.start()
            scheduler._started = True
            print("✅ [Scheduler] Started.")

            # ── Register workforce jobs ───────────────────────────────
            from workforce.scheduler_jobs import register_workforce_jobs
            register_workforce_jobs(scheduler)

            # ── Register billing jobs ─────────────────────────────────
            from billing.renewal_reminders import register_billing_jobs
            register_billing_jobs(scheduler)

            # ── Register guest pipeline jobs ──────────────────────────
            from guests.scheduler_jobs import register_guest_jobs
            register_guest_jobs(scheduler)

            # ── Register birthday notification jobs ───────────────────
            from core.birthday_jobs import register_birthday_jobs
            register_birthday_jobs(scheduler)

            # ── Seed initial data after short delay ───────────────────
            # 2s delay ensures Django ORM is fully ready before first query
            import threading as _t
            _t.Timer(2.0, _run_startup_jobs).start()

            # ── Demo nightly reset (only if DEMO_SUBDOMAIN is set) ──────
            from django.conf import settings as django_settings
            demo_sub = getattr(django_settings, "DEMO_SUBDOMAIN", "")
            if demo_sub:
                scheduler.add_job(
                    _reset_demo,
                    "cron",
                    hour=3,
                    minute=0,
                    id="demo_nightly_reset",
                    replace_existing=True,
                )
                print(f"🎭 [Scheduler] Demo nightly reset registered for subdomain '{demo_sub}'")

            print("🔁 [Scheduler] All jobs registered.")

        except Exception as exc:
            print(f"❌ [Scheduler] Failed to start: {exc}")

    threading.Thread(target=_run, daemon=True).start()


def _run_startup_jobs():
    """
    Run jobs that need to fire once on startup (after ORM is ready).
    These are the same functions that run on their daily cron schedule.
    """
    try:
        from workforce.scheduler_jobs import (
            schedule_event_notifications,
            schedule_push_notifications,
        )
        schedule_event_notifications()
        schedule_push_notifications()
    except Exception as exc:
        print(f"❌ [Scheduler] Startup job error: {exc}")


def _reset_demo():
    """Nightly demo reset — wipes and reseeds the demo church at 3 AM."""
    from django.conf import settings as django_settings
    from django.core.management import call_command
    subdomain = getattr(django_settings, "DEMO_SUBDOMAIN", "demo")
    try:
        call_command("seed_demo", subdomain=subdomain, reset=True)
        print(f"🎭 [Scheduler] Demo church '{subdomain}' reset successfully")
    except Exception as exc:
        print(f"❌ [Scheduler] Demo reset failed: {exc}")


def get_scheduler():
    """Return the global scheduler instance. Used by the start_scheduler command."""
    return scheduler
