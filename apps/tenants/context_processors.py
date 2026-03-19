def church_context(request):
    """
    Inject church, subscription, member, and permissions into every
    template context. All values come from attributes set by
    TenantMiddleware and ChurchContextMiddleware.
    """
    return {
        "church":       getattr(request, "church",       None),
        "subscription": getattr(request, "subscription", None),
        "member":       getattr(request, "member",       None),
        "permissions":  getattr(request, "permissions",  None),
    }