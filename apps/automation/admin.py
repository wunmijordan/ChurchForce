from django.contrib import admin
from core.admin import ChurchAdmin
from automation.models import AutomationLog


@admin.register(AutomationLog)
class AutomationLogAdmin(ChurchAdmin):
    list_display   = (
        "event_type", "category", "status", "summary", "church", "created_at"
    )
    list_filter    = ("church", "category", "status")
    search_fields  = ("event_type", "summary", "church__name")
    readonly_fields = (
        "church", "category", "event_type", "status",
        "summary", "payload", "created_at", "updated_at",
    )
    date_hierarchy = "created_at"
    ordering       = ["-created_at"]

    def has_add_permission(self, request):
        return False  # logs are system-generated, never manual

    def has_change_permission(self, request, obj=None):
        return False  # immutable

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # only superusers can purge