from django.contrib import admin

from notifications.models import Notification, UserSettings, PushSubscription


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display  = (
        "title", "member", "church", "is_read", "is_urgent",
        "is_success", "send_at", "created_at",
    )
    list_filter   = ("church", "is_read", "is_urgent", "is_success")
    search_fields = (
        "title", "description",
        "member__user__full_name",
        "member__user__username",
        "church__name",
    )
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy  = "created_at"
    raw_id_fields   = ("member",)

    actions = ["mark_all_read"]

    @admin.action(description="Mark selected notifications as read")
    def mark_all_read(self, request, queryset):
        updated = queryset.filter(is_read=False).update(is_read=True)
        self.message_user(request, f"{updated} notification(s) marked as read.")


@admin.register(UserSettings)
class UserSettingsAdmin(admin.ModelAdmin):
    list_display  = ("member", "church", "notification_sound", "vibration_enabled")
    list_filter   = ("church", "notification_sound", "vibration_enabled")
    search_fields = (
        "member__user__full_name",
        "member__user__username",
        "church__name",
    )
    raw_id_fields = ("member",)


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display  = ("member", "church", "created_at")
    list_filter   = ("church",)
    search_fields = (
        "member__user__full_name",
        "member__user__username",
        "church__name",
    )
    readonly_fields = ("created_at",)
    raw_id_fields   = ("member",)