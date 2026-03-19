# accounts/migrations/0018_cleanup_events.py
from django.db import migrations

def cleanup_and_reset_events(apps, schema_editor):
    Event = apps.get_model('accounts', 'Event')
    # Clean out duplicates
    Event.objects.filter(
        name__in=["Sunday Service", "Midweek Recharge", "Team Meeting", "SOS"]
    ).delete()

    # Recreate clean weekly ones
    events = [
        {"name": "Sunday Service", "event_type": "service", "day_of_week": "sunday"},
        {"name": "Midweek Recharge", "event_type": "midweek", "day_of_week": "thursday"},
        {"name": "Team Meeting", "event_type": "meeting", "day_of_week": "wednesday"},
        {"name": "SOS", "event_type": "training", "day_of_week": "tuesday"},
    ]

    for ev in events:
        Event.objects.get_or_create(
            name=ev["name"],
            event_type=ev["event_type"],
            day_of_week=ev["day_of_week"],
            is_active=True,
        )

class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_event_duration_days_event_end_date'),
    ]

    operations = [
        migrations.RunPython(cleanup_and_reset_events),
    ]
