"""
Migration 0013: Add _callback_uri field to YouVersionOAuthToken.

This field was added to migration 0011 after that migration had already been
applied to the live database, so the column never got created.  This migration
adds it safely with a default of "" so existing rows are left valid.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0012_alter_youversionoauthtoken_created_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="youversionoauthtoken",
            name="_callback_uri",
            field=models.CharField(
                blank=True,
                default="",
                help_text="redirect_uri used during this auth dance",
                max_length=500,
            ),
        ),
    ]