"""
bible/models.py

Tenant-scoped models for the ChurchForce Bible feature:
  - BibleTranslationCache / BibleChapterCache  — cached HelloAO API data
  - ReadingPlan / ReadingPlanEntry              — admin-created plans
  - MemoryVerse                                 — daily or weekly memory verses
  - BibleStudyReference                         — study references for members
  - MemberPlanProgress / MemberPlanStreak       — per-member reading progress
  - BibleBadge / MemberBadgeAward               — gamified badge system
  - BibleShareRecord                            — track verse / progress shares
"""

from django.db import models
from core.models import ChurchOwnedModel, TimestampedModel


class ApiBibleTranslationMap(TimestampedModel):
    """
    Maps our short translation codes (NIV, NLT, etc.) to API.Bible's internal Bible UUIDs.
    Populated automatically when APIBIBLE_API_KEY is configured.
    """

    short_code = models.CharField(max_length=20, unique=True)  # e.g. "NIV"
    bible_id = models.CharField(max_length=60)  # API.Bible UUID
    name = models.CharField(max_length=200, blank=True)  # display name

    class Meta:
        ordering = ["short_code"]
        verbose_name = "API.Bible Translation Map"
        verbose_name_plural = "API.Bible Translation Maps"

    def __str__(self):
        return f"{self.short_code} → {self.bible_id}"


class YouVersionTranslationMap(TimestampedModel):
    """
    Maps familiar short translation codes (BSB, NIV, etc.) to YouVersion Bible IDs.
    Populated automatically when YOUVERSION_APP_KEY is configured.
    """

    short_code = models.CharField(max_length=20, unique=True)
    bible_id = models.PositiveIntegerField(db_index=True)
    name = models.CharField(max_length=200, blank=True)
    language = models.CharField(max_length=20, blank=True, default="en")
    deep_link = models.URLField(blank=True)

    class Meta:
        ordering = ["short_code"]
        verbose_name = "YouVersion Translation Map"
        verbose_name_plural = "YouVersion Translation Maps"

    def __str__(self):
        return f"{self.short_code} -> {self.bible_id}"


class BibleProviderRequestLog(ChurchOwnedModel):
    """Small analytics trail for Bible provider requests and fallback behavior."""

    PROVIDER_CHOICES = [
        ("youversion", "YouVersion"),
        ("helloao", "HelloAO"),
        ("apibible", "API.Bible"),
    ]
    OPERATION_CHOICES = [
        ("translations", "Translations"),
        ("books", "Books"),
        ("chapter", "Chapter"),
        ("search", "Search"),
        ("passage", "Passage"),
    ]

    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, db_index=True)
    operation = models.CharField(
        max_length=20, choices=OPERATION_CHOICES, db_index=True
    )
    translation = models.CharField(max_length=20, blank=True, db_index=True)
    reference = models.CharField(max_length=120, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    ok = models.BooleanField(default=False, db_index=True)
    fallback_used = models.BooleanField(default=False, db_index=True)
    latency_ms = models.PositiveIntegerField(default=0)
    rate_limit_limit = models.CharField(max_length=40, blank=True)
    rate_limit_remaining = models.CharField(max_length=40, blank=True)
    rate_limit_reset = models.CharField(max_length=80, blank=True)
    error = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "provider", "-created_at"]),
            models.Index(fields=["church", "operation", "-created_at"]),
            models.Index(fields=["church", "ok", "-created_at"]),
        ]
        verbose_name = "Bible Provider Request Log"
        verbose_name_plural = "Bible Provider Request Logs"

    def __str__(self):
        state = "ok" if self.ok else "failed"
        return f"{self.provider} {self.operation} {state}"


# ── Translation & Chapter Cache ───────────────────────────────────────────────


class BibleTranslationCache(TimestampedModel):
    """
    Cached list of available translations from HelloAO API.
    One row per translation; updated periodically.
    """

    translation_id = models.CharField(max_length=20, unique=True)  # e.g. "BSB"
    name = models.CharField(max_length=120)
    language = models.CharField(max_length=60, blank=True)
    website = models.URLField(blank=True)
    license_url = models.URLField(blank=True)
    short_name = models.CharField(max_length=30, blank=True)
    books_data = models.JSONField(default=list)  # cached books.json payload

    class Meta:
        ordering = ["translation_id"]
        verbose_name = "Cached Translation"
        verbose_name_plural = "Cached Translations"

    def __str__(self):
        return f"{self.translation_id} — {self.name}"


class BibleChapterCache(TimestampedModel):
    """
    Cached chapter data (verses) from HelloAO API.
    Key: translation + book_id + chapter.
    """

    translation = models.CharField(max_length=20, db_index=True)
    book_id = models.CharField(max_length=10, db_index=True)  # e.g. "JHN"
    book_name = models.CharField(max_length=60, blank=True)
    chapter = models.PositiveSmallIntegerField()
    verses_data = models.JSONField(default=list)  # [{number, text}, ...]
    verse_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ("translation", "book_id", "chapter")
        indexes = [
            models.Index(fields=["translation", "book_id", "chapter"]),
        ]
        verbose_name = "Cached Chapter"
        verbose_name_plural = "Cached Chapters"

    def __str__(self):
        return f"{self.translation} {self.book_name} {self.chapter}"


# ── Reading Plans ─────────────────────────────────────────────────────────────


class ReadingPlan(ChurchOwnedModel):
    """
    Admin-created Bible reading plan for the church/tenant.
    Frequency determines how entries are assigned to members.
    """

    FREQUENCY_CHOICES = [
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
        ("quarterly", "Quarterly"),
        ("yearly", "Yearly"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    frequency = models.CharField(
        max_length=10, choices=FREQUENCY_CHOICES, default="daily", db_index=True
    )

    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    translation = models.CharField(max_length=20, default="BSB")

    # The admin who created the plan
    created_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_reading_plans",
    )
    is_published = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["church", "is_published", "-start_date"]),
        ]
        verbose_name = "Reading Plan"
        verbose_name_plural = "Reading Plans"

    def __str__(self):
        return f"{self.title} ({self.get_frequency_display()})"

    @property
    def entry_count(self):
        return self.entries.filter(is_active=True).count()


class ReadingPlanEntry(ChurchOwnedModel):
    """
    A single reading unit within a ReadingPlan.
    For daily plans: one passage per day.
    For weekly: one per week, etc.

    Entry types:
      'passage'       — plain Bible reading (passage reference only)
      'devotional'    — inspirational content with optional Bible references (YouVersion-style)
    """

    ENTRY_TYPE_CHOICES = [
        ("passage", "Bible Passage"),
        ("devotional", "Devotional / Inspirational"),
    ]

    plan = models.ForeignKey(
        ReadingPlan, on_delete=models.CASCADE, related_name="entries"
    )

    # Ordering within plan (day 1, day 2 ... or week 1 ...)
    order = models.PositiveSmallIntegerField(default=0)

    # The scheduled date or start-of-period date
    scheduled_date = models.DateField(null=True, blank=True, db_index=True)

    # Entry type — plain passage or inspirational devotional
    entry_type = models.CharField(
        max_length=12, choices=ENTRY_TYPE_CHOICES, default="passage"
    )

    # Passage reference — e.g. "John 3:1-21" or "Psalm 23"
    passage_reference = models.CharField(max_length=200)
    translation = models.CharField(max_length=20, default="BSB")

    # Parsed components for direct API lookup
    book_id = models.CharField(max_length=10, blank=True)  # "JHN"
    chapter = models.PositiveSmallIntegerField(null=True, blank=True)
    verse_start = models.PositiveSmallIntegerField(null=True, blank=True)
    verse_end = models.PositiveSmallIntegerField(null=True, blank=True)
    # End chapter of a range — None means single chapter (same as `chapter`)
    chapter_to = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="End chapter of a range (e.g. 3 for Genesis 1–3). Null = single chapter.",
    )

    # ── Devotional / inspirational content (YouVersion-style) ──────────────
    # Title for this day's devotional (e.g. "Walking in Grace")
    devotional_title = models.CharField(max_length=200, blank=True)

    # Rich inspirational text body (HTML-safe, admin-authored)
    # Can reference multiple Bible passages inline via [REF:John 3:16] markers
    devotional_body = models.TextField(
        blank=True,
        help_text=(
            "Inspirational content body. Use [REF:John 3:16] markers to embed "
            "inline Bible references that expand when clicked."
        ),
    )

    # Additional passage references cited in the devotional body
    # Stored as JSON list: [{"reference": "John 3:16", "note": "Key verse"}]
    inline_references = models.JSONField(
        default=list,
        blank=True,
        help_text="Additional Bible references cited in this devotional entry.",
    )

    # Optional notes/devotional prompt from the admin (for passage-type entries)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "scheduled_date"]
        indexes = [
            models.Index(fields=["plan", "scheduled_date"]),
            models.Index(fields=["plan", "order"]),
        ]
        verbose_name = "Reading Plan Entry"
        verbose_name_plural = "Reading Plan Entries"

    def __str__(self):
        return f"{self.plan.title} — {self.devotional_title or self.passage_reference}"


class BibleHighlight(ChurchOwnedModel):
    """
    A member's in-reader highlight on a Bible verse or passage.
    Supports multiple colors for annotation.
    """

    COLOR_CHOICES = [
        ("yellow", "Yellow"),
        ("green", "Green"),
        ("blue", "Blue"),
        ("pink", "Pink"),
        ("purple", "Purple"),
    ]

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="bible_highlights",
    )
    # Passage identifiers
    translation = models.CharField(max_length=20, default="BSB")
    book_id = models.CharField(max_length=10, db_index=True)
    book_name = models.CharField(max_length=60, blank=True)
    chapter = models.PositiveSmallIntegerField()
    verse_number = models.PositiveSmallIntegerField()

    color = models.CharField(max_length=10, choices=COLOR_CHOICES, default="yellow")
    note = models.TextField(
        blank=True, help_text="Optional personal note on this verse."
    )

    class Meta:
        unique_together = (
            "member",
            "translation",
            "book_id",
            "chapter",
            "verse_number",
        )
        indexes = [
            models.Index(fields=["member", "book_id", "chapter"]),
        ]
        verbose_name = "Bible Highlight"
        verbose_name_plural = "Bible Highlights"

    def __str__(self):
        return f"{self.member} — {self.book_name} {self.chapter}:{self.verse_number} [{self.color}]"


class ReadingPlanDiscussion(ChurchOwnedModel):
    """
    Discussion thread linked to a reading plan entry.
    Can be scoped to the whole workforce or a specific unit/group.
    Can be posted to chat or feeds.
    """

    SCOPE_CHOICES = [
        ("workforce", "Whole Workforce"),
        ("unit", "Unit / Group"),
    ]
    DESTINATION_CHOICES = [
        ("chat", "Chat Thread"),
        ("feeds", "Feeds Post"),
    ]

    entry = models.ForeignKey(
        ReadingPlanEntry,
        on_delete=models.CASCADE,
        related_name="discussions",
    )
    plan = models.ForeignKey(
        ReadingPlan,
        on_delete=models.CASCADE,
        related_name="discussions",
    )

    # Who started the discussion
    author = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="plan_discussions",
    )

    # Question / prompt body
    body = models.TextField()

    # Scope: workforce-wide or unit-specific
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default="workforce")
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="plan_discussions",
        help_text="Set when scope='unit'.",
    )

    # Where this becomes a thread
    destination = models.CharField(
        max_length=6, choices=DESTINATION_CHOICES, default="feeds"
    )

    # IDs of the created thread/post (populated after creation)
    chat_room_id = models.BigIntegerField(null=True, blank=True)
    chat_message_id = models.BigIntegerField(null=True, blank=True)
    feed_post_id = models.BigIntegerField(null=True, blank=True)

    # Admin-initiated discussions (vs member questions)
    is_admin_prompt = models.BooleanField(
        default=False,
        help_text="Admins/leaders start conversations; members ask questions.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["plan", "entry"]),
            models.Index(fields=["church", "scope"]),
        ]
        verbose_name = "Plan Discussion"
        verbose_name_plural = "Plan Discussions"

    def __str__(self):
        return f"Discussion on {self.entry} by {self.author}"

    def get_redirect_url(self):
        """Return the URL of the chat room or feed post for this discussion, if available."""
        try:
            if self.destination == "chat" and self.chat_room_id:
                return f"/workforce/chat/{self.chat_room_id}/"
            if self.destination == "feeds" and self.feed_post_id:
                return f"/feeds/post/{self.feed_post_id}/"
        except Exception:
            pass
        return None


class MemoryVerse(ChurchOwnedModel):
    """
    A verse assigned by admins for members to memorise.
    Can be daily or weekly scope.
    """

    SCOPE_CHOICES = [
        ("daily", "Daily"),
        ("weekly", "Weekly"),
    ]

    reference = models.CharField(max_length=100)  # "Philippians 4:13"
    text = models.TextField()
    translation = models.CharField(max_length=20, default="BSB")
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default="weekly")

    # Date this verse is active for
    active_date = models.DateField(db_index=True)
    end_date = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_memory_verses",
    )
    is_published = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-active_date"]
        indexes = [
            models.Index(fields=["church", "is_published", "-active_date"]),
        ]
        verbose_name = "Memory Verse"
        verbose_name_plural = "Memory Verses"

    def __str__(self):
        return f"{self.reference} ({self.scope})"


# ── Bible Study References ────────────────────────────────────────────────────


class BibleStudyReference(ChurchOwnedModel):
    """
    Admin-created study reference — like YouVersion plans with notes.
    """

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    reference = models.CharField(max_length=200)  # "Romans 8:1-39"
    study_notes = models.TextField(blank=True)
    translation = models.CharField(max_length=20, default="BSB")

    active_date = models.DateField(db_index=True, null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_study_refs",
    )
    is_published = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-active_date", "-created_at"]
        verbose_name = "Bible Study Reference"
        verbose_name_plural = "Bible Study References"

    def __str__(self):
        return f"{self.title} — {self.reference}"


# ── Member Progress ───────────────────────────────────────────────────────────


class MemberPlanProgress(ChurchOwnedModel):
    """
    Tracks a member's completion of individual plan entries.
    Members manually mark entries as complete.
    """

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="bible_plan_progress",
    )
    entry = models.ForeignKey(
        ReadingPlanEntry,
        on_delete=models.CASCADE,
        related_name="completions",
    )
    plan = models.ForeignKey(
        ReadingPlan,
        on_delete=models.CASCADE,
        related_name="member_progress",
    )
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("member", "entry")
        indexes = [
            models.Index(fields=["member", "plan"]),
            models.Index(fields=["plan", "entry"]),
        ]
        verbose_name = "Member Plan Progress"
        verbose_name_plural = "Member Plan Progress"

    def __str__(self):
        return f"{self.member} completed {self.entry.passage_reference}"


class MemberPlanStreak(ChurchOwnedModel):
    """
    Rolling streak tracker for a member on a given plan.
    current_streak: consecutive days/periods with a completion.
    longest_streak: all-time best.
    """

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="bible_streaks",
    )
    plan = models.ForeignKey(
        ReadingPlan,
        on_delete=models.CASCADE,
        related_name="member_streaks",
    )
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_completed_date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ("member", "plan")
        verbose_name = "Member Plan Streak"
        verbose_name_plural = "Member Plan Streaks"

    def __str__(self):
        return f"{self.member} — {self.plan.title} — {self.current_streak}🔥"


class MemberMemoryVerseProgress(ChurchOwnedModel):
    """Tracks which memory verses a member has memorised."""

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="memory_verse_progress",
    )
    verse = models.ForeignKey(
        MemoryVerse,
        on_delete=models.CASCADE,
        related_name="completions",
    )
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("member", "verse")
        verbose_name = "Memory Verse Progress"

    def __str__(self):
        return f"{self.member} memorised {self.verse.reference}"


class MemberStudyProgress(ChurchOwnedModel):
    """Tracks completion of Bible study references."""

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="study_progress",
    )
    study = models.ForeignKey(
        BibleStudyReference,
        on_delete=models.CASCADE,
        related_name="completions",
    )
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("member", "study")
        verbose_name = "Study Reference Progress"

    def __str__(self):
        return f"{self.member} completed {self.study.title}"


# ── Badges ────────────────────────────────────────────────────────────────────


class BibleBadge(TimestampedModel):
    """
    Global badge definitions (not tenant-scoped — same for all churches).
    """

    BADGE_CHOICES = [
        ("first_reading", "First Reading"),
        ("streak_7", "7-Day Streak"),
        ("streak_30", "30-Day Streak"),
        ("plan_finisher", "Plan Finisher"),
        ("memory_master", "Memory Verse Master"),
        ("study_complete", "Bible Study Complete"),
        ("verse_sharer", "Verse Sharer"),
        ("streak_100", "100-Day Streak"),
    ]

    ICON_MAP = {
        "first_reading": "📖",
        "streak_7": "🔥",
        "streak_30": "⚡",
        "plan_finisher": "🏆",
        "memory_master": "🧠",
        "study_complete": "🎓",
        "verse_sharer": "✨",
        "streak_100": "💎",
    }

    slug = models.CharField(max_length=30, unique=True, choices=BADGE_CHOICES)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    icon_emoji = models.CharField(max_length=10, blank=True)
    color_hex = models.CharField(max_length=7, default="#f59f00")
    points = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["slug"]
        verbose_name = "Bible Badge"
        verbose_name_plural = "Bible Badges"

    def __str__(self):
        return f"{self.icon_emoji} {self.name}"

    @classmethod
    def ensure_defaults(cls):
        """Create default badges if they don't exist."""
        defaults = [
            (
                "first_reading",
                "First Reading",
                "Completed your first Bible reading",
                "📖",
                "#22c55e",
                10,
            ),
            (
                "streak_7",
                "7-Day Streak",
                "Read the Bible 7 days in a row",
                "🔥",
                "#f97316",
                20,
            ),
            (
                "streak_30",
                "30-Day Streak",
                "Kept a 30-day reading streak going",
                "⚡",
                "#a855f7",
                50,
            ),
            (
                "plan_finisher",
                "Plan Finisher",
                "Completed an entire reading plan",
                "🏆",
                "#f59f00",
                100,
            ),
            (
                "memory_master",
                "Memory Verse Master",
                "Memorised a Bible verse",
                "🧠",
                "#3b82f6",
                15,
            ),
            (
                "study_complete",
                "Bible Study Complete",
                "Completed a Bible study reference",
                "🎓",
                "#14b8a6",
                15,
            ),
            (
                "verse_sharer",
                "Verse Sharer",
                "Shared a Bible verse to the feed",
                "✨",
                "#ec4899",
                5,
            ),
            (
                "streak_100",
                "Century Streak",
                "An epic 100-day reading streak",
                "💎",
                "#06b6d4",
                200,
            ),
        ]
        for slug, name, desc, emoji, color, points in defaults:
            cls.objects.get_or_create(
                slug=slug,
                defaults=dict(
                    name=name,
                    description=desc,
                    icon_emoji=emoji,
                    color_hex=color,
                    points=points,
                ),
            )


class MemberBadgeAward(ChurchOwnedModel):
    """
    Records that a specific member has earned a specific badge.
    One row per (member, badge) — idempotent, never duplicated.
    """

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="bible_badges",
    )
    badge = models.ForeignKey(
        BibleBadge,
        on_delete=models.CASCADE,
        related_name="awards",
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("member", "badge")
        ordering = ["-awarded_at"]
        indexes = [
            models.Index(fields=["church", "member"]),
        ]
        verbose_name = "Badge Award"
        verbose_name_plural = "Badge Awards"

    def __str__(self):
        return f"{self.member} earned {self.badge.name}"


# ── Share Records ─────────────────────────────────────────────────────────────


class BibleShareRecord(ChurchOwnedModel):
    """
    Records when a member shares a verse or badge to feeds or chat.
    """

    SHARE_TYPE_CHOICES = [
        ("verse", "Bible Verse"),
        ("progress", "Reading Progress"),
        ("badge", "Badge"),
    ]
    DESTINATION_CHOICES = [
        ("feed", "Feeds"),
        ("chat", "Chat"),
    ]

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="bible_shares",
    )
    share_type = models.CharField(max_length=10, choices=SHARE_TYPE_CHOICES)
    destination = models.CharField(
        max_length=5, choices=DESTINATION_CHOICES, default="feed"
    )

    # Verse share details
    reference = models.CharField(max_length=200, blank=True)
    verse_text = models.TextField(blank=True)
    translation = models.CharField(max_length=20, blank=True, default="BSB")

    # Badge share (FK, optional)
    badge = models.ForeignKey(
        BibleBadge,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shares",
    )

    # Linked feed post (if destination=feed)
    feed_post_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "member", "-created_at"]),
        ]
        verbose_name = "Bible Share Record"
        verbose_name_plural = "Bible Share Records"

    def __str__(self):
        return f"{self.member} shared {self.share_type}: {self.reference or self.badge}"


class YouVersionOAuthToken(TimestampedModel):
    """
    Per-member YouVersion OAuth 2.0 tokens.

    One record per member (OneToOne). Created on first successful OAuth
    callback; updated on every token refresh.

    _callback_uri stores the exact redirect_uri passed to YouVersion so
    exchange_code() can reproduce it (OAuth spec requires the values to match).
    """

    member = models.OneToOneField(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="youversion_oauth",
        verbose_name="Church Member",
    )

    # Tokens
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)

    # YouVersion profile (populated after token exchange)
    yv_user_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    yv_username = models.CharField(max_length=120, blank=True, default="")
    yv_avatar_url = models.URLField(blank=True, default="")

    # In-flight PKCE / OAuth state (cleared after exchange)
    oauth_state = models.CharField(max_length=128, blank=True, default="")
    code_verifier = models.CharField(max_length=256, blank=True, default="")

    # The exact redirect_uri sent during the pending auth dance.
    # Must match what's sent in the token exchange request.
    _callback_uri = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="redirect_uri used during this auth dance",
    )

    class Meta:
        verbose_name = "YouVersion OAuth Token"
        verbose_name_plural = "YouVersion OAuth Tokens"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.member} — YouVersion:{self.yv_username or self.yv_user_id or '(pending)'}"

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone

        if not self.expires_at:
            return False
        return timezone.now() >= self.expires_at
