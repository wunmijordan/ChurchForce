"""
music/models.py

Unit-based worship music management.

Designed for a church's music/worship unit. Models are church-owned
(multi-tenant) and optionally unit-scoped. A church with multiple
music units (e.g. main band, youth band) can have separate tracks,
setlists, and rehearsals per unit.

Model hierarchy:
    Track           — a song in the church's library
        ChordChart  — one or more key/instrument arrangements per track
    Setlist         — a list of songs planned for a specific Event
        SetlistSong — ordered song in a setlist with performance notes
    RehearsalSession— a scheduled rehearsal linked to an Event
"""

from django.db import models
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel, OrderedChurchModel


MUSICAL_KEYS = [
    ("C",  "C"),  ("Cm",  "C minor"),
    ("C#", "C#"), ("C#m", "C# minor"),
    ("Db", "Db"),
    ("D",  "D"),  ("Dm",  "D minor"),
    ("Eb", "Eb"), ("Ebm", "Eb minor"),
    ("E",  "E"),  ("Em",  "E minor"),
    ("F",  "F"),  ("Fm",  "F minor"),
    ("F#", "F#"), ("F#m", "F# minor"),
    ("Gb", "Gb"),
    ("G",  "G"),  ("Gm",  "G minor"),
    ("Ab", "Ab"), ("Abm", "Ab minor"),
    ("A",  "A"),  ("Am",  "A minor"),
    ("Bb", "Bb"), ("Bbm", "Bb minor"),
    ("B",  "B"),  ("Bm",  "B minor"),
]


class Track(ChurchOwnedModel):
    """
    A song in the church's worship library.

    Can be shared across all units in the church or restricted to
    a specific unit (e.g. a youth-band-only arrangement).
    """

    title      = models.CharField(max_length=255, db_index=True)
    artist     = models.CharField(max_length=255, blank=True)
    lyrics     = models.TextField(blank=True)
    original_key = models.CharField(
        max_length=5, choices=MUSICAL_KEYS, blank=True,
        help_text="Original recorded key of the song.",
    )
    tempo      = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Beats per minute (BPM).",
    )

    # External IDs — populated by API integrations
    spotify_id = models.CharField(max_length=100, blank=True)
    ccli_id    = models.CharField(
        max_length=50, blank=True,
        help_text="CCLI song number for licensing reference.",
    )

    # Optional audio reference uploaded via Cloudinary
    audio_file = CloudinaryField(
        "audio", resource_type="video", blank=True, null=True,
        help_text="Audio reference file (MP3/WAV).",
    )

    # Optional unit scope — null means available to all units
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="tracks",
        help_text="Leave blank to make available to all units.",
    )
    added_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="added_tracks",
    )

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["church", "title"]),
            models.Index(fields=["church", "unit"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "title", "artist"],
                name="unique_track_per_church",
            )
        ]

    def __str__(self):
        return f"{self.title}" + (f" — {self.artist}" if self.artist else "")


class ChordChart(ChurchOwnedModel):
    """
    A key/instrument-specific arrangement of a Track.

    A single track can have multiple chord charts:
        - E major (band default)
        - D major (for a different vocalist range)
        - Acoustic version
        - etc.

    flat_score_id: If the church uses Flat.io for notation, store
    the score ID here so members can view/edit it directly.
    """

    track    = models.ForeignKey(
        Track, on_delete=models.CASCADE, related_name="chord_charts"
    )
    key      = models.CharField(max_length=5, choices=MUSICAL_KEYS)
    label    = models.CharField(
        max_length=100, blank=True,
        help_text="Optional label, e.g. 'Acoustic', 'Band default', 'Capo 2'.",
    )
    content  = models.TextField(
        blank=True,
        help_text="Chord chart text (Nashville number, chord names, or tab).",
    )
    flat_score_id = models.CharField(
        max_length=100, blank=True,
        help_text="Flat.io score ID for interactive notation.",
    )
    file     = CloudinaryField(
        "chord_chart", resource_type="raw", blank=True, null=True,
        help_text="PDF or image chord chart upload.",
    )
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="chord_charts",
    )

    class Meta:
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(
                fields=["track", "key", "label"],
                name="unique_chart_per_track_key_label",
            )
        ]

    def __str__(self):
        label = f" ({self.label})" if self.label else ""
        return f"{self.track.title} — {self.key}{label}"


class Setlist(ChurchOwnedModel):
    """
    A worship setlist for a specific Event.

    Linked to services.Event rather than the old ServiceEvent model.
    One event can have one setlist. Setlists belong to the church
    and optionally to a specific unit (band).
    """

    title     = models.CharField(max_length=255)
    event     = models.OneToOneField(
        "services.Event",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="setlist",
        help_text="The service or event this setlist is prepared for.",
    )
    unit      = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="setlists",
        help_text="The music/worship unit responsible for this setlist.",
    )
    finalized = models.BooleanField(
        default=False,
        help_text="Mark true when the setlist is approved and locked.",
    )
    notes     = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_setlists",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "event"]),
        ]

    def __str__(self):
        event_str = str(self.event) if self.event else "No event"
        return f"{self.title} ({event_str})"


class SetlistSong(OrderedChurchModel):
    """
    An ordered entry in a Setlist, with performance notes.

    Stores the chosen key (may differ from the track's original_key)
    and any service-specific notes (e.g. "extend bridge", "key change after verse 2").
    """

    setlist       = models.ForeignKey(
        Setlist, on_delete=models.CASCADE, related_name="songs"
    )
    track         = models.ForeignKey(
        Track, on_delete=models.PROTECT, related_name="setlist_appearances"
    )
    performance_key = models.CharField(
        max_length=5, choices=MUSICAL_KEYS, blank=True,
        help_text="Key for this performance (may differ from track's original key).",
    )
    notes         = models.TextField(
        blank=True,
        help_text="Service-specific notes: cues, key changes, duration, etc.",
    )

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["setlist", "track"],
                name="unique_song_per_setlist",
            )
        ]

    def __str__(self):
        return f"{self.order}. {self.track.title} ({self.setlist.title})"


class RehearsalSession(ChurchOwnedModel):
    """
    A scheduled rehearsal session for a music unit.

    Linked to an Event (the rehearsal itself — event_type="Rehearsal")
    and optionally to a Setlist to track which songs were practised.
    """

    event    = models.ForeignKey(
        "services.Event",
        on_delete=models.CASCADE,
        related_name="rehearsal_sessions",
    )
    setlist  = models.ForeignKey(
        Setlist,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="rehearsals",
        help_text="The setlist being rehearsed.",
    )
    unit     = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="rehearsals",
    )
    notes    = models.TextField(blank=True)
    completed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Rehearsal: {self.event} ({self.unit or 'No unit'})"