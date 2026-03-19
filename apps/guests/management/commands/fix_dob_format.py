from django.core.management.base import BaseCommand
from guests.models import GuestEntry
import datetime

class Command(BaseCommand):
    help = "Fix old date_of_birth values stored as '1900-MM-DD' to 'Month Day' strings"

    def handle(self, *args, **options):
        guests = GuestEntry.objects.filter(date_of_birth__startswith="1900-")
        total = guests.count()
        self.stdout.write(f"Found {total} guest(s) with old DOB format.")

        for guest in guests:
            try:
                # Parse old date string
                dt = datetime.datetime.strptime(guest.date_of_birth, "%Y-%m-%d")
                # Format to "Month Day"
                guest.date_of_birth = dt.strftime("%B %d")
                guest.save(update_fields=["date_of_birth"])
                self.stdout.write(f"Updated {guest.full_name} -> {guest.date_of_birth}")
            except Exception as e:
                self.stderr.write(f"Failed to update {guest.full_name}: {e}")

        self.stdout.write(self.style.SUCCESS("Date of birth format fix completed."))
