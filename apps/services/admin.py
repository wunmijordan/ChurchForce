from django.contrib import admin
from core.admin import ChurchAdmin
from services.models import Event


@admin.register(Event)
class EventAdmin(ChurchAdmin):
    list_display  = (
        "name", "event_type", "attendance_mode", "church",
        "unit", "date", "day_of_week", "time",
        "is_recurring_weekly", "postponed", "is_active",
    )
    list_filter   = (
        "church", "event_type", "attendance_mode",
        "is_recurring_weekly", "postponed", "is_active",
    )
    search_fields = ("name", "church__name", "unit__name")
    raw_id_fields = ("unit", "created_by")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy  = "date"