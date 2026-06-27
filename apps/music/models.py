"""
music/models.py — ChurchForce Music Module

Multi-track worship music management: song library, chord charts,
lyrics sections, setlists, rehearsal sessions, mix notes, and
attendance per rehearsal.

Model hierarchy:
    Track               — song in the church library
        LyricsSection   — structured verse/chorus/bridge sections
        ChordChart      — key+instrument arrangements
        TrackTag        — genre/mood/theme tags
    Setlist             — ordered song plan for a service Event
        SetlistSong     — ordered entry with performance key + notes
    RehearsalSession    — scheduled rehearsal linked to an Event
    MixNote             — sound-engineer mix notes per song per event
"""

import uuid
from django.db import models
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel, OrderedChurchModel

MUSICAL_KEYS = [
    ("C", "C"),
    ("Cm", "C minor"),
    ("C#", "C#"),
    ("C#m", "C# minor"),
    ("Db", "Db"),
    ("D", "D"),
    ("Dm", "D minor"),
    ("Eb", "Eb"),
    ("Ebm", "Eb minor"),
    ("E", "E"),
    ("Em", "E minor"),
    ("F", "F"),
    ("Fm", "F minor"),
    ("F#", "F#"),
    ("F#m", "F# minor"),
    ("Gb", "Gb"),
    ("G", "G"),
    ("Gm", "G minor"),
    ("Ab", "Ab"),
    ("Abm", "Ab minor"),
    ("A", "A"),
    ("Am", "A minor"),
    ("Bb", "Bb"),
    ("Bbm", "Bb minor"),
    ("B", "B"),
    ("Bm", "B minor"),
]

TIME_SIGNATURES = [
    ("4/4", "4/4"),
    ("3/4", "3/4"),
    ("6/8", "6/8"),
    ("2/4", "2/4"),
    ("12/8", "12/8"),
    ("5/4", "5/4"),
]


# ── Track ─────────────────────────────────────────────────────────────────────


class TrackTag(ChurchOwnedModel):
    """Genre, mood, or theme tag for filtering the song library."""

    name = models.CharField(max_length=60)
    color = models.CharField(
        max_length=7, default="#6366f1", help_text="Hex color for UI display."
    )

    class Meta:
        unique_together = ("church", "name")

    def __str__(self):
        return self.name


class Track(ChurchOwnedModel):
    """
    A song in the church's worship library.

    Central object — chord charts, lyrics sections, and mix notes all
    hang off this. Can be scoped to a specific unit (band) or shared
    church-wide (unit=None).
    """

    uid = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True
    )
    title = models.CharField(max_length=255, db_index=True)
    artist = models.CharField(max_length=255, blank=True)
    original_key = models.CharField(max_length=5, choices=MUSICAL_KEYS, blank=True)
    time_signature = models.CharField(
        max_length=5, choices=TIME_SIGNATURES, default="", blank=True
    )
    tempo = models.PositiveIntegerField(null=True, blank=True, help_text="BPM.")
    duration_seconds = models.PositiveIntegerField(
        null=True, blank=True, help_text="Track duration in seconds."
    )

    # Lyrics stored as plain text (structured via LyricsSection)
    lyrics = models.TextField(
        blank=True, help_text="Full lyrics text. Sections are structured separately."
    )
    lyrics_auto = models.BooleanField(
        default=False, help_text="True if lyrics were auto-transcribed by Whisper."
    )

    # External references
    spotify_id = models.CharField(max_length=100, blank=True)
    ccli_id = models.CharField(
        max_length=50, blank=True, help_text="CCLI song number for licensing."
    )
    youtube_url = models.URLField(blank=True, help_text="Reference YouTube video.")
    apple_music_url = models.URLField(blank=True)

    # Audio reference
    audio_file = CloudinaryField(
        "audio",
        resource_type="video",
        blank=True,
        null=True,
        help_text="Reference audio (MP3/WAV).",
    )
    audio_transcribed = models.BooleanField(default=False)

    # Scope
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tracks",
        help_text="Null = church-wide.",
    )
    tags = models.ManyToManyField(TrackTag, blank=True, related_name="tracks")
    added_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="added_tracks",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["church", "title"]),
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "title", "artist"], name="unique_track_per_church"
            )
        ]

    def __str__(self):
        return self.title + (f" — {self.artist}" if self.artist else "")

    @property
    def duration_display(self):
        if not self.duration_seconds:
            return ""
        m, s = divmod(self.duration_seconds, 60)
        return f"{m}:{s:02d}"

    @property
    def playback_url(self):
        """
        Best available playback URL, in priority order:
          1. Church-uploaded audio_file (dev → mediafiles/, prod → Cloudinary)
          2. SoundCloud direct stream URL
          3. Spotify 30-second preview MP3
          4. Empty string (no audio available)
        """
        from core.storage import smart_audio_url

        # 1. Church upload
        url = smart_audio_url(self.audio_file)
        if url:
            return url
        # 2. SoundCloud stream
        if getattr(self, "soundcloud_stream_url", ""):
            return self.soundcloud_stream_url
        # 3. Spotify preview
        if getattr(self, "spotify_preview_url", ""):
            return self.spotify_preview_url
        return ""

    @property
    def playback_label(self):
        """Short label for UI: 'upload', 'soundcloud', 'preview', 'youtube', 'none'."""
        from core.storage import field_has_value

        if field_has_value(self.audio_file):
            return "upload"
        if getattr(self, "soundcloud_stream_url", ""):
            return "soundcloud"
        if getattr(self, "spotify_preview_url", ""):
            return "preview"
        if self.youtube_url:
            return "youtube"
        return "none"


class LyricsSection(OrderedChurchModel):
    """
    A structured section of a track's lyrics: Verse 1, Chorus, Bridge, etc.

    Sections are ordered and rendered in sequence for projection.
    Each section can be individually projected in the media module.
    """

    SECTION_TYPES = [
        ("intro", "Intro"),
        ("verse", "Verse"),
        ("pre_chorus", "Pre-Chorus"),
        ("chorus", "Chorus"),
        ("bridge", "Bridge"),
        ("outro", "Outro"),
        ("tag", "Tag"),
        ("interlude", "Interlude"),
        ("spoken", "Spoken Word"),
    ]

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="sections")
    section_type = models.CharField(
        max_length=20, choices=SECTION_TYPES, default="verse"
    )
    label = models.CharField(
        max_length=60,
        blank=True,
        help_text="e.g. 'Verse 1', 'Chorus 2'. Auto-generated if blank.",
    )
    content = models.TextField(help_text="Lyrics for this section.")
    repeat_count = models.PositiveSmallIntegerField(
        default=1, help_text="How many times this section repeats."
    )

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.label or self.section_type} — {self.track.title}"

    def save(self, *args, **kwargs):
        if not self.label:
            # Auto-label: count existing sections of same type
            existing = (
                LyricsSection.objects.filter(
                    track=self.track, section_type=self.section_type
                )
                .exclude(pk=self.pk)
                .count()
            )
            type_label = self.get_section_type_display()
            self.label = f"{type_label} {existing + 1}" if existing else type_label
        super().save(*args, **kwargs)


class ChordChart(ChurchOwnedModel):
    """
    A key/instrument-specific arrangement of a Track.

    Multiple charts per track: E major (band), D major (vocalist), Acoustic, etc.
    """

    track = models.ForeignKey(
        Track, on_delete=models.CASCADE, related_name="chord_charts"
    )
    key = models.CharField(max_length=5, choices=MUSICAL_KEYS)
    label = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g. 'Acoustic', 'Capo 2', 'Band default'.",
    )
    content = models.TextField(
        blank=True, help_text="Chord chart text (Nashville number, chord names, tab)."
    )
    flat_score_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="Flat.io score ID for interactive notation.",
    )
    file = CloudinaryField(
        "chord_chart",
        resource_type="raw",
        blank=True,
        null=True,
        help_text="PDF or image upload.",
    )
    is_default = models.BooleanField(
        default=False, help_text="Default chart shown when opening the track."
    )
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chord_charts",
    )

    class Meta:
        ordering = ["-is_default", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["track", "key", "label"],
                name="unique_chart_per_track_key_label",
            )
        ]

    def __str__(self):
        label = f" ({self.label})" if self.label else ""
        return f"{self.track.title} — {self.key}{label}"


# ── Setlist ───────────────────────────────────────────────────────────────────


class Setlist(ChurchOwnedModel):
    """
    Worship setlist for a specific Event.

    When finalized=True, the media module can auto-generate lyric slides
    from this setlist's songs and their LyricsSection records.
    """

    uid = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True
    )
    title = models.CharField(max_length=255)
    event = models.OneToOneField(
        "services.Event",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="setlist",
        help_text="Service this setlist is prepared for.",
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="setlists",
    )
    finalized = models.BooleanField(
        default=False, help_text="Lock and trigger media sync when True."
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
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
        return f"{self.title}" + (f" ({self.event})" if self.event else "")

    def sync_to_media(self):
        """
        Auto-generate/update a ServicePresentation in the media module
        from this setlist's songs and their lyrics sections.
        Called when finalized is set to True.
        """
        from media.models import ServicePresentation, PresentationSlide
        from media.services import sync_setlist_to_presentation

        sync_setlist_to_presentation(self)


class SetlistSong(OrderedChurchModel):
    """Ordered song entry in a Setlist with per-performance notes and key."""

    setlist = models.ForeignKey(Setlist, on_delete=models.CASCADE, related_name="songs")
    track = models.ForeignKey(
        Track, on_delete=models.PROTECT, related_name="setlist_appearances"
    )
    performance_key = models.CharField(
        max_length=5,
        choices=MUSICAL_KEYS,
        blank=True,
        help_text="Key for this performance (may differ from original).",
    )
    notes = models.TextField(blank=True, help_text="Cues, key changes, duration notes.")
    # Projection: which sections to include (null = all)
    sections_to_project = models.JSONField(
        null=True,
        blank=True,
        help_text="Ordered list of LyricsSection PKs to project. "
        "Null = all sections in order.",
    )

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["setlist", "track"], name="unique_song_per_setlist"
            )
        ]

    def __str__(self):
        return f"{self.order}. {self.track.title}"


# ── Rehearsal ─────────────────────────────────────────────────────────────────


class RehearsalSession(ChurchOwnedModel):
    """Scheduled rehearsal session linked to an Event and optionally a Setlist."""

    uid = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True
    )
    event = models.ForeignKey(
        "services.Event", on_delete=models.CASCADE, related_name="rehearsal_sessions"
    )
    setlist = models.ForeignKey(
        Setlist,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rehearsals",
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rehearsals",
    )
    notes = models.TextField(blank=True)
    completed = models.BooleanField(default=False)
    recording_url = models.URLField(
        blank=True, help_text="Link to rehearsal recording."
    )
    recording_file = CloudinaryField(
        "audio",
        resource_type="video",
        blank=True,
        null=True,
        help_text="Uploaded rehearsal recording shared with unit members.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = self.event.prepares_for_service if self.event_id else None
        if target:
            return f"Rehearsal for {target.name} — {self.event}"
        return f"Rehearsal: {self.event}"

    @property
    def recording_file_url(self):
        """URL for the rehearsal recording (dev → /media/…, prod → Cloudinary)."""
        from core.storage import smart_audio_url

        return smart_audio_url(self.recording_file)


# ── Mix Notes ─────────────────────────────────────────────────────────────────


class MixNote(ChurchOwnedModel):
    """
    Sound engineer mix notes for a specific song in a specific event.

    Tracks channel levels, EQ settings, effects, and general notes
    so the engineer can replicate the mix at the next service.
    """

    setlist_song = models.ForeignKey(
        SetlistSong, on_delete=models.CASCADE, related_name="mix_notes"
    )
    engineer = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mix_notes",
    )
    # Channel settings (stored as JSON for flexibility)
    channel_settings = models.JSONField(
        default=dict, blank=True, help_text="Per-instrument/channel level and EQ notes."
    )
    fx_notes = models.TextField(blank=True, help_text="Effects and processing notes.")
    general_note = models.TextField(blank=True)
    bpm_used = models.PositiveIntegerField(
        null=True, blank=True, help_text="Actual BPM used in this performance."
    )
    key_used = models.CharField(max_length=5, choices=MUSICAL_KEYS, blank=True)

    class Meta:
        unique_together = ("setlist_song", "engineer")

    def __str__(self):
        return f"Mix note: {self.setlist_song}"


class SongStem(OrderedChurchModel):
    """
    Individual multitrack stem asset for a song (e.g. Vox, Drums, Bass).

    Provides per-stem defaults used by the mixer UI for quick custom mixes.
    """

    STEM_FAMILIES = [
        ("vox", "Vocals"),
        ("drums", "Drums"),
        ("bass", "Bass"),
        ("keys", "Keys"),
        ("guitar", "Guitar"),
        ("fx", "FX"),
        ("click", "Click"),
        ("other", "Other"),
    ]

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="stems")
    name = models.CharField(max_length=80)
    family = models.CharField(max_length=20, choices=STEM_FAMILIES, default="other")
    audio_file = CloudinaryField(
        "audio",
        resource_type="video",
        blank=True,
        null=True,
        help_text="Stem audio file (wav/mp3/aac).",
    )
    default_gain = models.FloatField(default=0.8, help_text="0.0 - 1.0")
    default_pan = models.FloatField(default=0.0, help_text="-1.0 (L) to 1.0 (R)")
    default_muted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["track", "name"], name="unique_stem_name_per_track"
            )
        ]

    def __str__(self):
        return f"{self.track.title} — {self.name}"

    @property
    def playback_url(self):
        """
        Return a usable playback URL for the stem audio_file, or empty string.
        Routes to mediafiles/ in dev, Cloudinary in prod.
        """
        from core.storage import smart_audio_url

        return smart_audio_url(self.audio_file)


class SongMixPreset(ChurchOwnedModel):
    """
    Saved custom mix state for a user+track.
    Stores gain/pan/mute plus strip order and transpose.
    """

    track = models.ForeignKey(
        Track, on_delete=models.CASCADE, related_name="mix_presets"
    )
    member = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="song_mix_presets",
    )
    name = models.CharField(max_length=80, default="My Mix")
    transpose_semitones = models.SmallIntegerField(default=0)
    channel_state = models.JSONField(default=dict, blank=True)
    strip_order = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["church", "track", "member", "name"],
                name="unique_song_mix_preset_per_member",
            )
        ]
        ordering = ["-updated_at"]

    def __str__(self):
        base = (
            self.member.user.full_name
            if self.member and getattr(self.member, "user", None)
            else "Member"
        )
        return f"{self.track.title} — {base} ({self.name})"


# ── SetlistSong Musician Assignment ──────────────────────────────────────────


class SetlistSongAssignment(ChurchOwnedModel):
    """
    Who plays what on a specific SetlistSong.

    Inspired by Planning Center's "Who's Playing What" feature:
    each musician is assigned an instrument for a specific song in the setlist,
    letting leaders see at a glance who covers every part and letting musicians
    see only their own songs with their key context.
    """

    INSTRUMENT_CHOICES = [
        ("vocals_lead", "Lead Vocals"),
        ("vocals_bg", "Background Vocals"),
        ("piano", "Piano / Keys"),
        ("electric_guitar", "Electric Guitar"),
        ("acoustic_guitar", "Acoustic Guitar"),
        ("bass_guitar", "Bass Guitar"),
        ("drums", "Drums / Percussion"),
        ("violin", "Violin"),
        ("trumpet", "Trumpet"),
        ("saxophone", "Saxophone"),
        ("flute", "Flute"),
        ("cello", "Cello"),
        ("sound", "Sound Engineer"),
        ("slides", "Slides Operator"),
        ("other", "Other"),
    ]

    setlist_song = models.ForeignKey(
        SetlistSong,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="setlist_assignments",
    )
    instrument = models.CharField(max_length=30, choices=INSTRUMENT_CHOICES, default="other")
    notes = models.TextField(blank=True, help_text="Specific notes for this musician on this song.")
    confirmed = models.BooleanField(default=False, help_text="Has the musician confirmed?")

    class Meta:
        unique_together = ("setlist_song", "member", "instrument")
        ordering = ["instrument", "member__user__full_name"]

    def __str__(self):
        name = self.member.user.full_name if self.member and self.member.user else "?"
        return f"{self.setlist_song.track.title} — {name} ({self.get_instrument_display()})"


# ── Song Resource ─────────────────────────────────────────────────────────────


class SongResource(ChurchOwnedModel):
    """
    Attachable resource for a track: chord chart PDF, sheet music,
    reference recording, click loop, or other file.

    Mirrors Planning Center's per-song resource attachments so musicians
    get everything they need in one place without hunting through emails.
    """

    RESOURCE_TYPE_CHOICES = [
        ("chord_chart", "Chord Chart"),
        ("sheet_music", "Sheet Music"),
        ("stem", "Stem / Track"),
        ("loop", "Loop"),
        ("reference", "Reference Recording"),
        ("lyrics", "Lyrics Sheet"),
        ("other", "Other"),
    ]

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="resources")
    name = models.CharField(max_length=120)
    resource_type = models.CharField(max_length=20, choices=RESOURCE_TYPE_CHOICES, default="other")
    file = CloudinaryField(
        "resource",
        resource_type="raw",
        blank=True,
        null=True,
    )
    external_url = models.URLField(blank=True, help_text="External link (Google Drive, Dropbox, etc.)")
    uploaded_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_resources",
    )

    class Meta:
        ordering = ["resource_type", "name"]

    def __str__(self):
        return f"{self.track.title} — {self.name} ({self.get_resource_type_display()})"

    @property
    def download_url(self):
        if self.file:
            try:
                return self.file.url
            except Exception:
                pass
        return self.external_url or ""
