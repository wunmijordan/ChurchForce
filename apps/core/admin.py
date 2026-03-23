from django.contrib import admin

class ChurchAdmin(admin.ModelAdmin):
    """
    Base Admin for all ChurchForce tenant models.
    Bypasses frontend 'auto_scope' but maintains 'church' isolation.
    """
    def get_queryset(self, request):
        # Always use the 'raw' manager but filter by the current request's church
        # This ensures the Admin only sees data for the church they are currently managing
        return self.model.raw_objects.get_queryset().for_request(request)

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