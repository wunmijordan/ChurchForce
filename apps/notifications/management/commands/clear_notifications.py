from django.core.management.base import BaseCommand
from notifications.models import Notification

class Command(BaseCommand):
    help = "Mark all notifications as read for all users (safe way to clear notifications)"

    def handle(self, *args, **options):
        total = Notification.objects.update(is_read=True)
        self.stdout.write(self.style.SUCCESS(
            f"✅ Successfully marked {total} notifications as read."
        ))
