"""
tenants/middleware.py

Two middleware classes that together establish the full tenant context
for every request.

TenantMiddleware (must run first):
    Resolves which Church this request belongs to from the host/path.
    Priority order:
        1. Custom domain  (white-label: client.com)
        2. Subdomain      (SaaS: slug.workforce.church)
        3. Path slug      (trial: workforce.church/slug/...)
        4. Localhost fallback (development only)
    Also enforces subscription validity — redirects lapsed tenants to
    the appropriate wall page instead of letting requests through.

ChurchContextMiddleware (must run second):
    Attaches church-scoped objects to the request as lazy attributes:
        request.church           — Church instance
        request.subscription     — ChurchSubscription or None
        request.member           — ChurchMember or None (lazy)
        request.workforce_member — WorkforceMember or None (lazy)
        request.permissions      — PermissionResolver or None (lazy)
"""

from django.http import Http404
from django.utils.functional import SimpleLazyObject

from core.request_context import set_current_request
from tenants.models import Church
from accounts.models import ChurchMember
from billing.services import validate_white_label_access

from django.middleware.csrf import CsrfViewMiddleware


# Paths that bypass the subscription validity check.
# Everything needed to reach the upgrade/payment flow must be here
# so a lapsed tenant is never locked out of fixing their subscription.
SUBSCRIPTION_EXEMPT_PREFIXES = (
    "/tenants/",          # signup, trial-expired, subscription-inactive
    "/billing/",          # pricing, upgrade, portal, webhook
    "/admin/",            # Django admin — never block
    "/static/",           # static assets
    "/media/",            # media files
    "/health/",           # health check endpoint
    "/accounts/login/",
    "/accounts/logout/",
    "/accounts/password_reset/",
    "/accounts/password_reset_done/",
    "/accounts/reset/",
    "/sw.js",             # service worker
)


class TenantMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]
        path_parts = request.path.strip("/").split("/")
        church = None

        # ── 1. White-label custom domain ──────────────────────────────
        church = Church.raw_objects.filter(
            custom_domain=host,
            is_active=True,
        ).first()

        # ── 2. Subdomain (paid SaaS) ──────────────────────────────────
        if not church and "." in host:
            subdomain = host.split(".")[0]
            church = Church.raw_objects.filter(
                subdomain=subdomain,
                is_active=True,
            ).first()

        # ── 3. Root domain trial (workforce.church/slug/...) ─────────────
        if not church and path_parts and path_parts[0]:
            slug = path_parts[0]
            church = Church.raw_objects.filter(
                slug=slug,
                is_active=True,
            ).first()

            if church:
                # Strip the slug prefix from the path so all downstream
                # URL patterns work without knowing about the slug.
                new_path = "/" + "/".join(path_parts[1:])
                request.path_info = new_path or "/"
                request.path = request.path_info

        # ── 4. Development fallback ───────────────────────────────────
        if not church and "localhost" in host:
            church = Church.raw_objects.filter(is_active=True).first()

        if not church:
            raise Http404("Organisation not found.")

        # Validate white-label domain access
        validate_white_label_access(church, host)

        # Attach early — exempt views (billing, admin) need church too
        request.church = church
        request.subscription = getattr(church, "churchsubscription", None)

        # ── Subscription guard ────────────────────────────────────────
        if not self._is_exempt(request.path):
            wall = self._subscription_wall(request, church)
            if wall:
                return wall

        return self.get_response(request)

    # ── Helpers ───────────────────────────────────────────────────────

    def _is_exempt(self, path):
        return any(path.startswith(prefix) for prefix in SUBSCRIPTION_EXEMPT_PREFIXES)

    def _subscription_wall(self, request, church):
        from django.shortcuts import redirect

        sub = getattr(church, "churchsubscription", None)

        if sub is None:
            return redirect("tenants:trial_expired")

        if not sub.is_valid():
            if sub.is_trial:
                return redirect("tenants:trial_expired")
            return redirect("tenants:subscription_inactive")

        return None


class ChurchContextMiddleware:
    """
    Attaches church-scoped objects to the request.
    Must run after TenantMiddleware so request.church is already set.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Register request in async-safe context var for ORM auto-scoping
        set_current_request(request)

        church = getattr(request, "church", None)
        request.subscription = getattr(church, "churchsubscription", None)

        if request.user.is_authenticated and church:
            request.member = SimpleLazyObject(
                lambda: self._get_member(request)
            )
            request.workforce_member = SimpleLazyObject(
                lambda: self._get_workforce_member(request)
            )
            request.permissions = SimpleLazyObject(
                lambda: self._get_permissions(request)
            )
        else:
            request.member           = None
            request.workforce_member = None
            request.permissions      = None

        return self.get_response(request)

    # ── Lazy loaders ──────────────────────────────────────────────────

    def _get_member(self, request):
        return ChurchMember.raw_objects.filter(
            church=request.church,
            user=request.user,
            is_active=True,
        ).select_related("user").first()

    def _get_workforce_member(self, request):
        from workforce.models import WorkforceMember
        return WorkforceMember.raw_objects.filter(
            church=request.church,
            member__user=request.user,   # WorkforceMember → member (ChurchMember) → user
            is_active=True,
        ).select_related("member__user").first()

    def _get_permissions(self, request):
        from permissions.services.resolver import PermissionResolver
        return PermissionResolver(request.user, request.church)


class DynamicCSRFMiddleware(CsrfViewMiddleware):
    """Approve CSRF for any origin that matches a church's custom_domain."""
    def _accept(self, request):
        origin = request.headers.get("Origin", "")
        if origin:
            host = origin.replace("https://", "").replace("http://", "").rstrip("/")
            if Church.objects.filter(custom_domain=host, is_active=True).exists():
                return None   # None = accept
        return super()._accept(request)