from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.http import JsonResponse
from django.views.static import serve
from accounts.views import post_login_redirect, has_perm, _scoped_url
from django.contrib.auth.views import LogoutView
from bible.views import youversion_oauth_callback


def health(request):
    return JsonResponse({"status": "ok"})


def marketing_root(request):
    """Root URL handler: marketing homepage on churchforce.io, signup wall otherwise."""
    host = request.get_host().split(":")[0]
    marketing_hosts = [
        domain.split(":")[0] for domain in getattr(settings, "MARKETING_DOMAINS", [])
    ]
    if host in marketing_hosts:
        from marketing.views import home

        return home(request)
    # Founder panel root
    if getattr(request, "is_founder_panel", False):
        from founder.views import founder_home

        return founder_home(request)
    # On app domain with no path — redirect to login or dashboard
    from django.shortcuts import redirect

    if request.user.is_authenticated:
        if hasattr(request, "church") and request.church:
            if has_perm(request, "dashboard.admin"):
                return redirect(_scoped_url(request, "dashboard:admin_dashboard"))
            return redirect(_scoped_url(request, "dashboard:dashboard"))
        return redirect("dashboard:dashboard")
    return redirect("accounts:login")


def marketing_pricing(request):
    """Pricing page: public marketing version on churchforce.io, portal version otherwise."""
    host = request.get_host().split(":")[0]
    marketing_hosts = [
        domain.split(":")[0] for domain in getattr(settings, "MARKETING_DOMAINS", [])
    ]
    if host in marketing_hosts:
        from marketing.views import pricing

        return pricing(request)
    # On app domain: show the tenant billing pricing page
    from billing.views import pricing as tenant_pricing

    return tenant_pricing(request)


urlpatterns = [
    path("admin/", admin.site.urls),
    # ── Host-dispatched root routes ───────────────────────────────────────────
    # churchforce.io/          → marketing homepage
    # workforce.church/        → redirect to login/dashboard
    path("", marketing_root, name="root"),
    # churchforce.io/pricing/  → public plan cards (no login)
    # workforce.church/pricing/→ tenant billing pricing (login required)
    path("pricing/", marketing_pricing, name="public_pricing"),
    # ── Founder control panel (founder.workforce.church) ─────────────────────
    # Only accessible to Django superusers. Routed by checking request.is_founder_panel
    # which TenantMiddleware sets when the host matches FOUNDER_SUBDOMAIN.APP_DOMAIN.
    path("founder/", include("founder.urls", namespace="founder")),
    # Tenant public routes — signup, walls, settings
    path("", include("tenants.urls", namespace="tenants")),
    # Billing — upgrade, portal, webhook (pricing handled above)
    path("billing/", include("billing.urls", namespace="billing")),
    # Auth + accounts
    path("logout/", LogoutView.as_view(next_page="/"), name="logout"),
    path("accounts/", include("accounts.urls", namespace="accounts")),
    # Core workforce
    path("workforce/", include("workforce.urls", namespace="workforce")),
    path("guests/", include("guests.urls", namespace="guests")),
    path("music/", include("music.urls", namespace="music")),
    path("media/", include("media.urls", namespace="media")),
    path("children/", include("children.urls", namespace="children")),
    path("youth/", include("youth.urls", namespace="youth")),
    path("teenagers/", include("teenagers.urls", namespace="teenagers")),
    path("units/", include("units.urls", namespace="units")),
    path("lms/", include("lms.urls", namespace="lms")),
    path("notifications/", include("notifications.urls", namespace="notifications")),
    path("messaging/", include("messaging.urls", namespace="messaging")),
    path("dashboard/", include("dashboard.urls", namespace="dashboard")),
    path("feeds/", include("feeds.urls", namespace="feeds")),
    path("services/", include("services.urls", namespace="services")),
    path("post-login/", post_login_redirect, name="post_login_redirect"),
    path("sw.js", lambda req: serve(req, "sw.js", document_root=settings.BASE_DIR)),
    path("health/", health),
    path("support/", include("core.support_urls", namespace="support")),
    # Global YouVersion OAuth callback — registered in YV developer dashboard
    path(
        "bible/youversion/callback/",
        youversion_oauth_callback,
        name="yv_callback_global",
    ),
    # Tenant-scoped Bible routes (slug-prefixed in requests via TenantMiddleware)
    path("bible/", include("bible.urls", namespace="bible")),
]

if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
