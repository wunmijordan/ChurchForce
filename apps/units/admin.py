from django.contrib import admin
from core.admin import ChurchAdmin
from units.models import (
    ChurchUnit,
    UnitMembership,
    UnitAttendanceRecord,
    ChatRoom,
    PrivateChatRequest,
)


@admin.register(ChurchUnit)
class ChurchUnitAdmin(ChurchAdmin):
    list_display = (
        "name",
        "unit_type",
        "church",
        "report_to",
        "is_default",
        "member_count",
        "is_active",
        "guest_management",
        "music_module",
        "media_module",
    )
    list_filter = (
        "church",
        "unit_type",
        "is_default",
        "is_active",
        "guest_management",
        "music_module",
        "media_module",
        "children_module",
        "youth_module",
        "teenagers_module",
    )
    search_fields = ("name", "slug", "church__name")
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ("report_to",)
    readonly_fields = ("created_at", "updated_at")

    def member_count(self, obj):
        return obj.memberships.filter(is_active=True).count()

    member_count.short_description = "Members"


@admin.register(UnitMembership)
class UnitMembershipAdmin(ChurchAdmin):
    list_display = (
        "__str__",
        "unit",
        "church",
        "is_probation",
        "is_unit_head",
        "consecutive_absences",
        "for_review",
        "is_active",
    )
    list_filter = (
        "church",
        "unit",
        "is_probation",
        "is_unit_head",
        "for_review",
        "is_active",
    )
    search_fields = (
        "workforce_member__member__user__full_name",
        "trainee_profile__member__user__full_name",
        "unit__name",
        "church__name",
    )
    raw_id_fields = ("workforce_member", "trainee_profile", "unit")
    readonly_fields = ("joined_at", "consecutive_absences")

    actions = ["clear_for_review", "set_probation", "clear_probation"]

    @admin.action(description="Clear 'for review' flag")
    def clear_for_review(self, request, queryset):
        updated = queryset.update(for_review=False, consecutive_absences=0)
        self.message_user(request, f"{updated} membership(s) cleared.")

    @admin.action(description="Set probation")
    def set_probation(self, request, queryset):
        queryset.update(is_probation=True)

    @admin.action(description="Clear probation")
    def clear_probation(self, request, queryset):
        queryset.update(is_probation=False)


@admin.register(UnitAttendanceRecord)
class UnitAttendanceRecordAdmin(ChurchAdmin):
    list_display = ("membership", "unit", "date", "status", "session", "church")
    list_filter = ("church", "unit", "status")
    search_fields = (
        "membership__workforce_member__member__user__full_name",
        "unit__name",
        "session",
        "church__name",
    )
    raw_id_fields = ("membership", "unit", "marked_by")
    date_hierarchy = "date"


@admin.register(ChatRoom)
class ChatRoomAdmin(ChurchAdmin):
    list_display = ("name", "room_type", "unit", "church", "is_default", "is_active")
    list_filter = ("church", "room_type", "is_default", "is_active")
    search_fields = ("name", "unit__name", "church__name")
    raw_id_fields = ("unit", "created_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PrivateChatRequest)
class PrivateChatRequestAdmin(ChurchAdmin):
    list_display = ("requester", "recipient", "room", "church", "created_at")
    list_filter = ("church",)
    search_fields = (
        "requester__user__full_name",
        "recipient__user__full_name",
        "room__name",
        "church__name",
    )
    raw_id_fields = ("room", "requester", "recipient")
    readonly_fields = ("created_at",)
