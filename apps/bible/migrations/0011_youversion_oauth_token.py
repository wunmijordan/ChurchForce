"""
Migration 0011: Add YouVersionOAuthToken model.

Stores per-member YouVersion OAuth 2.0 tokens.
_callback_uri field stores the exact redirect_uri used during the auth dance
so exchange_code() can reproduce it precisely (required by OAuth spec).
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0010_youversion_provider_analytics"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="YouVersionOAuthToken",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "member",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="youversion_oauth",
                        to="accounts.churchmember",
                        verbose_name="Church Member",
                    ),
                ),
                ("access_token", models.TextField()),
                ("refresh_token", models.TextField(blank=True, default="")),
                ("expires_at", models.DateTimeField(null=True, blank=True)),
                (
                    "yv_user_id",
                    models.CharField(
                        max_length=64, blank=True, default="", db_index=True
                    ),
                ),
                (
                    "yv_username",
                    models.CharField(max_length=120, blank=True, default=""),
                ),
                ("yv_avatar_url", models.URLField(blank=True, default="")),
                (
                    "oauth_state",
                    models.CharField(max_length=128, blank=True, default=""),
                ),
                (
                    "code_verifier",
                    models.CharField(max_length=256, blank=True, default=""),
                ),
                # Stores the exact redirect_uri sent to YouVersion so exchange_code
                # can reproduce it exactly (OAuth spec requires it to match).
                (
                    "_callback_uri",
                    models.CharField(
                        max_length=500,
                        blank=True,
                        default="",
                        help_text="redirect_uri used during this auth dance",
                    ),
                ),
            ],
            options={
                "verbose_name": "YouVersion OAuth Token",
                "verbose_name_plural": "YouVersion OAuth Tokens",
                "ordering": ["-updated_at"],
            },
        ),
    ]
