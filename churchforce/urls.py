from dashboard import views as views_dashboard
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.http import JsonResponse
from django.views.static import serve
from accounts.views import post_login_redirect


def health(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),

    # Tenant public routes — signup, walls, settings
    path("tenants/",  include("tenants.urls",  namespace="tenants")),

    # Billing — pricing, upgrade, portal, webhook
    path("billing/",  include("billing.urls",  namespace="billing")),

    # Auth + accounts
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/", include("accounts.urls", namespace="accounts")),

    # Core workforce (root namespace)
    path("",           include("workforce.urls",     namespace="workforce")),

    # Domain apps
    path("guests/",        include("guests.urls",     namespace="guests")),
    path("units/",         include("units.urls",         namespace="units")),
    path("lms/",           include("lms.urls",           namespace="lms")),
    path("notifications/", include("notifications.urls", namespace="notifications")),
    path("messaging/",     include("messaging.urls",     namespace="messaging")),

    # Dashboard routes + top-level alias for {% url "dashboard" %} in templates
    path("dashboard/",    include("dashboard.urls", namespace="dashboard")),
    path("d/",            views_dashboard.dashboard_view,  name="dashboard"),
    path("admin-panel/",  views_dashboard.admin_dashboard, name="admin_dashboard_root"),

    # Utility
    path("post-login/",  post_login_redirect, name="post_login_redirect"),
    path("sw.js",  lambda req: serve(req, "sw.js", document_root=settings.BASE_DIR)),
    path("health/", health),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL,  document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]