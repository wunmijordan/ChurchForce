from django.contrib import admin
from .models import (
    BibleTranslationCache, BibleChapterCache,
    YouVersionTranslationMap, ApiBibleTranslationMap, BibleProviderRequestLog,
    ReadingPlan, ReadingPlanEntry, MemoryVerse,
    BibleStudyReference, MemberPlanProgress, MemberPlanStreak,
    MemberBadgeAward, BibleBadge, BibleShareRecord,
    MemberMemoryVerseProgress, MemberStudyProgress,
)


@admin.register(BibleBadge)
class BibleBadgeAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "icon_emoji", "points", "color_hex")
    search_fields = ("slug", "name")


@admin.register(BibleTranslationCache)
class BibleTranslationCacheAdmin(admin.ModelAdmin):
    list_display = ("translation_id", "name", "language", "updated_at")
    search_fields = ("translation_id", "name")


@admin.register(BibleChapterCache)
class BibleChapterCacheAdmin(admin.ModelAdmin):
    list_display = ("translation", "book_name", "book_id", "chapter", "verse_count", "updated_at")
    list_filter = ("translation",)
    search_fields = ("book_name", "book_id")


@admin.register(YouVersionTranslationMap)
class YouVersionTranslationMapAdmin(admin.ModelAdmin):
    list_display = ("short_code", "bible_id", "name", "language", "updated_at")
    search_fields = ("short_code", "name", "bible_id")
    list_filter = ("language",)


@admin.register(ApiBibleTranslationMap)
class ApiBibleTranslationMapAdmin(admin.ModelAdmin):
    list_display = ("short_code", "bible_id", "name", "updated_at")
    search_fields = ("short_code", "name", "bible_id")


@admin.register(BibleProviderRequestLog)
class BibleProviderRequestLogAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "operation",
        "translation",
        "ok",
        "fallback_used",
        "latency_ms",
        "created_at",
    )
    list_filter = ("provider", "operation", "ok", "fallback_used")
    search_fields = ("translation", "reference", "error")
    readonly_fields = (
        "church",
        "provider",
        "operation",
        "translation",
        "reference",
        "status_code",
        "ok",
        "fallback_used",
        "latency_ms",
        "rate_limit_limit",
        "rate_limit_remaining",
        "rate_limit_reset",
        "error",
        "created_at",
        "updated_at",
    )


class ReadingPlanEntryInline(admin.TabularInline):
    model = ReadingPlanEntry
    extra = 1
    fields = ("order", "scheduled_date", "passage_reference", "notes")


@admin.register(ReadingPlan)
class ReadingPlanAdmin(admin.ModelAdmin):
    list_display = ("title", "church", "frequency", "start_date", "end_date", "is_published", "entry_count")
    list_filter = ("frequency", "is_published", "church")
    search_fields = ("title",)
    inlines = [ReadingPlanEntryInline]


@admin.register(MemoryVerse)
class MemoryVerseAdmin(admin.ModelAdmin):
    list_display = ("reference", "church", "scope", "active_date", "is_published")
    list_filter = ("scope", "is_published", "church")
    search_fields = ("reference",)


@admin.register(BibleStudyReference)
class BibleStudyReferenceAdmin(admin.ModelAdmin):
    list_display = ("title", "reference", "church", "active_date", "is_published")
    list_filter = ("is_published", "church")
    search_fields = ("title", "reference")


@admin.register(MemberBadgeAward)
class MemberBadgeAwardAdmin(admin.ModelAdmin):
    list_display = ("member", "badge", "church", "awarded_at")
    list_filter = ("badge", "church")
    search_fields = ("member__user__full_name",)
