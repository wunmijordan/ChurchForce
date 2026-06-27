"""
Migration: LMS module template system + reading plan content type
- LMSModule: add template_type, reading_plan FK
- LMSModule: update CONTENT_TYPE_CHOICES to include reading_plan (no db change — just CharField)
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("lms", "0004_lmsmodule_template_data"),
        ("bible", "0005_bible_reader_discussion"),
    ]

    operations = [
        migrations.AddField(
            model_name="lmsmodule",
            name="template_type",
            field=models.CharField(
                choices=[
                    ("none", "No Template (free-form)"),
                    ("lesson", "Lesson Page (Notion-style)"),
                    ("case_study", "Case Study"),
                    ("reflection", "Guided Reflection"),
                    ("bible_devotional", "Bible Devotional"),
                    ("reading_plan_embed", "Reading Plan Embed"),
                    ("discussion_prompt", "Discussion Prompt"),
                    ("checklist", "Action Checklist"),
                    ("quiz_bank", "Quiz / Question Bank"),
                ],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="lmsmodule",
            name="reading_plan",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="lms_modules",
                to="bible.readingplan",
            ),
        ),
    ]
