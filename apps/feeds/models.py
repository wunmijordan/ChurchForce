"""
feeds/models.py
Thread-like social feed for ChurchForce dashboard.
Each post belongs to a Church (tenant) and optionally to a ChurchUnit.
"""

from django.db import models
from core.models import ChurchOwnedModel

REACTION_CHOICES = [
    ("like", "👍 Like"),
    ("love", "❤️ Love"),
    ("fire", "🔥 Fire"),
    ("pray", "🙏 Pray"),
    ("clap", "👏 Clap"),
    ("amen", "🙌 Amen"),
    ("bless", "✨ Bless"),
    ("inspired", "💡 Inspired"),
    ("plugged", "🔌 Plugged"),
    ("peace", "🕊️ Peace"),
    ("thanks", "🤝 Thanks"),
    ("joy", "😊 Joy"),
    ("wow", "😮 Wow"),
    ("blessed", "⭐ Blessed"),
    ("bible", "📖 Bible"),
    ("faith", "✝️ Faith"),
    ("hope", "🌱 Hope"),
    ("following", "👥 Following"),
    ("checkmate", "♟️ Checkmate"),
]


class Feed(ChurchOwnedModel):
    """
    A single post in the workforce feed.
    Scope: church-wide (unit=None) or unit-specific.
    cover_image is optional — used for the Threads-style header image.
    """

    SCOPE_CHOICES = [
        ("general", "General Workforce"),
        ("unit", "Unit / Group"),
    ]

    author = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="feed_posts",
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="feed_posts",
        help_text="Leave blank for a church-wide (general workforce) post.",
    )
    scope = models.CharField(
        max_length=10, choices=SCOPE_CHOICES, default="general", db_index=True
    )

    body = models.TextField(blank=True)

    # Optional cover image (uploaded by author)
    cover_image = models.ImageField(
        upload_to="feeds/covers/",
        blank=True,
        null=True,
        help_text="Optional cover / header image for this post.",
    )

    # Flags
    is_pinned = models.BooleanField(default=False, db_index=True)
    is_admin_post = models.BooleanField(
        default=False,
        help_text="Admin/leader posts get a badge and appear above regular posts.",
    )

    # Reposts — count cached for fast rendering
    repost_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["church", "scope", "-created_at"]),
            models.Index(fields=["church", "unit", "-created_at"]),
            models.Index(fields=["church", "is_pinned", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.author.full_name}: {(self.body or '')[:60]}"

    @property
    def media_files(self):
        return self.media.all()

    @property
    def reaction_summary(self):
        """Return {reaction: count} across all reaction rows."""
        from django.db.models import Count

        qs = self.reactions.values("reaction").annotate(n=Count("id")).order_by("-n")
        return {r["reaction"]: r["n"] for r in qs}

    @property
    def total_reactions(self):
        return self.reactions.count()

    @property
    def comment_count(self):
        return self.comments.filter(parent__isnull=True).count()


class FeedMedia(ChurchOwnedModel):
    """Video, audio, or image attached to a Feed post."""

    MEDIA_TYPE_CHOICES = [
        ("image", "Image"),
        ("video", "Video"),
        ("audio", "Audio"),
    ]

    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="media")
    file = models.FileField(upload_to="feeds/media/")
    media_type = models.CharField(
        max_length=10, choices=MEDIA_TYPE_CHOICES, db_index=True
    )
    thumbnail = models.ImageField(upload_to="feeds/thumbs/", blank=True, null=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.media_type} on feed #{self.feed_id}"


class FeedReaction(ChurchOwnedModel):
    """
    One row per (user, feed, reaction) — allows a user to apply multiple
    distinct reaction types to the same post.
    """

    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="feed_reactions"
    )
    reaction = models.CharField(max_length=10, choices=REACTION_CHOICES, default="like")

    class Meta:
        # Each (user, feed, reaction-type) triple is unique — allows multiple
        # different reactions from the same user on the same post.
        unique_together = ("feed", "user", "reaction")
        indexes = [models.Index(fields=["feed", "reaction"])]

    def __str__(self):
        return f"{self.user.full_name} {self.reaction} feed #{self.feed_id}"


class FeedComment(ChurchOwnedModel):
    """
    Comment (or reply) on a feed post.
    parent = None → top-level comment.
    parent = <comment> → reply (one level deep, Threads-style).
    """

    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="feed_comments"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies"
    )
    body = models.TextField()

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["feed", "parent", "created_at"]),
        ]

    def __str__(self):
        return f"{self.author.full_name}: {self.body[:60]}"


class FeedRepost(ChurchOwnedModel):
    """Record of a repost — prevents double-reposting and powers repost count."""

    original = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="reposts")
    repost = models.ForeignKey(
        Feed,
        on_delete=models.CASCADE,
        related_name="reposted_from",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="feed_reposts"
    )

    class Meta:
        unique_together = ("original", "user")

    def __str__(self):
        return f"{self.user.full_name} reposted #{self.original_id}"


class FeedNotification(ChurchOwnedModel):
    """
    Lightweight notification record for feed interactions.
    The main notification system handles delivery; this table
    powers the feeds-specific notification tab.
    """

    VERB_CHOICES = [
        ("reacted", "reacted to your post"),
        ("commented", "commented on your post"),
        ("replied", "replied to your comment"),
        ("reposted", "reposted your post"),
        ("mentioned", "mentioned you"),
        ("pinned", "pinned your post"),
    ]

    recipient = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="feed_notifications",
    )
    actor = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="feed_actions"
    )
    verb = models.CharField(max_length=20, choices=VERB_CHOICES)
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="feed_notifs")
    comment = models.ForeignKey(
        FeedComment,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifs",
    )
    is_read = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read", "-created_at"])]

    def __str__(self):
        return f"{self.actor.full_name} {self.verb} → {self.recipient.full_name}"
