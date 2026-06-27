from django.contrib import admin
from core.admin import ChurchAdmin, ChurchTabularInline
from music.models import Track, ChordChart, Setlist, SetlistSong, RehearsalSession


class ChordChartInline(ChurchTabularInline):
    model = ChordChart
    extra = 1
    fields = ("key", "label", "content", "flat_score_id", "file")
    show_change_link = True


class SetlistSongInline(ChurchTabularInline):
    model = SetlistSong
    extra = 1
    fields = ("order", "track", "performance_key", "notes")
    raw_id_fields = ("track",)


@admin.register(Track)
class TrackAdmin(ChurchAdmin):
    list_display = (
        "title",
        "artist",
        "original_key",
        "tempo",
        "unit",
        "church",
        "is_active",
    )
    list_filter = ("church", "unit", "original_key", "is_active")
    search_fields = ("title", "artist", "ccli_id", "church__name", "unit__name")
    raw_id_fields = ("unit", "added_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = [ChordChartInline]


@admin.register(ChordChart)
class ChordChartAdmin(ChurchAdmin):
    list_display = ("__str__", "track", "key", "label", "church", "is_active")
    list_filter = ("church", "key", "is_active")
    search_fields = ("track__title", "label", "church__name")
    raw_id_fields = ("track", "created_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Setlist)
class SetlistAdmin(ChurchAdmin):
    list_display = ("title", "event", "unit", "church", "finalized", "is_active")
    list_filter = ("church", "unit", "finalized", "is_active")
    search_fields = ("title", "church__name", "unit__name", "event__name")
    raw_id_fields = ("event", "unit", "created_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = [SetlistSongInline]


@admin.register(RehearsalSession)
class RehearsalSessionAdmin(ChurchAdmin):
    list_display = ("event", "setlist", "unit", "church", "completed", "is_active")
    list_filter = ("church", "unit", "completed", "is_active")
    search_fields = ("event__name", "unit__name", "church__name")
    raw_id_fields = ("event", "setlist", "unit")
    readonly_fields = ("created_at", "updated_at")
