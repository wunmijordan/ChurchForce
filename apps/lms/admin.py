from django.contrib import admin
from core.admin import ChurchAdmin, ChurchTabularInline
from lms.models import (
    LMSCourse, LMSModule, LMSEnrollment,
    LMSSubmission, LMSReview, LMSCertificate,
)


class LMSModuleInline(ChurchTabularInline):
    model  = LMSModule
    extra  = 1
    fields = ("order", "title", "content_type", "is_required", "requires_attachment")
    show_change_link = True


@admin.register(LMSCourse)
class LMSCourseAdmin(ChurchAdmin):
    list_display  = (
        "title", "course_type", "delivery_mode", "church",
        "unit", "passing_score", "strict_sequence", "is_active",
    )
    list_filter   = ("church", "course_type", "delivery_mode", "is_active")
    search_fields = ("title", "church__name", "unit__name")
    raw_id_fields = ("unit", "membership_track", "created_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = [LMSModuleInline]


@admin.register(LMSModule)
class LMSModuleAdmin(ChurchAdmin):
    list_display  = (
        "title", "course", "content_type", "order",
        "is_required", "requires_attachment", "church",
    )
    list_filter   = ("church", "content_type", "is_required")
    search_fields = ("title", "course__title", "church__name")
    raw_id_fields = ("course",)


@admin.register(LMSEnrollment)
class LMSEnrollmentAdmin(ChurchAdmin):
    list_display  = (
        "member", "course", "status", "score", "church", "created_at",
    )
    list_filter   = ("church", "status", "course")
    search_fields = (
        "member__user__full_name", "member__user__username",
        "course__title", "church__name",
    )
    raw_id_fields = ("member", "course", "current_module", "enrolled_by")
    readonly_fields = ("created_at", "completed_at")


@admin.register(LMSSubmission)
class LMSSubmissionAdmin(ChurchAdmin):
    list_display  = (
        "__str__", "status", "score", "retake_count", "church", "created_at",
    )
    list_filter   = ("church", "status")
    search_fields = (
        "enrollment__member__user__full_name", "module__title", "church__name",
    )
    raw_id_fields = ("enrollment", "module")
    readonly_fields = ("created_at",)


@admin.register(LMSReview)
class LMSReviewAdmin(ChurchAdmin):
    list_display  = ("__str__", "passed", "score", "proctor", "reviewed_at")
    list_filter   = ("passed",)
    search_fields = (
        "proctor__user__full_name",
        "submission__enrollment__member__user__full_name",
    )
    raw_id_fields = ("submission", "proctor")
    readonly_fields = ("reviewed_at",)


@admin.register(LMSCertificate)
class LMSCertificateAdmin(ChurchAdmin):
    list_display  = ("enrollment", "church", "issued_at", "issued_by")
    list_filter   = ("church",)
    search_fields = (
        "enrollment__member__user__full_name",
        "enrollment__course__title",
        "church__name",
    )
    raw_id_fields = ("enrollment", "issued_by")
    readonly_fields = ("issued_at",)
