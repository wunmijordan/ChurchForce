from django.contrib import admin

class ChurchAdmin(admin.ModelAdmin):
    """
    Base Admin for all ChurchForce tenant models.
    Bypasses frontend 'auto_scope' but maintains 'church' isolation.
    """
    def get_queryset(self, request):
        # Use raw manager; superusers in Django Admin can see all tenants
        qs = self.model.raw_objects.get_queryset()
        if request.user.is_superuser and request.path.startswith("/admin/"):
            return qs
        # Default: filter by current request's church
        return qs.for_request(request)

    # Optional: Auto-set the church field when saving new records in Admin
    def save_model(self, request, obj, form, change):
        if hasattr(obj, 'church') and not obj.church_id:
            obj.church = request.church
        super().save_model(request, obj, form, change)


class ChurchTabularInline(admin.TabularInline):
    """
    Base Inline for tenant-owned models.
    """
    def get_queryset(self, request):
        return self.model.raw_objects.get_queryset().for_request(request)


class ChurchStackedInline(admin.StackedInline):
    """
    Base Stacked Inline for tenant-owned models.
    Mirrors ChurchTabularInline but renders fields in stacked layout —
    useful for inlines with many fields or rich field types (TextFields,
    CloudinaryFields, JSON fields) where the tabular layout would be cramped.
    """
    def get_queryset(self, request):
        return self.model.raw_objects.get_queryset().for_request(request)