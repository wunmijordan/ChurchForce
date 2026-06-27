from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class BibleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bible"
    verbose_name = "Bible"

    def ready(self):
        import sys

        _skip_commands = {
            "migrate",
            "makemigrations",
            "collectstatic",
            "shell",
            "dbshell",
            "check",
            "test",
            "createsuperuser",
            "flush",
            "dumpdata",
            "loaddata",
            "showmigrations",
            "squashmigrations",
        }
        if len(sys.argv) > 1 and sys.argv[1] in _skip_commands:
            return

        # Everything that touches the ORM or network must run in a thread.
        # Django's ready() is called in an async context under ASGI/Uvicorn,
        # so synchronous DB access here raises:
        #   "You cannot call this from an async context - use a thread or sync_to_async"
        import threading

        def _sync_in_background():
            import time

            # Small delay so the connection pool and DB are ready
            time.sleep(2)
            try:
                from bible.services.apibible import is_configured, get_translations

                if not is_configured():
                    return

                from django.db import OperationalError, ProgrammingError

                try:
                    from bible.models import ApiBibleTranslationMap

                    if ApiBibleTranslationMap.objects.exists():
                        return  # already populated — nothing to do
                except (OperationalError, ProgrammingError):
                    # Table doesn't exist yet — migrations haven't run. Skip silently.
                    return

                logger.info(
                    "bible: ApiBibleTranslationMap empty — syncing from API.Bible…"
                )
                results = get_translations(force_refresh=True)
                if results:
                    logger.info(
                        "bible: synced %d API.Bible translations at startup",
                        len(results),
                    )
                else:
                    logger.warning(
                        "bible: startup sync returned 0 translations — "
                        "check APIBIBLE_API_KEY and that your app at scripture.api.bible is active"
                    )

            except Exception as exc:
                logger.warning("bible: startup API.Bible sync skipped — %s", exc)

        t = threading.Thread(target=_sync_in_background, daemon=True)
        t.start()
