"""
lms/models.py

Church-embedded Learning Management System.

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

from django.db import models
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel, OrderedChurchModel


class LMSCourse(ChurchOwnedModel):
    """A configurable course within the church LMS."""

    COURSE_TYPE_CHOICES = [
        ("induction",     "Member Induction"),
        ("unit_training", "Unit Training"),
        ("disciplinary",  "Disciplinary Program"),
    ]
    DELIVERY_MODE_CHOICES = [
        ("physical", "Physical"),
        ("virtual",  "Virtual"),
        ("hybrid",   "Physical & Virtual"),
    ]

    title          = models.CharField(max_length=255)
    description    = models.TextField(blank=True)
    course_type    = models.CharField(
        max_length=20, choices=COURSE_TYPE_CHOICES, default="unit_training", db_index=True,
    )
    delivery_mode  = models.CharField(
        max_length=10, choices=DELIVERY_MODE_CHOICES, default="physical",
    )
    unit           = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="lms_courses",
        help_text="If set, this course is specific to this unit. Leave blank for church-wide.",
    )
    # Linked to a MembershipTrack for induction courses
    membership_track = models.ForeignKey(
        "guests.MembershipTrack",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="lms_courses",
    )
    passing_score   = models.PositiveSmallIntegerField(
        default=70,
        help_text="Minimum score (0–100) to pass this course.",
    )
    allow_retake    = models.BooleanField(
        default=True,
        help_text="Allow enrollees to retake a failed module.",
    )
    max_retakes     = models.PositiveSmallIntegerField(
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
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_courses",
    )

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["church", "course_type"]),
            models.Index(fields=["church", "unit"]),
        ]

    def __str__(self):
        return f"{self.title} [{self.get_course_type_display()}]"


class LMSModule(OrderedChurchModel):
    """An ordered section/module within a course."""

    CONTENT_TYPE_CHOICES = [
        ("text",         "Text / Reading"),
        ("video",        "Video"),
        ("pdf",          "PDF / Document"),
        ("attendance",   "Attendance Required"),
        ("assessment",   "Written Assessment"),
        ("practical",    "Practical / Task"),
    ]

    course       = models.ForeignKey(
        LMSCourse, on_delete=models.CASCADE, related_name="modules"
    )
    title        = models.CharField(max_length=255)
    description  = models.TextField(blank=True)
    content_type = models.CharField(
        max_length=20, choices=CONTENT_TYPE_CHOICES, default="text",
    )
    content      = models.TextField(
        blank=True,
        help_text="Text content, video embed URL, or instruction body.",
    )
    resource     = CloudinaryField(
        "lms_resource", resource_type="auto", blank=True, null=True,
        help_text="PDF, video, or other resource file.",
    )
    is_required  = models.BooleanField(
        default=True,
        help_text="If False, this module is optional and does not block progression.",
    )
    # Submission requirements
    requires_attachment = models.BooleanField(
        default=False,
        help_text="If True, submission must include a file attachment.",
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


class LMSEnrollment(ChurchOwnedModel):
    """
    An enrollee's registration in a course.
    One record per (member, course). Tracks overall completion status.
    """

    STATUS_CHOICES = [
        ("active",     "In Progress"),
        ("passed",     "Passed"),
        ("failed",     "Failed"),
        ("withdrawn",  "Withdrawn"),
        ("certified",  "Certified"),
    ]

    member          = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="lms_enrollments",
    )
    course          = models.ForeignKey(
        LMSCourse, on_delete=models.CASCADE, related_name="enrollments"
    )
    status          = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default="active", db_index=True,
    )
    current_module  = models.ForeignKey(
        LMSModule,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="current_enrollees",
        help_text="The module the enrollee is currently on.",
    )
    score           = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Overall computed score (0–100) at completion.",
    )
    enrolled_by     = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="enrolled_members",
    )
    completed_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("member", "course")
        indexes = [
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "course"]),
        ]

    def __str__(self):
        return f"{self.member} → {self.course.title} [{self.status}]"


class LMSSubmission(ChurchOwnedModel):
    """
    A report/submission by an enrollee for a specific module.
    Proctors review submissions and mark them pass/fail.
    """

    STATUS_CHOICES = [
        ("submitted", "Submitted"),
        ("reviewing", "Under Review"),
        ("passed",    "Passed"),
        ("failed",    "Failed"),
        ("returned",  "Returned for Revision"),
    ]

    enrollment  = models.ForeignKey(
        LMSEnrollment, on_delete=models.CASCADE, related_name="submissions"
    )
    module      = models.ForeignKey(
        LMSModule, on_delete=models.CASCADE, related_name="submissions"
    )
    status      = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default="submitted", db_index=True,
    )
    content     = models.TextField(
        blank=True,
        help_text="Written response, reflection, or report body.",
    )
    attachment  = CloudinaryField(
        "lms_submission", resource_type="auto", blank=True, null=True,
        help_text="Required for modules where requires_attachment=True.",
    )
    score       = models.PositiveSmallIntegerField(
        null=True, blank=True,
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


class LMSReview(ChurchOwnedModel):
    """A proctor's review of an LMSSubmission."""

    submission  = models.OneToOneField(
        LMSSubmission, on_delete=models.CASCADE, related_name="review"
    )
    proctor     = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="lms_reviews",
    )
    passed      = models.BooleanField(
        help_text="True = submission passed; False = failed.",
    )
    score       = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Score (0–100) assigned by the proctor.",
    )
    feedback    = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        result = "Passed" if self.passed else "Failed"
        return f"{self.proctor} reviewed {self.submission} — {result}"


class LMSCertificate(ChurchOwnedModel):
    """
    Certificate issued to a member on passing an LMS course.
    Linked to the enrollment record for audit.
    """

    enrollment     = models.OneToOneField(
        LMSEnrollment, on_delete=models.CASCADE, related_name="certificate"
    )
    issued_at      = models.DateTimeField(auto_now_add=True)
    issued_by      = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="issued_certificates",
    )
    certificate_text = models.TextField(blank=True)
    file           = CloudinaryField(
        "lms_certificate", resource_type="raw", blank=True, null=True,
        help_text="Generated PDF certificate file.",
    )

    def __str__(self):
        return f"Certificate: {self.enrollment.member} — {self.enrollment.course.title}"