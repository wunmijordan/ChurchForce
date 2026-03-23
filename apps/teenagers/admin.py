from django.contrib import admin
from core.admin import ChurchAdmin, ChurchTabularInline
from teenagers.models import TeenProfile, MentorAssignment, TrendPulse


class MentorAssignmentInline(ChurchTabularInline):
    model = MentorAssignment
    extra = 0


@admin.register(TeenProfile)
class TeenProfileAdmin(ChurchAdmin):
    list_display = ("full_name", "unit", "member", "guest", "school", "church", "is_active")
    list_filter = ("church", "unit", "is_active")
    search_fields = ("full_name", "school", "member__user__full_name", "guest__full_name")
    inlines = [MentorAssignmentInline]


@admin.register(MentorAssignment)
class MentorAssignmentAdmin(ChurchAdmin):
    list_display = ("teen", "mentor", "start_date", "church")
    list_filter = ("church", "start_date")
    search_fields = ("teen__full_name", "mentor__user__full_name")


@admin.register(TrendPulse)
class TrendPulseAdmin(ChurchAdmin):
    list_display = ("topic", "reported_by", "church", "created_at")
    list_filter = ("church",)
    search_fields = ("topic", "summary")
    readonly_fields = ("created_at",)
