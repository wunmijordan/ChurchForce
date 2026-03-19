# accounts/migrations/0002_seed_events.py
from django.db import migrations
from datetime import date, timedelta

def create_default_events(apps, schema_editor):
    Event = apps.get_model('accounts', 'Event')
    today = date.today()
    current_year = today.year

    # Weekly recurring events (Sunday Service, Midweek, Meeting)
    weekly_events = [
        {"name": "Sunday Service", "event_type": "service", "day_of_week": "sunday"},
        {"name": "Midweek Recharge", "event_type": "midweek", "day_of_week": "thursday"},
        {"name": "Team Meeting", "event_type": "meeting", "day_of_week": "wednesday"},
        {"name": "SOS", "event_type": "training", "day_of_week": "tuesday"},
    ]

    # Create weekly events for the next 52 weeks
    for ev in weekly_events:
        for i in range(0, 52):
            candidate_date = today + timedelta(days=i)
            weekday_map = {'sunday':0,'monday':1,'tuesday':2,'wednesday':3,
                           'thursday':4,'friday':5,'saturday':6}
            if candidate_date.weekday() == weekday_map[ev["day_of_week"]]:
                Event.objects.get_or_create(
                    name=ev["name"],
                    event_type=ev["event_type"],
                    date=candidate_date
                )

class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0015_alter_event_options_event_date_personalreminder'),
    ]

    operations = [
        migrations.RunPython(create_default_events),
    ]
