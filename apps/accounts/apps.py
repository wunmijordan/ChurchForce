from django.apps import AppConfig

class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

        # Import signals to ensure they are registered
        # This is necessary to connect the signals defined in accounts/signals.py
        # to the appropriate events in the Django lifecycle.
        # This line ensures that the signals are loaded when the app is ready.
        # If you have any signal handlers, they should be defined in accounts/signals.py
        # and will be automatically connected when the app is ready.
        # This is a common practice in Django to ensure that signal handlers are registered
        # when the application starts, allowing them to respond to events such as model saves,
        # deletions, or other actions.
        # Make sure to create the accounts/signals.py file and define your signal handlers there.
        # Example signal handler could be:
        # from django.db.models.signals import post_save
        # from django.dispatch import receiver
        # from .models import User
