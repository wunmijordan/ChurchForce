"""
Migration: Add missing is_active field and composite indexes to BibleHighlight.

The BibleHighlight model inherits ChurchOwnedModel which adds is_active, but
migration 0005 created the table without it (it was hand-written rather than
auto-generated). This migration brings the DB schema in sync with the model.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0005_bible_reader_discussion"),
    ]

    operations = [
        migrations.AddField(
            model_name="biblehighlight",
            name="is_active",
            field=models.BooleanField(default=True, db_index=True),
        ),
        migrations.AddIndex(
            model_name="biblehighlight",
            index=models.Index(
                fields=["church", "created_at"],
                name="bible_hl_church_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="biblehighlight",
            index=models.Index(
                fields=["church", "is_active"],
                name="bible_hl_church_active_idx",
            ),
        ),
    ]
