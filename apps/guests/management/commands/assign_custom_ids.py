from django.core.management.base import BaseCommand
from guests.models import GuestEntry
from django.db import transaction

class Command(BaseCommand):
    help = "Assign incremental custom_id to guests ordered by date_of_visit"

    def handle(self, *args, **options):
        self.stdout.write("Starting custom_id assignment...")

        guests = GuestEntry.objects.all().order_by('date_of_visit', 'id')

        prefix = "GNG"
        with transaction.atomic():
            current_custom_id = 1
            for guest in guests:
                # Format as GNG000001, GNG000002, ...
                guest.custom_id = f"{prefix}{current_custom_id:06d}"
                guest.save(update_fields=['custom_id'])
                self.stdout.write(f"Assigned custom_id={guest.custom_id} to guest id={guest.id} (date_of_visit={guest.date_of_visit})")
                current_custom_id += 1

        self.stdout.write(self.style.SUCCESS("Successfully assigned custom_id to all guests."))
