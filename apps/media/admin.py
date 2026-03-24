from django.contrib import admin
from core.admin import ChurchAdmin, ChurchTabularInline
from media.models import ProductionSchedule, MediaAsset, BroadcastConfig


class MediaAssetInline(ChurchTabularInline):
    model   = MediaAsset
    extra   = 1
    fields  = ("order", "title", "asset_type", "file", "external_url", "notes")
    show_change_link = True


@admin.register(ProductionSchedule)
class ProductionScheduleAdmin(ChurchAdmin):
    list_display  = (
        "event", "unit", "church", "status",
        "slides_ready", "audio_ready", "video_ready", "broadcast_ready",
        "is_fully_ready", "is_active",
    )
    list_filter   = ("church", "unit", "status", "is_active")
    search_fields = ("event__name", "unit__name", "church__name")
    raw_id_fields = ("event", "unit", "prepared_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = [MediaAssetInline]

    actions = ["mark_ready"]

    @admin.action(description="Mark selected schedules as Ready")
    def mark_ready(self, request, queryset):
        updated = queryset.update(
            status="ready",
            slides_ready=True,
            audio_ready=True,
            video_ready=True,
            broadcast_ready=True,
        )
        self.message_user(request, f"{updated} schedule(s) marked as ready.")


@admin.register(MediaAsset)
class MediaAssetAdmin(ChurchAdmin):
    list_display  = ("title", "asset_type", "schedule", "church", "order", "is_active")
    list_filter   = ("church", "asset_type", "is_active")
    search_fields = ("title", "church__name", "schedule__event__name")
    raw_id_fields = ("schedule", "uploaded_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BroadcastConfig)
class BroadcastConfigAdmin(ChurchAdmin):
    list_display  = (
        "event", "unit", "church",
        "is_streaming", "stream_platform",
        "is_recording", "is_active",
    )
    list_filter   = ("church", "unit", "is_streaming", "is_recording", "is_active")
    search_fields = ("event__name", "unit__name", "church__name", "stream_platform")
    raw_id_fields = ("event", "unit")
    readonly_fields = ("created_at", "updated_at")

    # Never expose stream keys in list view — only in change form
    def get_list_display(self, request):
        return [f for f in self.list_display if f != "stream_key"]