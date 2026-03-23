from django.contrib import admin
from core.admin import ChurchAdmin, ChurchTabularInline
from children.models import ChildProfile, ParentContact, CheckInSession, CheckInRecord


class ParentContactInline(ChurchTabularInline):
    model = ParentContact
    extra = 0


class CheckInRecordInline(ChurchTabularInline):
    model = CheckInRecord
    extra = 0
    readonly_fields = ("checked_in_at", "checked_out_at")


@admin.register(ChildProfile)
class ChildProfileAdmin(ChurchAdmin):
    list_display = (
        "first_name",
        "last_name",
        "unit",
        "guardian_member",
        "guardian_guest",
        "guardian_name",
        "church",
        "is_active",
    )
    list_filter = ("church", "unit", "gender", "is_active")
    search_fields = (
        "first_name",
        "last_name",
        "guardian_name",
        "guardian_member__user__full_name",
        "guardian_guest__full_name",
    )
    inlines = [ParentContactInline]


@admin.register(CheckInSession)
class CheckInSessionAdmin(ChurchAdmin):
    list_display = ("title", "session_date", "unit", "church", "is_active")
    list_filter = ("church", "session_date", "unit", "is_active")
    search_fields = ("title", "notes")
    inlines = [CheckInRecordInline]


@admin.register(CheckInRecord)
class CheckInRecordAdmin(ChurchAdmin):
    list_display = (
        "child",
        "session",
        "status",
        "checked_in_at",
        "checked_out_at",
        "church",
    )
    list_filter = ("church", "status")
    search_fields = ("child__first_name", "child__last_name", "session__title")
    readonly_fields = ("checked_in_at", "checked_out_at")
