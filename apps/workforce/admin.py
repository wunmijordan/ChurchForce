from django.contrib import admin
from django.utils.html import format_html

from workforce.models import (
    WorkforceStage,
    WorkforceRole,
    WorkforceMember,
    WorkforceMembershipRole,
    AttendanceRecord,
    ClockRecord,
    PersonalReminder,
    UserActivity,
)


@admin.register(WorkforceStage)
class WorkforceStageAdmin(admin.ModelAdmin):
    list_display  = ("name", "church", "order", "is_active")
    list_filter   = ("church", "is_active")
    search_fields = ("name", "church__name")
    ordering      = ("church", "order")


@admin.register(WorkforceRole)
class WorkforceRoleAdmin(admin.ModelAdmin):
    list_display  = ("name", "church", "is_leadership", "order", "is_active")
    list_filter   = ("church", "is_leadership", "is_active")
    search_fields = ("name", "church__name")
    ordering      = ("church", "order")


@admin.register(WorkforceMember)
class WorkforceMemberAdmin(admin.ModelAdmin):
    list_display  = ("member", "church", "stage", "joined_at", "is_active")
    list_filter   = ("church", "stage", "is_active")
    search_fields = (
        "member__user__full_name",
        "member__user__username",
        "church__name",
    )
    raw_id_fields = ("member", "stage")
    readonly_fields = ("joined_at", "stage_updated_at")


@admin.register(WorkforceMembershipRole)
class WorkforceMembershipRoleAdmin(admin.ModelAdmin):
    list_display  = ("workforce_member", "role", "church", "assigned_at")
    list_filter   = ("church", "role")
    search_fields = (
        "workforce_member__member__user__full_name",
        "role__name",
        "church__name",
    )
    raw_id_fields = ("workforce_member", "role")
    readonly_fields = ("assigned_at",)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display  = ("user", "event", "date", "status", "church")
    list_filter   = ("church", "status", "date")
    search_fields = (
        "user__workforce_member__member__user__full_name",
        "event__name",
        "church__name",
    )
    date_hierarchy = "date"
    raw_id_fields  = ("user", "event")


@admin.register(ClockRecord)
class ClockRecordAdmin(admin.ModelAdmin):
    list_display  = ("user", "event", "date", "clock_in", "clock_out", "church")
    list_filter   = ("church", "date")
    search_fields = (
        "user__workforce_member__member__user__full_name",
        "church__name",
    )
    date_hierarchy = "date"
    raw_id_fields  = ("user", "event")


@admin.register(PersonalReminder)
class PersonalReminderAdmin(admin.ModelAdmin):
    list_display  = ("user", "title", "date", "is_done", "church")
    list_filter   = ("church", "is_done", "date")
    search_fields = ("title", "user__member__user__full_name", "church__name")
    date_hierarchy = "date"


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display  = ("user", "activity_type", "church", "created_at")
    list_filter   = ("church", "activity_type")
    search_fields = ("user__user__full_name", "description", "church__name")
    date_hierarchy = "created_at"