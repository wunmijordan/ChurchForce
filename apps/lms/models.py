"""
lms/models.py

Church-embedded Learning Management System.

LMSCourse            — configurable course (induction / unit / workforce / disciplinary)
LMSModule            — ordered content item inside a course
LMSRubric            — per-module grading rubric (Canvas-style)
LMSRubricCriterion   — one row in the rubric table
LMSRubricScore       — points per criterion per review
LMSEnrollment        — one record per (member × course)
LMSSubmission        — member's answer / work for a module
LMSPeerReviewAssignment — which peer reviews which submission
LMSPeerReview        — completed peer feedback + score
LMSReview            — proctor's final review of a submission
LMSCertificate       — issued on course pass
LMSSession           — links an LMSCourse to a services.Event (training session)
LMSDiscussionPost    — threaded discussion board per module
LMSAnnouncement      — course-level announcements
LMSModuleCompletion  — "mark as done" for non-assessed modules
LMSCourseRequirement — (legacy) course requirement flag

Supports three course contexts:
    'induction'    — tied to MembershipTrack; must be completed before
                     WorkforceTraineeProfile upgrades to WorkforceMember
    'unit_training'— ongoing unit-specific training (not mandatory for induction)
    'disciplinary' — assigned to members on probation

Delivery modes: physical, virtual, hybrid (each course configures its own).

Assessment flow:
    LMSCourse → LMSModule (ordered) → LMSSubmission (per enrollee per module)
    → LMSReview (proctor reviews submission) → LMSCertificate (on pass)
    or LMSEnrollment cancelled (on fail + no retake allowance)

Progressive assessment: LMSEnrollment.current_module tracks which module
the enrollee is on. They cannot submit for the next module until the
previous one is reviewed and passed (if the course uses strict_sequence=True).
"""

import json
from django.db import models
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel, OrderedChurchModel


class LMSCourse(ChurchOwnedModel):
    """A configurable course within the church LMS."""

    COURSE_TYPE_CHOICES = [
        ("induction", "Member Induction"),
        ("unit_training", "Unit Training"),
        ("workforce", "Workforce-wide Training"),
        ("disciplinary", "Disciplinary Program"),
    ]
    DELIVERY_MODE_CHOICES = [
        ("physical", "Physical"),
        ("virtual", "Virtual"),
        ("hybrid", "Physical & Virtual"),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    course_type = models.CharField(
        max_length=20,
        choices=COURSE_TYPE_CHOICES,
        default="unit_training",
        db_index=True,
    )
    course_image = CloudinaryField(
        "lms_course_banner",
        resource_type="image",
        blank=True, null=True,
        help_text="Banner image shown on the course card and detail header.",
    )
    delivery_mode = models.CharField(
        max_length=10,
        choices=DELIVERY_MODE_CHOICES,
        default="physical",
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lms_courses",
        help_text="If set, this course is specific to this unit. Leave blank for church-wide.",
    )
    # Linked to a MembershipTrack for induction courses
    membership_track = models.ForeignKey(
        "guests.MembershipTrack",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lms_courses",
    )
    passing_score = models.PositiveSmallIntegerField(
        default=70,
        help_text="Minimum score (0–100) to pass this course.",
    )
    allow_retake = models.BooleanField(
        default=True,
        help_text="Allow enrollees to retake a failed module.",
    )
    max_retakes = models.PositiveSmallIntegerField(
        default=2,
        help_text="Maximum number of retakes per module. 0 = unlimited.",
    )
    strict_sequence = models.BooleanField(
        default=True,
        help_text="If True, enrollee must pass each module before accessing the next.",
    )
    certificate_template = models.TextField(
        blank=True,
        help_text="Certificate text template. Use {name}, {course}, {date} as placeholders.",
    )
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_courses",
    )
    # Human-readable slug for URL routing — auto-generated from title.
    slug = models.SlugField(
        max_length=120,
        blank=True,
        db_index=True,
        help_text="Auto-generated from title. Used in course URLs.",
    )
    # Certificate design — upload an existing template image (PNG/PDF)
    # or leave blank to use the text-only certificate_template.
    certificate_image = CloudinaryField(
        "lms_certificate_design",
        resource_type="auto",
        blank=True,
        null=True,
        help_text=(
            "Optional: upload an existing certificate design (PNG, PDF). "
            "If set, this is used instead of the text template."
        ),
    )

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["church", "course_type"]),
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "slug"]),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            base = slugify(self.title)[:100]
            slug = base
            counter = 1
            # church-scoped uniqueness
            while (
                LMSCourse.raw_objects.filter(church=self.church, slug=slug)
                .exclude(pk=self.pk)
                .exists()
            ):
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} [{self.get_course_type_display()}]"

    @property
    def total_points(self):
        """Sum of all rubric max_points across all modules with a rubric."""
        total = 0
        for mod in self.modules.filter(is_active=True):
            if hasattr(mod, "rubric"):
                total += mod.rubric.criteria.filter(is_active=True).aggregate(
                    s=models.Sum("max_points")
                )["s"] or 0
        return total


class LMSCourseRequirement(ChurchOwnedModel):
    course = models.ForeignKey("lms.LMSCourse", on_delete=models.CASCADE)
    counts_for_promotion = models.BooleanField(default=True)
    is_required = models.BooleanField(default=True)


class LMSModule(OrderedChurchModel):
    """An ordered section/module within a course."""

    CONTENT_TYPE_CHOICES = [
        ("text", "Text / Reading"),
        ("video", "Video"),
        ("audio", "Audio"),
        ("pdf", "PDF / Document"),
        ("assessment", "Written Assessment"),
        ("practical", "Practical / Task"),
        ("quiz", "Quiz"),
        ("link", "External Link"),
        ("embed", "Embedded Content"),
        ("reading_plan", "Bible Reading Plan"),
    ]

    # Module template types — determines which structured template is used
    # when content_type is 'text' or 'reading_plan'. Admin populates the
    # template_data JSON field using the selected template's schema.
    TEMPLATE_TYPE_CHOICES = [
        ("none", "No Template (free-form)"),
        ("lesson", "Lesson Page (Notion-style)"),
        ("case_study", "Case Study"),
        ("reflection", "Guided Reflection"),
        ("bible_devotional", "Bible Devotional"),
        ("reading_plan_embed", "Reading Plan Embed"),
        ("discussion_prompt", "Discussion Prompt"),
        ("checklist", "Action Checklist"),
        ("quiz_bank", "Quiz / Question Bank"),
    ]

    course = models.ForeignKey(
        LMSCourse, on_delete=models.CASCADE, related_name="modules"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    content_type = models.CharField(
        max_length=20,
        choices=CONTENT_TYPE_CHOICES,
        default="text",
    )
    content = models.TextField(
        blank=True,
        help_text="Text content, reading material, or assessment question body.",
    )
    resource = CloudinaryField(
        "lms_resource",
        resource_type="auto",
        blank=True,
        null=True,
        help_text="PDF, video, audio, or other resource file.",
    )
    # ── Canvas-style rich content fields ──────────────────────────────────
    external_url = models.URLField(
        blank=True,
        help_text=(
            "External link or YouTube/Vimeo URL. For 'link' type this is the "
            "destination. For 'video' without an uploaded file, the player "
            "auto-detects YouTube/Vimeo and renders an embed."
        ),
    )
    embed_code = models.TextField(
        blank=True,
        help_text=(
            "Raw HTML embed snippet (e.g. <iframe> from Google Slides, H5P, "
            "Canva, Typeform). Rendered inside a sandboxed wrapper."
        ),
    )
    template_data = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Structured content generated from a module template, such as quiz "
            "questions, answer options, and answer keys."
        ),
    )
    # Which template schema populates template_data (and renders the module view)
    template_type = models.CharField(
        max_length=20,
        choices=[
            ("none", "No Template (free-form)"),
            ("lesson", "Lesson Page (Notion-style)"),
            ("case_study", "Case Study"),
            ("reflection", "Guided Reflection"),
            ("bible_devotional", "Bible Devotional"),
            ("reading_plan_embed", "Reading Plan Embed"),
            ("discussion_prompt", "Discussion Prompt"),
            ("checklist", "Action Checklist"),
            ("quiz_bank", "Quiz / Question Bank"),
        ],
        default="none",
        help_text=(
            "When set, the admin populates this module via a structured template "
            "form. template_data stores the resulting JSON."
        ),
    )
    # For reading_plan content type — links to an existing bible reading plan
    reading_plan = models.ForeignKey(
        "bible.ReadingPlan",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lms_modules",
        help_text=(
            "Link a Bible reading plan into this module. "
            "Members complete plan entries as part of the course."
        ),
    )
    estimated_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Estimated time to complete this module in minutes.",
    )
    # ──────────────────────────────────────────────────────────────────────
    is_required = models.BooleanField(
        default=True,
        help_text="If False, this module is optional and does not block progression.",
    )
    # Submission requirements
    requires_attachment = models.BooleanField(
        default=False,
        help_text="If True, submission must include a file attachment.",
    )

    # ── Canvas-style features ──────────────────────────────────────────────
    enable_peer_review = models.BooleanField(
        default=False,
        help_text="Route submissions to peers before proctor final-grade.",
    )
    peer_review_count = models.PositiveSmallIntegerField(
        default=2,
        help_text="How many peers are assigned per submission.",
    )
    discussion_enabled = models.BooleanField(
        default=False,
        help_text="Show a threaded discussion board below this module.",
    )

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "order"],
                name="unique_module_order_per_course",
            )
        ]

    def __str__(self):
        return f"{self.order}. {self.title} ({self.course.title})"

    @property
    def content_type_icon(self):
        icons = {
            "text": "📝", "video": "▶", "audio": "🎵", "pdf": "📄",
            "assessment": "✏", "practical": "🔧", "quiz": "❓",
            "link": "🔗", "embed": "🖼",
        }
        return icons.get(self.content_type, "📄")

    @property
    def is_gradable(self):
        """True if this module type produces a score."""
        return self.content_type in ("assessment", "practical", "quiz")


# ─────────────────────────────────────────────────────────────────────────────
# Rubric
# ─────────────────────────────────────────────────────────────────────────────

class LMSRubric(ChurchOwnedModel):
    """
    Canvas-style grading rubric attached to a module.
    The rubric is used by proctors (and optionally peers) to score submissions
    criterion-by-criterion instead of entering a single score.
    """
    module = models.OneToOneField(LMSModule, on_delete=models.CASCADE, related_name="rubric")
    title = models.CharField(max_length=255, default="Rubric")

    class Meta:
        pass

    def __str__(self):
        return f"Rubric: {self.module.title}"

    @property
    def total_points(self):
        return self.criteria.filter(is_active=True).aggregate(
            s=models.Sum("max_points")
        )["s"] or 0


class LMSRubricCriterion(ChurchOwnedModel):
    """One row in the rubric — e.g. 'Content Accuracy' worth 20 pts."""
    rubric = models.ForeignKey(LMSRubric, on_delete=models.CASCADE, related_name="criteria")
    description = models.CharField(max_length=255)
    max_points = models.PositiveSmallIntegerField(default=10)
    order = models.PositiveSmallIntegerField(default=0)
    # JSON list: [{"label": "Excellent", "points": 10, "description": "..."}, ...]
    ratings_json = models.TextField(blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.description} ({self.max_points} pts)"

    def ratings(self):
        """Return parsed ratings list or a sensible default."""
        if self.ratings_json:
            try:
                return json.loads(self.ratings_json)
            except (json.JSONDecodeError, TypeError):
                pass
        # Auto-generate default ratings from max_points
        mp = self.max_points
        return [
            {"label": "Excellent", "points": mp, "description": ""},
            {"label": "Proficient", "points": int(mp * 0.7), "description": ""},
            {"label": "Developing", "points": int(mp * 0.4), "description": ""},
            {"label": "Insufficient", "points": 0, "description": ""},
        ]


class LMSRubricScore(ChurchOwnedModel):
    """Points awarded per criterion in a proctor or peer review."""
    review = models.ForeignKey(
        "lms.LMSReview", on_delete=models.CASCADE, related_name="rubric_scores",
        null=True, blank=True,
    )
    peer_review = models.ForeignKey(
        "lms.LMSPeerReview", on_delete=models.CASCADE, related_name="rubric_scores",
        null=True, blank=True,
    )
    criterion = models.ForeignKey(
        LMSRubricCriterion, on_delete=models.CASCADE, related_name="scores"
    )
    points_awarded = models.PositiveSmallIntegerField(default=0)
    comment = models.TextField(blank=True)

    class Meta:
        unique_together = [("review", "criterion"), ("peer_review", "criterion")]


class LMSEnrollment(ChurchOwnedModel):
    """
    An enrollee's registration in a course.
    One record per (member, course). Tracks overall completion status.
    """

    STATUS_CHOICES = [
        ("active", "In Progress"),
        ("passed", "Passed"),
        ("failed", "Failed"),
        ("withdrawn", "Withdrawn"),
        ("certified", "Certified"),
    ]

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="lms_enrollments",
    )
    course = models.ForeignKey(
        LMSCourse, on_delete=models.CASCADE, related_name="enrollments"
    )
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default="active",
        db_index=True,
    )
    current_module = models.ForeignKey(
        LMSModule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_enrollees",
        help_text="The module the enrollee is currently on.",
    )
    score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Overall computed score (0–100) at completion.",
    )
    enrolled_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="enrolled_members",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("member", "course")
        indexes = [
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "course"]),
        ]

    def __str__(self):
        return f"{self.member} → {self.course.title} [{self.status}]"

    def progress_pct(self):
        """Percentage of required modules with a passed submission."""
        total = self.course.modules.filter(is_active=True, is_required=True).count()
        if not total:
            return 0
        passed = self.submissions.filter(status="passed").count()
        return int((passed / total) * 100)


class LMSSubmission(ChurchOwnedModel):
    """
    A report/submission by an enrollee for a specific module.
    Proctors review submissions and mark them pass/fail.
    """

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("reviewing", "Under Review"),
        ("peer_review", "Peer Review"),
        ("passed", "Passed"),
        ("failed", "Failed"),
        ("returned", "Returned for Revision"),
    ]

    enrollment = models.ForeignKey(
        LMSEnrollment, on_delete=models.CASCADE, related_name="submissions"
    )
    module = models.ForeignKey(
        LMSModule, on_delete=models.CASCADE, related_name="submissions"
    )
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default="submitted",
        db_index=True,
    )
    content = models.TextField(
        blank=True,
        help_text="Written response, reflection, or report body.",
    )
    attachment = CloudinaryField(
        "lms_submission",
        resource_type="auto",
        blank=True,
        null=True,
        help_text="Required for modules where requires_attachment=True.",
    )
    score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Score assigned by the proctor (0–100).",
    )
    retake_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("enrollment", "module", "retake_count")
        indexes = [
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "module"]),
        ]

    def __str__(self):
        return f"{self.enrollment.member} — {self.module.title} [{self.status}]"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.module.requires_attachment and not self.attachment:
            raise ValidationError(
                f"An attachment is required for the module '{self.module.title}'."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Peer review
# ─────────────────────────────────────────────────────────────────────────────

class LMSPeerReviewAssignment(ChurchOwnedModel):
    """Maps a reviewer to a submission they must peer-review."""
    submission = models.ForeignKey(
        LMSSubmission, on_delete=models.CASCADE, related_name="peer_assignments"
    )
    reviewer = models.ForeignKey(
        "accounts.ChurchMember", on_delete=models.CASCADE, related_name="peer_review_tasks"
    )

    class Meta:
        unique_together = ("submission", "reviewer")

    def __str__(self):
        return f"{self.reviewer} reviews {self.submission}"


class LMSPeerReview(ChurchOwnedModel):
    """A completed peer review of a submission."""
    assignment = models.OneToOneField(
        LMSPeerReviewAssignment, on_delete=models.CASCADE, related_name="review"
    )
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        pass

    def __str__(self):
        return f"Peer review by {self.assignment.reviewer}"


class LMSReview(ChurchOwnedModel):
    """A proctor's review of an LMSSubmission."""

    submission = models.OneToOneField(
        LMSSubmission, on_delete=models.CASCADE, related_name="review"
    )
    proctor = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="lms_reviews",
    )
    passed = models.BooleanField(
        help_text="True = submission passed; False = failed.",
    )
    score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Score (0–100) assigned by the proctor.",
    )
    feedback = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        result = "Passed" if self.passed else "Failed"
        return f"{self.proctor} reviewed {self.submission} — {result}"

    def rubric_total(self):
        return self.rubric_scores.aggregate(s=models.Sum("points_awarded"))["s"] or 0


class LMSCertificate(ChurchOwnedModel):
    """
    Certificate issued to a member on passing an LMS course.
    Linked to the enrollment record for audit.
    """

    enrollment = models.OneToOneField(
        LMSEnrollment, on_delete=models.CASCADE, related_name="certificate"
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="issued_certificates",
    )
    certificate_text = models.TextField(blank=True)
    file = CloudinaryField(
        "lms_certificate",
        resource_type="raw",
        blank=True,
        null=True,
        help_text="Generated PDF certificate file.",
    )

    def __str__(self):
        return f"Certificate: {self.enrollment.member} — {self.enrollment.course.title}"


class LMSSession(ChurchOwnedModel):
    """
    A scheduled physical or virtual class session for an LMS course.

    Each LMSSession links to a services.Event (the actual scheduled class).
    Attendance is gated through the existing workforce.AttendanceRecord model:
    a trainee's UnitMembership must have a present/late AttendanceRecord for
    the linked Event before the course unlocks for them.

    This avoids a separate attendance table — the canonical attendance record
    is used as the source of truth, consistent with the rest of the platform.

    Attendance is a default requirement for all courses (not a module type).
    The gate checks: AttendanceRecord(user=trainee_membership, event=session.event,
    status__in=['present','late','excused']).
    """

    course = models.ForeignKey(
        LMSCourse, on_delete=models.CASCADE, related_name="sessions"
    )
    # The services.Event this session corresponds to.
    # When event_type='Training', the event create/edit form shows available
    # LMS courses to link here.
    event = models.OneToOneField(
        "services.Event",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lms_session",
        help_text=(
            "The scheduled Event this class corresponds to. "
            "Attendance at this event unlocks the course for enrolled trainees."
        ),
    )
    # Which modules this class session covers.
    # When a trainee has an AttendanceRecord for the linked event, only the
    # modules listed here become submittable for them.
    # Leave empty to mean "this session covers the entire course" — all modules
    # are unlocked once ANY session attendance is confirmed.
    modules_covered = models.ManyToManyField(
        "lms.LMSModule",
        blank=True,
        related_name="sessions",
        help_text=(
            "Modules unlocked for trainees who attend this session. "
            "Leave empty to unlock all course modules on any session attendance."
        ),
    )
    title = models.CharField(max_length=255)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["event__date", "event__time"]
        indexes = [
            models.Index(fields=["church", "course"]),
        ]

    def __str__(self):
        date_str = self.event.date if self.event else "unscheduled"
        return f"{self.course.title} — {self.title} ({date_str})"

    def is_attended_by(self, trainee):
        """
        Return True if the trainee has an acceptable AttendanceRecord
        for the linked Event.

        trainee: WorkforceTraineeProfile instance.
        Looks up via trainee.member → WorkforceMember → UnitMembership.
        Since the trainee may not be a WorkforceMember yet, we look through
        the UnitMembership.trainee_profile path.
        """
        if not self.event_id:
            return False
        try:
            from workforce.models import AttendanceRecord

            # AttendanceRecord.user is now a ChurchMember FK.
            # trainee is a WorkforceTraineeProfile; trainee.member is ChurchMember.
            return AttendanceRecord.raw_objects.filter(
                church=self.church,
                user=trainee.member,
                event_id=self.event_id,
                status__in=("present", "late", "excused"),
            ).exists()
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Discussion board
# ─────────────────────────────────────────────────────────────────────────────

class LMSDiscussionPost(ChurchOwnedModel):
    """Threaded discussion on a module."""
    module = models.ForeignKey(LMSModule, on_delete=models.CASCADE, related_name="discussion_posts")
    author = models.ForeignKey(
        "accounts.ChurchMember", on_delete=models.CASCADE, related_name="lms_discussion_posts"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies"
    )
    body = models.TextField()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author} on {self.module.title}"


# ─────────────────────────────────────────────────────────────────────────────
# Announcements
# ─────────────────────────────────────────────────────────────────────────────

class LMSAnnouncement(ChurchOwnedModel):
    """Course-level announcement (like Canvas Announcements)."""
    course = models.ForeignKey(LMSCourse, on_delete=models.CASCADE, related_name="announcements")
    author = models.ForeignKey(
        "accounts.ChurchMember", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lms_announcements",
    )
    title = models.CharField(max_length=255)
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.course.title})"


# ─────────────────────────────────────────────────────────────────────────────
# Module completion (non-graded "mark as done")
# ─────────────────────────────────────────────────────────────────────────────

class LMSModuleCompletion(ChurchOwnedModel):
    """Records that a trainee has viewed/completed a non-assessed module."""
    enrollment = models.ForeignKey(
        LMSEnrollment, on_delete=models.CASCADE, related_name="completions"
    )
    module = models.ForeignKey(
        LMSModule, on_delete=models.CASCADE, related_name="completions"
    )

    class Meta:
        unique_together = ("enrollment", "module")

    def __str__(self):
        return f"{self.enrollment.member} completed {self.module.title}"
