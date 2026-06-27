from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0009_readingplanentry_translation"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="YouVersionTranslationMap",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("short_code", models.CharField(max_length=20, unique=True)),
                ("bible_id", models.PositiveIntegerField(db_index=True)),
                ("name", models.CharField(blank=True, max_length=200)),
                ("language", models.CharField(blank=True, default="en", max_length=20)),
                ("deep_link", models.URLField(blank=True)),
            ],
            options={
                "verbose_name": "YouVersion Translation Map",
                "verbose_name_plural": "YouVersion Translation Maps",
                "ordering": ["short_code"],
            },
        ),
        migrations.CreateModel(
            name="BibleProviderRequestLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True, db_index=True)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("youversion", "YouVersion"),
                            ("helloao", "HelloAO"),
                            ("apibible", "API.Bible"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                (
                    "operation",
                    models.CharField(
                        choices=[
                            ("translations", "Translations"),
                            ("books", "Books"),
                            ("chapter", "Chapter"),
                            ("search", "Search"),
                            ("passage", "Passage"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("translation", models.CharField(blank=True, db_index=True, max_length=20)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("status_code", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("ok", models.BooleanField(default=False, db_index=True)),
                ("fallback_used", models.BooleanField(default=False, db_index=True)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("rate_limit_limit", models.CharField(blank=True, max_length=40)),
                ("rate_limit_remaining", models.CharField(blank=True, max_length=40)),
                ("rate_limit_reset", models.CharField(blank=True, max_length=80)),
                ("error", models.CharField(blank=True, max_length=240)),
                (
                    "church",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)s_set",
                        to="tenants.church",
                    ),
                ),
            ],
            options={
                "verbose_name": "Bible Provider Request Log",
                "verbose_name_plural": "Bible Provider Request Logs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="bibleproviderrequestlog",
            index=models.Index(fields=["church", "provider", "-created_at"], name="bible_bible_church__9e3cba_idx"),
        ),
        migrations.AddIndex(
            model_name="bibleproviderrequestlog",
            index=models.Index(fields=["church", "operation", "-created_at"], name="bible_bible_church__371a28_idx"),
        ),
        migrations.AddIndex(
            model_name="bibleproviderrequestlog",
            index=models.Index(fields=["church", "ok", "-created_at"], name="bible_bible_church__b8297d_idx"),
        ),
    ]
