from django.contrib import admin
from core.admin import ChurchAdmin
from permissions.models import UnitRole, MembershipRole


@admin.register(UnitRole)
class UnitRoleAdmin(ChurchAdmin):
    list_display   = ("name", "unit", "church", "is_leadership", "order", "is_active")
    list_filter    = ("church", "is_leadership", "is_active")
    search_fields  = ("name", "unit__name", "church__name")
    ordering       = ("church", "unit", "order")
    raw_id_fields  = ("unit",)


@admin.register(MembershipRole)
class MembershipRoleAdmin(ChurchAdmin):
    list_display  = ("membership", "role", "church", "assigned_at")
    list_filter   = ("church",)
    search_fields = (
        "membership__workforce_member__member__user__full_name",
        "role__name",
        "church__name",
    )
    raw_id_fields  = ("membership", "role")
    readonly_fields = ("assigned_at",)