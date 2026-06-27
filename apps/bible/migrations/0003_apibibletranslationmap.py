from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0002_seed_defaults"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiBibleTranslationMap",
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
                ("short_code", models.CharField(max_length=20, unique=True)),
                ("bible_id", models.CharField(max_length=60)),
                ("name", models.CharField(blank=True, max_length=200)),
            ],
            options={
                "verbose_name": "API.Bible Translation Map",
                "verbose_name_plural": "API.Bible Translation Maps",
                "ordering": ["short_code"],
            },
        ),
    ]
