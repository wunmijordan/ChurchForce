from django.db import migrations, models


def copy_plan_translation(apps, schema_editor):
    ReadingPlanEntry = apps.get_model("bible", "ReadingPlanEntry")
    for entry in ReadingPlanEntry.objects.select_related("plan").all().iterator():
        entry.translation = (entry.plan.translation or "BSB").upper()
        entry.save(update_fields=["translation"])


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0008_readingplanentry_chapter_to"),
    ]

    operations = [
        migrations.AddField(
            model_name="readingplanentry",
            name="translation",
            field=models.CharField(default="BSB", max_length=20),
        ),
        migrations.RunPython(copy_plan_translation, migrations.RunPython.noop),
    ]
