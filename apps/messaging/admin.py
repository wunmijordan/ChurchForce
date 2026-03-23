from django.contrib import admin
from core.admin import ChurchAdmin, ChurchTabularInline
from messaging.models import GuestMessage, MessageLog


class MessageLogInline(ChurchTabularInline):
    model          = MessageLog
    extra          = 0
    readonly_fields = ("phone", "recipient_name", "status", "provider", "provider_reference", "error_message", "created_at")
    can_delete     = False


@admin.register(GuestMessage)
class GuestMessageAdmin(ChurchAdmin):
    list_display  = ("__str__", "church", "status", "sent_at", "created_at")
    list_filter   = ("church", "status")
    search_fields = (
        "subject", "body",
        "sender__workforce_member__member__user__full_name",
        "church__name",
    )
    raw_id_fields  = ("sender",)
    readonly_fields = ("created_at", "updated_at", "sent_at")
    inlines        = [MessageLogInline]


@admin.register(MessageLog)
class MessageLogAdmin(ChurchAdmin):
    list_display  = (
        "recipient_name", "phone", "status", "category",
        "provider", "church", "created_at",
    )
    list_filter   = ("church", "status", "category", "provider")
    search_fields = (
        "recipient_name", "phone", "body",
        "provider_reference", "church__name",
    )
    readonly_fields = (
        "church", "message", "guest", "phone", "recipient_name",
        "body", "status", "category", "provider",
        "provider_reference", "error_message", "created_at",
    )

    def has_add_permission(self, request):
        return False  # logs are immutable — never add manually

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # only superusers can purge logs