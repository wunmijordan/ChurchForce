"""
bible/migrations/0002_seed_defaults.py

Data migration — runs automatically on every `python manage.py migrate`,
in dev and prod, with zero manual steps.

Does three things, all best-effort (network failures never block migrate):
  1. Seeds the 8 default BibleBadge rows.
  2. Fetches the BSB translation metadata + books list from HelloAO (2 requests).
  3. Pre-warms 10 high-traffic chapters so keyword search works on day one.
     Capped at 10 (not 30+) to keep migrate fast; the rest warm lazily.

No management command needed — the service layer (helloao.py, badges.py)
handles everything; this migration just calls it at the right moment.
"""

from django.db import migrations


def seed_bible_defaults(apps, schema_editor):
    import logging
    import time
    logger = logging.getLogger(__name__)

    # ── 1. Badges ──────────────────────────────────────────────────────────
    try:
        from bible.models import BibleBadge
        BibleBadge.ensure_defaults()
        logger.info("bible: default badges seeded")
    except Exception as exc:
        logger.warning("bible: badge seeding failed (non-fatal): %s", exc)

    # ── 2. BSB translation metadata + books list ───────────────────────────
    try:
        from bible.services.helloao import get_translations, get_books
        get_translations(force_refresh=True)
        get_books("BSB")
        logger.info("bible: BSB translation and books cached")
    except Exception as exc:
        logger.info("bible: HelloAO translation seed skipped (no network?): %s", exc)

    # ── 3. Pre-warm 10 common chapters for keyword search ──────────────────
    SEED_CHAPTERS = [
        ("JHN", 3), ("ROM", 8), ("PSA", 23), ("PHP", 4),
        ("MAT", 5), ("EPH", 2), ("ISA", 40), ("GEN", 1),
        ("1CO", 13), ("HEB", 11),
    ]
    try:
        from bible.services.helloao import get_chapter
        for book_id, chapter in SEED_CHAPTERS:
            try:
                get_chapter(book_id, chapter, "BSB")
                time.sleep(0.2)  # polite to the API
            except Exception:
                pass             # one chapter failing never blocks the rest
        logger.info("bible: common chapters pre-warmed")
    except Exception as exc:
        logger.info("bible: chapter warm-up skipped: %s", exc)


def unseed_bible_defaults(apps, schema_editor):
    """Reverse: remove seeded badges. Cache rows are intentionally left."""
    try:
        BibleBadge = apps.get_model("bible", "BibleBadge")
        BibleBadge.objects.filter(slug__in=[
            "first_reading", "streak_7", "streak_30", "plan_finisher",
            "memory_master", "study_complete", "verse_sharer", "streak_100",
        ]).delete()
    except Exception:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            seed_bible_defaults,
            reverse_code=unseed_bible_defaults,
        ),
    ]
