from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        """
        Start the application-wide scheduler once all apps are loaded.

        This is the single point where the scheduler boots. All other
        apps (workforce, billing, guests) register their jobs into it
        via core/scheduler.py — none of them start their own scheduler.
        """
        # Guard against double-start in development (Django auto-reloader
        # imports AppConfig twice in some configurations)
        import os
        if os.environ.get("RUN_MAIN") == "true" or not _is_reloader_child():
            from core.scheduler import start
            start()


def _is_reloader_child():
    """
    Return True if we're in the Django auto-reloader child process.
    In that process, ready() fires twice — we only want to start the
    scheduler once (in the child, not the parent watcher process).
    """
    import os
    # Django sets this in the child process spawned by the reloader
    return os.environ.get("RUN_MAIN") != "true"