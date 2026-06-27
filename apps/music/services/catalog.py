from music.models import Track, TrackTag
from music.seed_data import POPULAR_CHRISTIAN_TRACKS_BY_GENRE

PLAN_TRACK_PACKS = {
    "trial": {
        "genres": ["nigerian_worship", "global_worship"],
        "limit": 100,
    },
    "saas": {
        "genres": [
            "nigerian_worship",
            "nigerian_praise",
            "african_gospel",
            "global_worship",
            "global_praise",
        ],
        "limit": 300,
    },
    "white_label": {
        "genres": [
            "nigerian_worship",
            "nigerian_praise",
            "african_gospel",
            "global_worship",
            "global_praise",
        ],
        "limit": 500,
    },
    "founders_saas_lifetime": {
        "genres": [
            "nigerian_worship",
            "nigerian_praise",
            "african_gospel",
            "global_worship",
            "global_praise",
        ],
        "limit": 500,
    },
    "founders_white_label_lifetime": {
        "genres": [
            "nigerian_worship",
            "nigerian_praise",
            "african_gospel",
            "global_worship",
            "global_praise",
        ],
        "limit": 500,
    },
}


def _genre_label(genre_key: str) -> str:
    return (genre_key or "").replace("_", " ").title()


def catalog_genres():
    return sorted(POPULAR_CHRISTIAN_TRACKS_BY_GENRE.keys())


def seed_tracks_for_church(church, *, unit=None, genres=None, limit=None):
    """
    Seed curated catalog tracks for one church.
    Returns dict with counts.
    """
    genres = genres or catalog_genres()
    created_count = 0
    linked_count = 0

    for genre in genres:
        entries = POPULAR_CHRISTIAN_TRACKS_BY_GENRE.get(genre, [])
        if not entries:
            continue

        tag, _ = TrackTag.raw_objects.get_or_create(
            church=church,
            name=_genre_label(genre),
            defaults={"color": "#22c55e", "is_active": True},
        )

        for title, artist in entries:
            if limit is not None and created_count >= int(limit):
                return {"created": created_count, "linked": linked_count}

            track, created = Track.raw_objects.get_or_create(
                church=church,
                title=title,
                artist=artist,
                defaults={"is_active": True, "unit": unit},
            )
            if created:
                created_count += 1

            if unit and not track.unit:
                track.unit = unit
                track.save(update_fields=["unit"])

            track.tags.add(tag)
            linked_count += 1

    return {"created": created_count, "linked": linked_count}


def pack_for_plan(plan_name: str):
    return PLAN_TRACK_PACKS.get(plan_name or "", PLAN_TRACK_PACKS["trial"])


def seed_tracks_for_plan(church, plan_name: str, *, unit=None):
    pack = pack_for_plan(plan_name)
    return seed_tracks_for_church(
        church,
        unit=unit,
        genres=pack["genres"],
        limit=pack["limit"],
    )
