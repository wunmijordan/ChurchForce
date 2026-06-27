"""
tenants/middleware.py

Two middleware classes that together establish the full tenant context
for every request.

TenantMiddleware (must run first):
    Resolves which Church this request belongs to from the host/path.
    Priority order:
        0. Marketing domain  (churchforce.io → no tenant context)
        0. Founder panel     (founder.workforce.church → superuser only)
        1. Custom domain     (white-label: client.com)
        2. Subdomain         (SaaS: slug.workforce.church)
        3. Path slug         (trial: workforce.church/slug/...)
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

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.urls import set_script_prefix
from django.utils.functional import SimpleLazyObject

from core.request_context import set_current_request
from tenants.models import Church
from accounts.models import ChurchMember
from billing.services import validate_white_label_access
from django.middleware.csrf import CsrfViewMiddleware

ACTIVE_CHURCH_SESSION_KEY = "active_church_slug"


# Paths that bypass the subscription validity check.
SUBSCRIPTION_EXEMPT_PREFIXES = (
    "/tenants/",
    "/billing/",
    "/admin/",
    "/static/",
    "/media/",
    "/health/",
    "/accounts/login/",
    "/accounts/logout/",
    "/accounts/password_reset/",
    "/accounts/password_reset_done/",
    "/accounts/reset/",
    "/sw.js",
)


class TenantMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.is_subscription_valid = False
        request.is_founder_panel = False

        original_path = request.path
        host = request.get_host().split(":")[0]
        path_parts = request.path.strip("/").split("/")
        church = None

        app_domains = list(getattr(settings, "APP_DOMAINS", []) or [])
        app_domain_hosts = [domain.split(":")[0] for domain in app_domains]
        is_app_host = host in app_domain_hosts

        # ── 0a. Marketing domain (churchforce.io) ─────────────────────
        # No tenant context — pass straight through to marketing views.
        marketing_domains = list(getattr(settings, "MARKETING_DOMAINS", []) or [])
        marketing_hosts = [domain.split(":")[0] for domain in marketing_domains]
        if host in marketing_hosts:
            return self.get_response(request)

        # ── 0b. Founder control panel (founder.workforce.church) ──────
        # Superuser-only subdomain. No church attached — the founder views
        # verify is_superuser themselves.
        founder_sub = getattr(settings, "FOUNDER_SUBDOMAIN", "founder")
        founder_hosts = [
            f"{founder_sub}.{domain_host}"
            for domain_host in app_domain_hosts
            if domain_host not in {"localhost", "127.0.0.1"}
        ]
        if host in founder_hosts:
            request.is_founder_panel = True
            request.church = None
            request.member = None
            return self.get_response(request)

        # ── Dev: slug.localhost routing ────────────────────────────────
        if settings.DEBUG and host.endswith(".localhost") and not is_app_host:
            sub = host.split(".")[0]
            church = (
                Church.raw_objects.filter(subdomain=sub, is_active=True).first()
                or Church.raw_objects.filter(slug=sub, is_active=True).first()
            )

        # ── 1. White-label custom domain ──────────────────────────────
        if not church:
            church = Church.raw_objects.filter(
                custom_domain=host,
                is_active=True,
            ).first()

        # ── 2. Subdomain (paid SaaS) ──────────────────────────────────
        if not church and "." in host and not is_app_host:
            subdomain = host.split(".")[0]
            church = Church.raw_objects.filter(
                subdomain=subdomain,
                is_active=True,
            ).first()

        # ── 3. Root domain trial (workforce.church/slug/...) ──────────
        if not church and path_parts and path_parts[0]:
            slug = path_parts[0]
            church = Church.raw_objects.filter(slug=slug, is_active=True).first()
            if church:
                new_path = "/" + "/".join(path_parts[1:])
                request.path_info = new_path or "/"
                if request.path_info != "/" and not request.path_info.endswith("/"):
                    request.path_info += "/"
                request.path = request.path_info

        # ── 4. Session-persisted app-host tenancy ─────────────────────
        # Once a tenant is resolved from /slug/... or subdomain, keep that tenant
        # sticky for later app-host requests that omit the slug.
        if not church and is_app_host:
            session_slug = request.session.get(ACTIVE_CHURCH_SESSION_KEY)
            if session_slug:
                church = Church.raw_objects.filter(
                    slug=session_slug,
                    is_active=True,
                ).first()
                if church and self._should_redirect_to_scoped_path(original_path):
                    # Never redirect AJAX/fetch requests — they can't follow a
                    # redirect and keep the original method (POST → GET → 405).
                    is_ajax = (
                        request.headers.get("X-Requested-With") == "XMLHttpRequest"
                        or "application/json" in request.headers.get("Content-Type", "")
                        or "application/json" in request.headers.get("Accept", "")
                    )
                    if not is_ajax:
                        scoped_path = f"/{church.slug}{original_path if original_path.startswith('/') else '/' + original_path}"
                        if request.META.get("QUERY_STRING"):
                            scoped_path = f"{scoped_path}?{request.META['QUERY_STRING']}"
                        return HttpResponseRedirect(scoped_path)

        # ── 5. Development fallback ───────────────────────────────────
        if not church and "localhost" in host:
            church = Church.raw_objects.filter(is_active=True).first()

        if church and (request.path.startswith(f"/{church.slug}/") or is_app_host):
            set_script_prefix(f"/{church.slug}")

        if not church:
            raise Http404("Organisation not found.")

        validate_white_label_access(church, host)

        request.church = church
        # Campus-churches inherit the HQ subscription when they have none of
        # their own — resolve effective subscription here so the rest of the
        # app just reads request.subscription without caring.
        own_sub = getattr(church, "churchsubscription", None)
        if own_sub is None and church.parent_church_id:
            own_sub = church.get_effective_subscription()
        request.subscription = own_sub
        request.session[ACTIVE_CHURCH_SESSION_KEY] = church.slug

        sub = request.subscription
        request.is_subscription_valid = sub is not None and sub.is_valid()

        # Attach HQ override payload so views/templates can read it cheaply
        request.hq_override = church.hq_override if church.parent_church_id else {}

        if not self._is_exempt(request.path):
            wall = self._subscription_wall(request, church)
            if wall:
                return wall

        return self.get_response(request)

    def _is_exempt(self, path):
        return any(path.startswith(p) for p in SUBSCRIPTION_EXEMPT_PREFIXES)

    def _should_redirect_to_scoped_path(self, path):
        if not path or path == "/":
            return True
        return not any(
            path.startswith(prefix)
            for prefix in (
                "/static/",
                "/media/",
                "/admin/",
                "/health/",
                "/sw.js",
            )
        )

    def _subscription_wall(self, request, church):
        from django.shortcuts import redirect

        sub = request.subscription
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
        set_current_request(request)

        church = getattr(request, "church", None)
        request.subscription = getattr(church, "churchsubscription", None)

        if request.user.is_authenticated and church:
            request.member = SimpleLazyObject(lambda: self._get_member(request))
            request.workforce_member = SimpleLazyObject(
                lambda: self._get_workforce_member(request)
            )
            request.permissions = SimpleLazyObject(
                lambda: self._get_permissions(request)
            )
        else:
            request.member = None
            request.workforce_member = None
            request.permissions = None

        return self.get_response(request)

    def _get_member(self, request):
        return (
            ChurchMember.raw_objects.filter(
                church=request.church,
                user=request.user,
                is_active=True,
            )
            .select_related("user")
            .first()
        )

    def _get_workforce_member(self, request):
        from workforce.models import WorkforceMember

        return (
            WorkforceMember.raw_objects.filter(
                church=request.church,
                member__user=request.user,
                is_active=True,
            )
            .select_related("member__user")
            .first()
        )

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
                return None
        return super()._accept(request)
