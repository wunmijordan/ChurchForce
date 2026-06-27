"""
music/management/commands/enrich_catalog_tracks.py

Management command: enrich catalog track records with audio from
SoundCloud, Spotify, and/or YouTube.

Usage examples
--------------
# Enrich all unenriched tracks for all churches (all sources):
python manage.py enrich_catalog_tracks

# Enrich only SoundCloud + YouTube, skip Spotify:
python manage.py enrich_catalog_tracks --no-spotify

# Re-enrich all tracks (even already-enriched ones):
python manage.py enrich_catalog_tracks --force

# Limit to 50 tracks per church (useful for incremental daily runs):
python manage.py enrich_catalog_tracks --limit 50

# Enrich only one church:
python manage.py enrich_catalog_tracks --church-id 3

# Slower requests to be extra polite to APIs:
python manage.py enrich_catalog_tracks --delay 1.0

# Show per-track verbose output:
python manage.py enrich_catalog_tracks --verbosity 2
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Enrich catalog Track records with audio from SoundCloud, "
        "Spotify (30s preview), and YouTube (video ID)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--church-id",
            type=int,
            default=None,
            help="Enrich only one church (by DB id). Default: all active churches.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Maximum tracks to process per church. "
                "Useful for incremental daily runs to stay within API quotas."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help=(
                "Re-enrich tracks that are already enriched. "
                "Use this to update stale SoundCloud stream URLs or "
                "to pick up new Spotify previews."
            ),
        )
        parser.add_argument(
            "--no-soundcloud",
            action="store_true",
            default=False,
            help="Skip SoundCloud enrichment.",
        )
        parser.add_argument(
            "--no-spotify",
            action="store_true",
            default=False,
            help="Skip Spotify preview enrichment.",
        )
        parser.add_argument(
            "--no-youtube",
            action="store_true",
            default=False,
            help="Skip YouTube video ID enrichment.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.3,
            help=(
                "Seconds to wait between API calls per track. "
                "Default 0.3 s. Increase to 1.0+ for polite bulk runs."
            ),
        )

    def handle(self, *args, **options):
        from tenants.models import Church
        from music.services.enrichment import enrich_tracks_for_church

        church_id = options["church_id"]
        limit = options["limit"]
        force = options["force"]
        try_soundcloud = not options["no_soundcloud"]
        try_spotify = not options["no_spotify"]
        try_youtube = not options["no_youtube"]
        delay = options["delay"]
        verbosity = options["verbosity"]

        if not try_soundcloud and not try_spotify and not try_youtube:
            self.stderr.write(
                self.style.ERROR(
                    "All sources disabled — nothing to do. "
                    "Remove at least one --no-* flag."
                )
            )
            return

        # Summarise which sources are active
        active = [
            s
            for s, enabled in [
                ("SoundCloud", try_soundcloud),
                ("Spotify", try_spotify),
                ("YouTube", try_youtube),
            ]
            if enabled
        ]
        self.stdout.write(
            f"Sources: {', '.join(active)}  |  "
            f"delay={delay}s  |  limit={limit or 'none'}  |  force={force}"
        )

        churches = Church.raw_objects.filter(is_active=True)
        if church_id:
            churches = churches.filter(id=church_id)
        if not churches.exists():
            self.stderr.write(self.style.WARNING("No matching churches found."))
            return

        grand_total = {
            "processed": 0,
            "enriched": 0,
            "skipped": 0,
            "errors": 0,
            "soundcloud": 0,
            "spotify": 0,
            "youtube": 0,
        }

        def _log(msg: str):
            if verbosity >= 1:
                self.stdout.write(msg)

        for church in churches:
            self.stdout.write(
                self.style.HTTP_INFO(f"\n── {church.name} ─────────────────────")
            )
            try:
                stats = enrich_tracks_for_church(
                    church,
                    try_soundcloud=try_soundcloud,
                    try_spotify=try_spotify,
                    try_youtube=try_youtube,
                    force=force,
                    limit=limit,
                    rate_limit_delay=delay,
                    log_fn=_log if verbosity >= 2 else None,
                )
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  Failed for {church.name}: {exc}")
                )
                continue

            self.stdout.write(
                f"  processed={stats['processed']}  "
                f"enriched={stats['enriched']}  "
                f"sc={stats['soundcloud']}  "
                f"sp={stats['spotify']}  "
                f"yt={stats['youtube']}  "
                f"skipped={stats['skipped']}  "
                f"errors={stats['errors']}"
            )
            for k in grand_total:
                grand_total[k] += stats.get(k, 0)

        self.stdout.write("\n" + "─" * 50)
        self.stdout.write(
            self.style.SUCCESS(
                f"Grand total:  "
                f"processed={grand_total['processed']}  "
                f"enriched={grand_total['enriched']}  "
                f"SoundCloud={grand_total['soundcloud']}  "
                f"Spotify={grand_total['spotify']}  "
                f"YouTube={grand_total['youtube']}  "
                f"skipped={grand_total['skipped']}  "
                f"errors={grand_total['errors']}"
            )
        )
