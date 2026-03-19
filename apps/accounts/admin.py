from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html

from accounts.models import CustomUser, ChurchMember
from accounts.forms import CustomUserCreationForm, CustomUserChangeForm


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm

    list_display = (
        "username", "full_name", "email",
        "is_staff", "is_active", "is_superuser", "image_display",
    )
    ordering = ("username",)
    readonly_fields = ("image_display",)
    filter_horizontal = ("groups", "user_permissions")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {
            "fields": (
                "full_name", "email", "phone_number", "image",
                "title", "marital_status", "address", "date_of_birth",
            )
        }),
        ("Permissions", {
            "fields": (
                "is_active", "is_staff", "is_superuser",
                "groups", "user_permissions",
            )
        }),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "username", "email", "full_name", "phone_number",
                "image", "password1", "password2",
                "is_active", "is_staff", "is_superuser",
                "groups", "user_permissions",
                "title", "marital_status", "address", "date_of_birth",
            ),
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        """
        Pass current_user and church to the form so permission
        and queryset scoping logic works correctly in the admin.

        Church is taken from the first ChurchMember record for the user
        being edited, or None if not found (superuser creation).
        """
        kwargs["form"] = self.add_form if obj is None else self.form
        form_class = super().get_form(request, obj, **kwargs)

        # Resolve the church for queryset scoping
        church = None
        if obj is not None:
            membership = ChurchMember.raw_objects.filter(user=obj).first()
            church = membership.church if membership else None

        class WrappedForm(form_class):
            def __init__(self_inner, *args, **kw):
                kw["current_user"] = request.user
                kw["church"] = church
                if obj is not None:
                    kw["edit_mode"] = True
                super().__init__(*args, **kw)

        return WrappedForm

    def image_display(self, obj):
        if obj.image and hasattr(obj.image, "url"):
            return format_html(
                '<img src="{}" width="40" height="40" style="border-radius:50%" />',
                obj.image.url,
            )
        return "—"
    image_display.short_description = "Profile picture"


@admin.register(ChurchMember)
class ChurchMemberAdmin(admin.ModelAdmin):
    list_display  = ("user", "church", "joined_at", "is_active")
    list_filter   = ("church", "is_active")
    search_fields = ("user__full_name", "user__username", "church__name")
    raw_id_fields = ("user",)