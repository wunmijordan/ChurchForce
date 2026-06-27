"""
0004_clear_apibible_cache

Clears any stale ApiBibleTranslationMap rows that were fetched with the
wrong base URL (api.scripture.api.bible instead of rest.api.bible).
The BibleConfig.ready() will re-populate on next server start.
"""
from django.db import migrations


def clear_apibible_cache(apps, schema_editor):
    ApiBibleTranslationMap = apps.get_model("bible", "ApiBibleTranslationMap")
    deleted, _ = ApiBibleTranslationMap.objects.all().delete()
    if deleted:
        print(f"\n  Cleared {deleted} stale API.Bible translation cache rows — will re-sync on next start.")


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0003_apibibletranslationmap"),
    ]

    operations = [
        migrations.RunPython(clear_apibible_cache, migrations.RunPython.noop),
    ]