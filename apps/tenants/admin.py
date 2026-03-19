from django.contrib import admin
from django.utils.html import format_html
from tenants.models import Church, ChurchSetting


class ChurchSettingInline(admin.StackedInline):
    model  = ChurchSetting
    extra  = 0
    can_delete = False
    verbose_name_plural = "Church settings"
    fields = (
        "committed_attendance_threshold",
        "committed_attendance_window_weeks",
        "member_id_prefix",
        "guest_id_prefix",
        "date_format",
        "enable_music_module",
        "enable_media_module",
        "enable_sms_birthday",
        "enable_sms_bulk",
    )


class ChurchAdmin(admin.ModelAdmin):
    list_display  = (
        "name", "slug", "subdomain", "custom_domain",
        "is_active", "has_coordinates", "timezone", "created_at",
    )
    list_filter   = ("is_active",)
    search_fields = ("name", "slug", "subdomain", "custom_domain")
    readonly_fields = ("created_at", "updated_at", "coordinates_preview")

    inlines = [ChurchSettingInline]

    fieldsets = (
        ("Identity", {
            "fields": ("name", "slug", "subdomain", "custom_domain", "logo", "primary_color", "template"),
        }),
        ("Status", {
            "fields": ("is_active", "created_at", "updated_at"),
        }),
        ("Location & Attendance", {
            "description": (
                "Latitude and longitude are required for geo-fenced attendance. "
                "Find your church's coordinates on Google Maps by right-clicking the venue location."
            ),
            "fields": (
                "latitude", "longitude", "attendance_radius_km",
                "coordinates_preview",
            ),
        }),
        ("Localisation", {
            "fields": ("timezone",),
        }),
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
                    url, lat, lon,
                )
        except Exception:
            pass
        return "Set latitude and longitude above to preview location."
    coordinates_preview.short_description = "Map preview"


admin.site.register(Church, ChurchAdmin)