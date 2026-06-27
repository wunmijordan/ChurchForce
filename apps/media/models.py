"""
media/models.py — ChurchForce Media Module

EasyWorship-grade presentation management: service presentations,
lyric slides auto-synced from music setlists, Bible passages with
multiple versions, graphic templates, and broadcast config.

Model hierarchy:
    BibleVersion            — KJV, NIV, NLT, etc.
    BiblePassage            — cached Bible text for slide insertion
    SlideTemplate           — branded graphic/background templates
    ServicePresentation     — presentation plan for a specific Event
        PresentationSlide   — ordered slide within a presentation
    ProductionSchedule      — production readiness checklist per Event
        MediaAsset          — uploaded/linked files per schedule
    BroadcastConfig         — streaming settings per Event
"""

import uuid
from django.db import models
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel, OrderedChurchModel


# ── Bible ─────────────────────────────────────────────────────────────────────


class BibleVersion(models.Model):
    """Bible translation catalogue — shared across all tenants."""

    code = models.CharField(
        max_length=10,
        unique=True,
        help_text="Short code: KJV, NIV, NLT, ESV, NKJV, MSG, AMP, …",
    )
    name = models.CharField(max_length=100, help_text="Full name.")
    language = models.CharField(max_length=40, default="English")
    is_active = models.BooleanField(default=True)
    api_source = models.CharField(
        max_length=60, blank=True, help_text="Source API key or identifier."
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class BiblePassage(ChurchOwnedModel):
    """
    Cached Bible text fetched from an API (api.esv.org, scripture.api.bible, etc.)
    and saved per church so repeated fetches are free.

    reference: human-readable, e.g. "John 3:16", "Psalm 23:1-6"
    text:      plain text of the passage
    bible_slides:    pre-split list of lines for slide rendering (JSON)
    """

    version = models.ForeignKey(
        BibleVersion, on_delete=models.PROTECT, related_name="passages"
    )
    reference = models.CharField(
        max_length=100, db_index=True, help_text="e.g. John 3:16 or Psalm 23:1-6"
    )
    text = models.TextField()
    bible_slides = models.JSONField(
        default=list,
        blank=True,
        help_text="List of strings; each element = one slide's text.",
    )
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("church", "version", "reference")
        indexes = [models.Index(fields=["church", "version"])]

    def __str__(self):
        return f"{self.reference} ({self.version.code})"


# ── Slide Templates ───────────────────────────────────────────────────────────


class SlideTemplate(ChurchOwnedModel):
    """
    Branded graphic template used as background/theme for slides.

    Churches upload their own backgrounds or choose from system defaults.
    CSS/JSON config stores font, color, overlay opacity, and layout.
    """

    TEMPLATE_TYPES = [
        ("lyrics", "Lyrics"),
        ("scripture", "Scripture"),
        ("title", "Title/Announcement"),
        ("blank", "Blank"),
        ("custom", "Custom"),
    ]

    name = models.CharField(max_length=100)
    template_type = models.CharField(
        max_length=20, choices=TEMPLATE_TYPES, default="lyrics"
    )
    background = CloudinaryField(
        "slide_bg", resource_type="image", blank=True, null=True
    )
    background_color = models.CharField(max_length=7, default="#000000")
    text_color = models.CharField(max_length=7, default="#ffffff")
    font_family = models.CharField(max_length=60, default="sans-serif")
    font_size = models.PositiveIntegerField(
        default=48, help_text="Base font size in px."
    )
    overlay_opacity = models.FloatField(
        default=0.4, help_text="Dark overlay on background image (0–1)."
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Advanced: padding, line-height, shadow, etc.",
    )
    is_default = models.BooleanField(default=False)
    is_system = models.BooleanField(
        default=False, help_text="System templates can't be deleted."
    )

    class Meta:
        ordering = ["-is_default", "name"]
        unique_together = ("church", "name")

    def __str__(self):
        return self.name


# ── Service Presentation ──────────────────────────────────────────────────────


class ServicePresentation(ChurchOwnedModel):
    """
    Full slide presentation for a service event — EasyWorship-style.

    Can be auto-populated from a music Setlist (lyric slides)
    and manual Bible passages, announcements, and title slides.

    presentation_data: JSON export of all slides for offline/projection
    use — can be served to a browser-based presenter view.
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
        related_name="presentation",
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="presentations",
    )
    # Linked setlist — when set, lyric slides auto-sync on setlist finalization
    setlist = models.OneToOneField(
        "music.Setlist",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="presentation",
    )
    default_template = models.ForeignKey(
        SlideTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="presentations",
    )
    is_live = models.BooleanField(
        default=False, help_text="True when actively being projected."
    )
    current_slide_index = models.PositiveIntegerField(
        default=0, help_text="Presenter's current slide position."
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_presentations",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "event"]),
            models.Index(fields=["church", "unit"]),
        ]

    def __str__(self):
        return self.title


class PresentationSlide(OrderedChurchModel):
    """
    One slide within a ServicePresentation.

    slide_type controls how it renders:
        lyric      — from a music.LyricsSection
        scripture  — from a BiblePassage
        title      — announcement / title card
        blank      — empty / interstitial
        image      — full-screen image
        video      — full-screen video (Cloudinary or URL)

    content: main text body
    sub_content: secondary text (e.g. Bible reference below the verse)
    metadata: arbitrary JSON (section label, track title, passage ref, etc.)
    """

    SLIDE_TYPES = [
        ("lyric", "Lyric"),
        ("scripture", "Scripture"),
        ("title", "Title / Announcement"),
        ("blank", "Blank"),
        ("image", "Full-Screen Image"),
        ("video", "Full-Screen Video"),
    ]

    presentation = models.ForeignKey(
        ServicePresentation, on_delete=models.CASCADE, related_name="slides"
    )
    slide_type = models.CharField(
        max_length=20, choices=SLIDE_TYPES, default="lyric", db_index=True
    )
    content = models.TextField(blank=True, help_text="Main slide text.")
    sub_content = models.CharField(
        max_length=300, blank=True, help_text="Secondary text (reference, subtitle)."
    )
    template = models.ForeignKey(
        SlideTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="slides",
    )
    image = CloudinaryField("slide_img", resource_type="image", blank=True, null=True)
    video_url = models.URLField(blank=True)
    # Back-references for sync awareness
    lyrics_section = models.ForeignKey(
        "music.LyricsSection",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="slides",
    )
    bible_passage = models.ForeignKey(
        BiblePassage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="slides",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="track title, section label, song order, etc.",
    )
    is_auto = models.BooleanField(
        default=False, help_text="True if auto-generated from setlist sync."
    )

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return (
            f"Slide {self.order}: {self.get_slide_type_display()} — {self.content[:40]}"
        )


# ── Production Schedule (enhanced) ───────────────────────────────────────────


class ProductionSchedule(ChurchOwnedModel):
    """
    Full production checklist for a service or event.
    Readiness flags for slides, audio, video, and broadcast.
    """

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("in_progress", "In Progress"),
        ("review", "Pending Review"),
        ("ready", "Ready"),
        ("completed", "Completed"),
    ]

    event = models.OneToOneField(
        "services.Event", on_delete=models.CASCADE, related_name="production_schedule"
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="production_schedules",
    )
    prepared_by = models.ForeignKey(
        "units.UnitMembership",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="prepared_schedules",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True
    )
    slides_ready = models.BooleanField(default=False)
    audio_ready = models.BooleanField(default=False)
    video_ready = models.BooleanField(default=False)
    broadcast_ready = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "unit"]),
        ]

    def __str__(self):
        return f"Production: {self.event}"

    @property
    def is_fully_ready(self):
        return all(
            [
                self.slides_ready,
                self.audio_ready,
                self.video_ready,
                self.broadcast_ready,
            ]
        )

    def auto_update_status(self):
        n = sum(
            [
                self.slides_ready,
                self.audio_ready,
                self.video_ready,
                self.broadcast_ready,
            ]
        )
        self.status = "draft" if n == 0 else "ready" if n == 4 else "in_progress"
        self.save(update_fields=["status"])


class MediaAsset(OrderedChurchModel):
    """File or URL asset attached to a ProductionSchedule."""

    ASSET_TYPES = [
        ("slides", "Presentation Slides"),
        ("lyric_video", "Lyric Video"),
        ("video_clip", "Video Clip"),
        ("lower_third", "Lower Third"),
        ("background", "Background Image"),
        ("audio", "Audio File"),
        ("other", "Other"),
    ]

    schedule = models.ForeignKey(
        ProductionSchedule, on_delete=models.CASCADE, related_name="assets"
    )
    title = models.CharField(max_length=255)
    asset_type = models.CharField(
        max_length=20, choices=ASSET_TYPES, default="other", db_index=True
    )
    file = CloudinaryField("media_asset", resource_type="auto", blank=True, null=True)
    external_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_assets",
    )

    class Meta:
        ordering = ["order"]
        indexes = [models.Index(fields=["church", "asset_type"])]

    def __str__(self):
        return f"{self.title} [{self.get_asset_type_display()}]"


class BroadcastConfig(ChurchOwnedModel):
    """Streaming and recording config for a specific Event."""

    event = models.OneToOneField(
        "services.Event", on_delete=models.CASCADE, related_name="broadcast_config"
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_configs",
    )
    is_streaming = models.BooleanField(default=False)
    stream_platform = models.CharField(max_length=50, blank=True)
    stream_url = models.URLField(blank=True)
    stream_key = models.CharField(max_length=255, blank=True)
    youtube_url = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)
    is_recording = models.BooleanField(default=False)
    recording_notes = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Broadcast: {self.event}"


class PastorNote(ChurchOwnedModel):
    """
    Message notes attached by a Pastor-role member to a service.
    Visible to the media/production team; not projected.
 
    Access control:
        - Only members whose WorkforceRole.name == 'Pastor' (or is_admin)
          can create/edit their own notes.
        - All media team members can read.
    """
    presentation = models.ForeignKey(
        ServicePresentation,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="pastor_notes",
    )
    event = models.ForeignKey(
        "services.Event",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pastor_notes",
    )
    author = models.ForeignKey(
        "units.UnitMembership",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="pastor_notes",
    )
    title = models.CharField(max_length=255, blank=True, help_text="Message title.")
    series_title = models.CharField(max_length=255, blank=True)
    body = models.TextField(help_text="Sermon notes, key scriptures, outline.")
    scripture_refs = models.JSONField(
        default=list, blank=True,
        help_text="List of scripture references for auto-slide generation.",
    )
 
    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "event"]),
            models.Index(fields=["church", "presentation"]),
        ]
 
    def __str__(self):
        return f"Pastor Note: {self.title or 'Untitled'} — {self.event or 'No event'}"