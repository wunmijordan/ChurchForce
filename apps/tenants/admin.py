from django.contrib import admin
from django.utils.html import format_html
from core.admin import ChurchAdmin as TenantScopedAdmin
from tenants.models import Church, ChurchSetting, Campus, CampusMembership


class ChurchSettingInline(admin.StackedInline):
    model = ChurchSetting
    extra = 0
    can_delete = False
    verbose_name_plural = "Church settings"
    fields = (
        "committed_attendance_threshold",
        "committed_attendance_window_weeks",
        "member_id_prefix",
        "guest_id_prefix",
        "date_format",
        "time_format",
        "enable_music_module",
        "enable_media_module",
        "enable_sms_birthday",
        "enable_sms_bulk",
    )


class ChurchModelAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "subdomain",
        "custom_domain",
        "is_active",
        "has_coordinates",
        "timezone",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "subdomain", "custom_domain")
    readonly_fields = ("created_at", "updated_at", "coordinates_preview")

    inlines = [ChurchSettingInline]

    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "name",
                    "slug",
                    "subdomain",
                    "custom_domain",
                    "logo",
                    "primary_color",
                    "template",
                ),
            },
        ),
        (
            "Status",
            {
                "fields": ("is_active", "created_at", "updated_at"),
            },
        ),
        (
            "Location & Attendance",
            {
                "description": (
                    "Latitude and longitude are required for geo-fenced attendance. "
                    "Find your church's coordinates on Google Maps by right-clicking the venue location."
                ),
                "fields": (
                    "latitude",
                    "longitude",
                    "attendance_radius_km",
                    "coordinates_preview",
                ),
            },
        ),
        (
            "Localisation",
            {
                "fields": ("timezone",),
            },
        ),
    )

    def has_coordinates(self, obj):
        """Show a tick/cross in the list to quickly spot churches missing coordinates."""
        try:
            if obj.latitude is not None and obj.longitude is not None:
                return format_html('<span style="color:green">✓</span>')
        except Exception:
            pass
        return format_html('<span style="color:red">✗</span>')

    has_coordinates.short_description = "Coordinates set"

    def coordinates_preview(self, obj):
        """
        Render a Google Maps link to preview the coordinates already saved.
        Only shown on the edit page after coordinates are set.
        """
        try:
            if obj.latitude is not None and obj.longitude is not None:
                lat, lon = float(obj.latitude), float(obj.longitude)
                url = f"https://www.google.com/maps?q={lat},{lon}"
                return format_html(
                    '<a href="{}" target="_blank" rel="noopener">'
                    "View on Google Maps ({}, {})"
                    "</a>",
                    url,
                    lat,
                    lon,
                )
        except Exception:
            pass
        return "Set latitude and longitude above to preview location."

    coordinates_preview.short_description = "Map preview"


# ── Campus (HQ-owned branch descriptor) ──────────────────────────────────────


@admin.register(Campus)
class CampusModelAdmin(TenantScopedAdmin):
    """
    Campus is a ChurchOwnedModel scoped to the HQ church.
    Extends core ChurchAdmin so raw_objects + tenant scoping works correctly.
    Soft-deleted (is_active=False) campuses are hidden by default.
    """

    list_display = (
        "name",
        "growth_stage",
        "church",
        "report_to",
        "campus_leader",
        "campus_church_link",
        "contributes_to_parent_metrics",
        "is_active",
    )
    list_filter = ("church", "growth_stage", "is_active")
    search_fields = ("name", "slug", "church__name", "campus_leader__user__full_name")
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ("report_to", "campus_leader", "campus_church")
    readonly_fields = (
        "created_at",
        "updated_at",
        "location_name",
        "campus_church_link",
    )

    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "name",
                    "slug",
                    "description",
                    "church",
                    "growth_stage",
                    "location_name",
                ),
            },
        ),
        (
            "Status",
            {
                "fields": ("is_active", "created_at", "updated_at"),
            },
        ),
        (
            "Leadership & Hierarchy",
            {
                "fields": (
                    "campus_leader",
                    "report_to",
                    "contributes_to_parent_metrics",
                ),
            },
        ),
        (
            "Location",
            {
                "fields": ("address", "latitude", "longitude"),
            },
        ),
        (
            "Provisioned Campus-Church",
            {
                "description": (
                    "The full Church tenant provisioned for this campus. "
                    "Set automatically on campus provisioning — edit with care."
                ),
                "fields": ("campus_church", "campus_church_link"),
            },
        ),
    )

    def get_queryset(self, request):
        qs = self.model.raw_objects.get_queryset()
        if "is_active__exact" not in request.GET:
            qs = qs.filter(is_active=True)
        return qs

    def campus_church_link(self, obj):
        church = getattr(obj, "campus_church", None)
        if not church:
            return "—"
        return format_html(
            '<a href="/admin/tenants/church/{}/change/">{}</a>',
            church.pk,
            church.name,
        )

    campus_church_link.short_description = "Campus church"


# ── CampusMembership (HQ member ↔ campus link) ───────────────────────────────


@admin.register(CampusMembership)
class CampusMembershipModelAdmin(TenantScopedAdmin):
    """
    Links a ChurchMember (HQ-scoped) to a Campus.
    Scoped to the HQ church via core ChurchAdmin.
    """

    list_display = (
        "member",
        "campus",
        "church",
        "is_primary",
        "joined_at",
        "is_active",
    )
    list_filter = ("church", "campus", "is_primary", "is_active")
    search_fields = ("member__user__full_name", "campus__name", "church__name")
    raw_id_fields = ("member", "campus")
    readonly_fields = ("joined_at",)
