"""
media/models.py

Unit-based media and production management.

Designed for a church's media/production unit. Covers the full
pre-service production workflow: presentation schedules, visual
assets, broadcast/streaming coordination, and slide management.

Model hierarchy:
    ProductionSchedule  — the production plan for a specific Event
        MediaAsset      — files/slides attached to a production schedule
    BroadcastConfig     — streaming/recording settings per Event
"""

from django.db import models
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel, OrderedChurchModel


class ProductionSchedule(ChurchOwnedModel):
    """
    Full production plan for a church service or event.

    Replaces the old PresentationSchedule stub. Links to services.Event
    rather than the removed ServiceEvent model.

    Tracks readiness status across slides, audio, video, and broadcast
    so the media unit leader has a single dashboard view.
    """

    STATUS_CHOICES = [
        ("draft",       "Draft"),
        ("in_progress", "In Progress"),
        ("review",      "Pending Review"),
        ("ready",       "Ready"),
        ("completed",   "Completed"),
    ]

    event      = models.OneToOneField(
        "services.Event",
        on_delete=models.CASCADE,
        related_name="production_schedule",
    )
    unit       = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="production_schedules",
        help_text="The media/production unit responsible for this event.",
    )
    prepared_by = models.ForeignKey(
        "units.UnitMembership",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="prepared_schedules",
    )
    status     = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True
    )

    # Readiness checklist flags
    slides_ready    = models.BooleanField(default=False)
    audio_ready     = models.BooleanField(default=False)
    video_ready     = models.BooleanField(default=False)
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
        return all([
            self.slides_ready,
            self.audio_ready,
            self.video_ready,
            self.broadcast_ready,
        ])

    def auto_update_status(self):
        """Auto-advance status based on readiness flags."""
        ready_count = sum([
            self.slides_ready,
            self.audio_ready,
            self.video_ready,
            self.broadcast_ready,
        ])
        if ready_count == 0:
            self.status = "draft"
        elif ready_count == 4:
            self.status = "ready"
        else:
            self.status = "in_progress"
        self.save(update_fields=["status"])


class MediaAsset(OrderedChurchModel):
    """
    A file or URL asset attached to a ProductionSchedule.

    Covers slides (PowerPoint/PDF), lyric files, video clips,
    lower-thirds, and any other production asset.
    """

    ASSET_TYPES = [
        ("slides",      "Presentation Slides"),
        ("lyric_video", "Lyric Video"),
        ("video_clip",  "Video Clip"),
        ("lower_third", "Lower Third"),
        ("background",  "Background Image"),
        ("audio",       "Audio File"),
        ("other",       "Other"),
    ]

    schedule    = models.ForeignKey(
        ProductionSchedule,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    title       = models.CharField(max_length=255)
    asset_type  = models.CharField(
        max_length=20, choices=ASSET_TYPES, default="other", db_index=True
    )
    file        = CloudinaryField(
        "media_asset", resource_type="auto", blank=True, null=True,
        help_text="Upload the asset file (PDF, image, video, audio).",
    )
    external_url = models.URLField(
        blank=True,
        help_text="External link (e.g. Google Slides, YouTube, Dropbox).",
    )
    notes        = models.TextField(blank=True)
    uploaded_by  = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_assets",
    )

    class Meta:
        ordering = ["order"]
        indexes = [
            models.Index(fields=["church", "asset_type"]),
        ]

    def __str__(self):
        return f"{self.title} [{self.get_asset_type_display()}]"


class BroadcastConfig(ChurchOwnedModel):
    """
    Streaming and recording configuration for a specific Event.

    Stores RTMP stream keys, YouTube/Facebook live URLs, and
    recording preferences so the media team doesn't have to look
    these up manually each service.
    """

    event       = models.OneToOneField(
        "services.Event",
        on_delete=models.CASCADE,
        related_name="broadcast_config",
    )
    unit        = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="broadcast_configs",
    )

    # Streaming
    is_streaming     = models.BooleanField(default=False)
    stream_platform  = models.CharField(
        max_length=50, blank=True,
        help_text="e.g. YouTube, Facebook, Twitch, Custom RTMP",
    )
    stream_url       = models.URLField(blank=True)
    stream_key       = models.CharField(
        max_length=255, blank=True,
        help_text="RTMP stream key — treat as sensitive.",
    )

    # Recording
    is_recording     = models.BooleanField(default=False)
    recording_notes  = models.TextField(blank=True)

    # Technical notes
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Broadcast: {self.event}"