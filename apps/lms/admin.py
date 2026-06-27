"""
lms/admin.py — register all LMS models in Django Admin
Uses ChurchAdmin / ChurchTabularInline from core.admin for proper
church-scoped queryset isolation on every list and inline.
"""

from django.contrib import admin

from core.admin import ChurchAdmin, ChurchTabularInline, ChurchStackedInline

from lms.models import (
    LMSAnnouncement,
    LMSCertificate,
    LMSCourse,
    LMSCourseRequirement,
    LMSDiscussionPost,
    LMSEnrollment,
    LMSModule,
    LMSModuleCompletion,
    LMSPeerReview,
    LMSPeerReviewAssignment,
    LMSReview,
    LMSRubric,
    LMSRubricCriterion,
    LMSRubricScore,
    LMSSession,
    LMSSubmission,
)


# ── Inlines ────────────────────────────────────────────────────────────────

class LMSRubricCriterionInline(ChurchTabularInline):
    model = LMSRubricCriterion
    extra = 1
    fields = ("description", "max_points", "order", "ratings_json", "is_active")


class LMSRubricInline(ChurchStackedInline):
    model = LMSRubric
    extra = 0
    show_change_link = True


class LMSModuleInline(ChurchTabularInline):
    model = LMSModule
    extra = 0
    fields = (
        "order", "title", "content_type", "is_required",
        "enable_peer_review", "discussion_enabled", "is_active",
    )
    ordering = ("order",)


class LMSEnrollmentInline(ChurchTabularInline):
    model = LMSEnrollment
    extra = 0
    fields = ("member", "status", "score", "completed_at", "is_active")
    readonly_fields = ("completed_at",)


class LMSRubricScoreInline(ChurchTabularInline):
    model = LMSRubricScore
    extra = 0
    fields = ("criterion", "points_awarded", "comment")


# ── ModelAdmins ────────────────────────────────────────────────────────────

@admin.register(LMSCourse)
class LMSCourseAdmin(ChurchAdmin):
    list_display = (
        "title", "course_type", "delivery_mode", "passing_score",
        "strict_sequence", "allow_retake", "church", "is_active",
    )
    list_filter = ("course_type", "delivery_mode", "is_active", "church")
    search_fields = ("title", "description", "slug")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [LMSModuleInline, LMSEnrollmentInline]
    fieldsets = (
        (None, {
            "fields": (
                "church", "title", "slug", "description", "course_image",
                "course_type", "delivery_mode", "unit", "membership_track",
            ),
        }),
        ("Grading & Access", {
            "fields": ("passing_score", "allow_retake", "max_retakes", "strict_sequence"),
        }),
        ("Certificate", {
            "fields": ("certificate_template", "certificate_image"),
            "classes": ("collapse",),
        }),
        ("Meta", {
            "fields": ("created_by", "is_active"),
        }),
    )


@admin.register(LMSModule)
class LMSModuleAdmin(ChurchAdmin):
    list_display = (
        "order", "title", "course", "content_type",
        "is_required", "enable_peer_review", "discussion_enabled", "is_active",
    )
    list_filter = (
        "content_type", "is_required", "enable_peer_review",
        "discussion_enabled", "is_active",
    )
    search_fields = ("title", "description", "course__title")
    ordering = ("course", "order")
    inlines = [LMSRubricInline]


@admin.register(LMSRubric)
class LMSRubricAdmin(ChurchAdmin):
    list_display = ("title", "module", "total_points", "church", "is_active")
    inlines = [LMSRubricCriterionInline]
    search_fields = ("title", "module__title")


@admin.register(LMSRubricCriterion)
class LMSRubricCriterionAdmin(ChurchAdmin):
    list_display = ("description", "rubric", "max_points", "order", "is_active")
    ordering = ("rubric", "order")


@admin.register(LMSEnrollment)
class LMSEnrollmentAdmin(ChurchAdmin):
    list_display = (
        "member", "course", "status", "score", "completed_at", "church", "is_active",
    )
    list_filter = ("status", "course__course_type", "is_active")
    search_fields = (
        "member__user__first_name", "member__user__last_name", "course__title",
    )
    readonly_fields = ("completed_at",)


@admin.register(LMSSubmission)
class LMSSubmissionAdmin(ChurchAdmin):
    list_display = (
        "enrollment", "module", "status", "score",
        "retake_count", "created_at", "is_active",
    )
    list_filter = ("status", "is_active")
    search_fields = ("enrollment__member__user__first_name", "module__title")
    readonly_fields = ("created_at",)


@admin.register(LMSReview)
class LMSReviewAdmin(ChurchAdmin):
    list_display = ("submission", "proctor", "passed", "score", "reviewed_at", "is_active")
    list_filter = ("passed", "is_active")
    inlines = [LMSRubricScoreInline]
    readonly_fields = ("reviewed_at",)


@admin.register(LMSPeerReviewAssignment)
class LMSPeerReviewAssignmentAdmin(ChurchAdmin):
    list_display = ("submission", "reviewer", "is_active")
    list_filter = ("is_active",)


@admin.register(LMSPeerReview)
class LMSPeerReviewAdmin(ChurchAdmin):
    list_display = ("assignment", "score", "completed_at", "is_active")
    inlines = [LMSRubricScoreInline]
    readonly_fields = ("completed_at",)


@admin.register(LMSCertificate)
class LMSCertificateAdmin(ChurchAdmin):
    list_display = ("enrollment", "issued_by", "issued_at", "is_active")
    readonly_fields = ("issued_at",)
    search_fields = (
        "enrollment__member__user__first_name",
        "enrollment__course__title",
    )


@admin.register(LMSSession)
class LMSSessionAdmin(ChurchAdmin):
    list_display = ("title", "course", "event", "church", "is_active")
    list_filter = ("course", "is_active")
    filter_horizontal = ("modules_covered",)
    search_fields = ("title", "course__title", "event__name")


@admin.register(LMSDiscussionPost)
class LMSDiscussionPostAdmin(ChurchAdmin):
    list_display = ("module", "author", "parent", "created_at", "is_active")
    list_filter = ("is_active",)
    search_fields = ("body", "author__user__first_name")


@admin.register(LMSAnnouncement)
class LMSAnnouncementAdmin(ChurchAdmin):
    list_display = ("title", "course", "author", "created_at", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "body", "course__title")


@admin.register(LMSModuleCompletion)
class LMSModuleCompletionAdmin(ChurchAdmin):
    list_display = ("enrollment", "module", "created_at", "is_active")


@admin.register(LMSCourseRequirement)
class LMSCourseRequirementAdmin(ChurchAdmin):
    list_display = ("course", "is_required", "counts_for_promotion", "is_active")