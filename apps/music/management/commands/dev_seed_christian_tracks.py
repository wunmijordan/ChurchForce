from django.core.management.base import BaseCommand

from music.services.catalog import seed_tracks_for_church, catalog_genres


class Command(BaseCommand):
    help = "Seed popular Christian tracks by genre for development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--church-id",
            type=int,
            default=None,
            help="Optional church ID to seed only one church.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Optional max tracks to create per church.",
        )
        parser.add_argument(
            "--genre",
            action="append",
            default=[],
            help="Optional genre key (can be repeated).",
        )

    def handle(self, *args, **options):
        from tenants.models import Church

        church_id = options.get("church_id")
        limit = options.get("limit")
        genres = options.get("genre") or []
        churches = Church.raw_objects.filter(is_active=True)
        if church_id:
            churches = churches.filter(id=church_id)
        created_count = 0
        linked_count = 0

        for church in churches:
            selected_genres = [g for g in genres if g in catalog_genres()] if genres else None
            stats = seed_tracks_for_church(
                church,
                genres=selected_genres,
                limit=limit,
            )
            created_count += stats["created"]
            linked_count += stats["linked"]

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed completed: {created_count} new tracks, {linked_count} tag links."
            )
        )
