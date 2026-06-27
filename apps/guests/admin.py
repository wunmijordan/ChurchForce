from django.contrib import admin
from core.admin import ChurchAdmin, ChurchTabularInline
from guests.models import (
    GuestEntry,
    FollowUpReport,
    SocialMediaEntry,
    GuestStatus,
    VisitPurpose,
    VisitChannel,
    ChurchService,
    Review,
)


class SocialMediaEntryInline(ChurchTabularInline):
    model = SocialMediaEntry
    extra = 1
    min_num = 0
    can_delete = True


@admin.register(GuestEntry)
class GuestEntryAdmin(ChurchAdmin):
    list_display = (
        "full_name",
        "custom_id",
        "church",
        "date_of_visit",
        "service_attended",
        "status",
        "assigned_to",
    )
    list_filter = ("church", "status", "service_attended")
    search_fields = ("full_name", "phone_number", "email", "custom_id")
    inlines = [SocialMediaEntryInline]
    readonly_fields = ("custom_id", "created_at", "updated_at")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "custom_id",
                    "church",
                    "picture",
                    "title",
                    "full_name",
                    "gender",
                    "phone_number",
                    "email",
                    "date_of_birth",
                    "age_range",
                    "marital_status",
                    "home_address",
                    "occupation",
                )
            },
        ),
        (
            "Visit details",
            {
                "fields": (
                    "date_of_visit",
                    "purpose_of_visit",
                    "channel_of_visit",
                    "service_attended",
                    "status",
                )
            },
        ),
        (
            "Assignment & Referral",
            {
                "fields": (
                    "assigned_to",
                    "assigned_at",
                    "converted_to",
                    "referred_by_member",
                    "referred_by_name",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Non-superusers only see guests for their own church
        from accounts.models import ChurchMember

        member = ChurchMember.raw_objects.filter(
            user=request.user, is_active=True
        ).first()
        if not member:
            return qs.none()
        return qs.filter(church=member.church)


@admin.register(FollowUpReport)
class FollowUpReportAdmin(ChurchAdmin):

    list_display = (
        "guest",
        "church",
        "report_date",
        "assigned_to",
        "attended_events_display",  # ✅ fixed
        "reviewed",
        "contact_attempted",
        "contact_answered",
        "not_planted",
    )

    list_filter = (
        "church",
        "report_date",
        "reviewed",
        "not_planted",
    )

    search_fields = ("guest__full_name", "note")
    list_editable = ("reviewed",)
    readonly_fields = ("created_at", "updated_at")

    # ✅ prevent N+1 queries
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("attended_events")

    # ✅ safe display for ManyToMany
    def attended_events_display(self, obj):
        events = obj.attended_events.all()[:3]
        return ", ".join(e.name for e in events) or "-"

    attended_events_display.short_description = "Attended Events"


@admin.register(GuestStatus)
class GuestStatusAdmin(ChurchAdmin):
    list_display = (
        "name",
        "slug",
        "church",
        "color",
        "is_default",
        "is_terminal",
        "order",
        "is_active",
    )
    list_filter = ("church", "is_default", "is_terminal", "is_active")
    search_fields = ("name", "slug", "church__name")
    ordering = ("church", "order")
    readonly_fields = ("slug",)


@admin.register(VisitPurpose)
class VisitPurposeAdmin(ChurchAdmin):
    list_display = ("name", "church", "order", "is_active")
    list_filter = ("church", "is_active")
    search_fields = ("name", "church__name")
    ordering = ("church", "order")


@admin.register(VisitChannel)
class VisitChannelAdmin(ChurchAdmin):
    list_display = ("name", "church", "order", "is_active")
    list_filter = ("church", "is_active")
    search_fields = ("name", "church__name")
    ordering = ("church", "order")


@admin.register(ChurchService)
class ChurchServiceAdmin(ChurchAdmin):
    list_display = ("name", "church", "order", "is_active")
    list_filter = ("church", "is_active")
    search_fields = ("name", "church__name")
    ordering = ("church", "order")


@admin.register(Review)
class ReviewAdmin(ChurchAdmin):
    list_display = ("guest", "reviewer", "church", "is_read", "created_at")
    list_filter = ("church", "is_read")
    search_fields = ("guest__full_name", "comment", "church__name")
    readonly_fields = ("created_at",)
