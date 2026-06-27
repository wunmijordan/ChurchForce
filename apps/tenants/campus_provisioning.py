"""
tenants/campus_provisioning.py

Provisions a campus-church: a full Church instance that is a branch of
an HQ church, with its own slug derived from the HQ slug + location name.

Routing rules (all environments):
  - In DEBUG (dev):          workforce.localhost:8000/<hq-slug>-<location>/
  - SaaS subdomain (prod):   <hq-slug>-<location>.<app-domain>/
  - Custom domain (prod):    as set by HQ admin separately; slug still built same way

The campus-church slug is ALWAYS:  <hq-slug>-<location>
The subdomain (when the HQ has one) is: <hq-slug>-<location>  (flat, no dots)
This keeps URLs predictable and relative to the parent at every tier.

Username suffix for campus members:  <username>.<hq-slug>-<location>

Entry point:  provision_campus_church(hq_church, campus, leader_member)
"""

import logging
from django.db import transaction
from django.utils.text import slugify

logger = logging.getLogger(__name__)


# ── Slug helpers ──────────────────────────────────────────────────────────────


def _campus_slug(hq_church, location_name: str) -> str:
    """
    Build a globally-unique Church.slug for a campus-church.

    Always: <hq-slug>-<location>  (e.g. gatewaynation-abuja)
    Numeric suffix appended only on collision.
    """
    from tenants.models import Church

    base = f"{hq_church.slug}-{location_name}"
    candidate = base
    n = 2
    while Church.raw_objects.filter(slug=candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _campus_subdomain(hq_church, location_name: str) -> str | None:
    """
    Build a subdomain for a campus-church.

    We use the same flat slug as the campus slug (no dots) so the
    middleware's subdomain lookup finds it with a simple filter.

    Only set when the HQ church itself has a subdomain (SaaS plan).
    In dev / trial (no subdomain), returns None — path routing is used.
    """
    from tenants.models import Church

    if not hq_church.subdomain:
        return None

    # campus subdomain = <hq-slug>-<location>  (same as slug, flat, no dots)
    base = f"{hq_church.slug}-{location_name}"
    candidate = base
    n = 2
    while Church.raw_objects.filter(subdomain=candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def campus_church_url(church_obj) -> str:
    """
    Return the public access URL for a campus-church (or any Church).

    In DEBUG / dev: always http://<app_domain>/<slug>  (path routing)
    In prod: custom_domain > subdomain > slug path
    """
    from django.conf import settings as _settings

    if not church_obj:
        return ""

    app_domains = getattr(_settings, "APP_DOMAINS", None) or ["workforce.church"]
    app_domain = app_domains[0]

    # Dev always uses path routing — subdomain DNS doesn't resolve on localhost
    if _settings.DEBUG:
        return f"http://{app_domain}/{church_obj.slug}"

    if church_obj.custom_domain:
        return f"https://{church_obj.custom_domain}"

    if church_obj.subdomain:
        return f"https://{church_obj.subdomain}.{app_domain}"

    # Trial / path-routed prod
    return f"https://{app_domain}/{church_obj.slug}"


# ── Core provisioning ─────────────────────────────────────────────────────────


@transaction.atomic
def provision_campus_church(
    hq_church,
    campus,
    *,
    leader_member=None,
    location_name: str = "",
) -> "Church":
    """
    Provision a full Church instance for a campus.

    Wrapped in a savepoint so that any inner failure (bootstrap, settings
    inheritance, leader provisioning) rolls back only the campus-church row
    without breaking the outer transaction the view may be running in.

    Steps:
      1. Derive slug/subdomain from HQ routing mode + location_name.
      2. Create the Church row with parent_church = hq_church.
      3. Apply the bootstrap template (all defaults seeded).
      4. Inherit key ChurchSetting values from HQ.
      5. Provision the campus leader inside the campus-church (if given).
      6. Link back: Campus.campus_church = new church.
    """
    from tenants.models import Church

    location_name = slugify(location_name or campus.name)
    campus.location_name = location_name
    # save location_name immediately so it persists even if rest fails
    campus.save(update_fields=["location_name"])

    # ── 1. Build slug / subdomain ─────────────────────────────────────
    new_slug = _campus_slug(hq_church, location_name)
    new_subdomain = _campus_subdomain(hq_church, location_name)

    # ── 2. Create the Church row ──────────────────────────────────────
    campus_church = Church.raw_objects.create(
        name=f"{hq_church.name} — {campus.name}",
        slug=new_slug,
        subdomain=new_subdomain,
        primary_color=hq_church.primary_color,
        logo=hq_church.logo,
        template=hq_church.template,
        timezone=hq_church.timezone,
        latitude=campus.latitude or hq_church.latitude,
        longitude=campus.longitude or hq_church.longitude,
        attendance_radius_km=hq_church.attendance_radius_km,
        parent_church=hq_church,
        campus_location_name=location_name,
        hq_override=campus.workspace_config or {},
        is_active=True,
    )

    logger.info(
        "Campus-church created: %s (slug=%s) under HQ %s",
        campus_church.name,
        campus_church.slug,
        hq_church.slug,
    )

    # ── 3. Bootstrap (NO bible seeding — bible content is not needed  ─
    #       for campus provisioning; those views use welcome/birthday     ─
    #       settings only, which are inherited from HQ in step 4).        ─
    _bootstrap_campus_church(campus_church)

    # ── 4. Inherit HQ settings ────────────────────────────────────────
    _inherit_hq_settings(hq_church, campus_church)

    # ── 5. Provision the campus leader inside the campus-church ──────
    if leader_member:
        _provision_leader_in_campus(leader_member, campus_church)

    # ── 6. Link back ──────────────────────────────────────────────────
    campus.campus_church = campus_church
    campus.save(update_fields=["campus_church"])

    return campus_church


# ── Bootstrap (bible-free) ────────────────────────────────────────────────────


def _bootstrap_campus_church(campus_church):
    """
    Run the standard church bootstrap for the campus-church but explicitly
    skip bible-passage seeding, which:
      a) makes HTTP calls that can fail / time out
      b) hits a unique constraint when the same reference already exists
         for another church in the same @transaction.atomic block, which
         would poison the savepoint and prevent any further DB work.

    Bible content for the welcome modal and birthday messages is inherited
    from HQ ChurchSetting in _inherit_hq_settings() — no separate seeding needed.
    """
    from bootstrap.services import apply_template_to_church

    template = campus_church.template or _get_default_template()

    # Monkey-patch the bible seeder to a no-op just for this call so we
    # don't have to fork apply_template_to_church.
    import bootstrap.services as _bs

    _orig = getattr(_bs, "seed_bible_content_for_plan", None)
    try:
        if _orig:
            _bs.seed_bible_content_for_plan = lambda *a, **kw: None
        apply_template_to_church(template, campus_church)
    finally:
        if _orig:
            _bs.seed_bible_content_for_plan = _orig


def _get_default_template():
    from bootstrap.services import ensure_default_template

    return ensure_default_template()


# ── Inherit HQ settings ───────────────────────────────────────────────────────


def _inherit_hq_settings(hq_church, campus_church):
    """
    Copy relevant ChurchSetting values from HQ into the campus-church.
    Done AFTER bootstrap so we overwrite defaults with HQ values.

    Uses raw attribute access (not getattr(church, 'settings')) inside
    an atomic block to avoid Django's RelatedObjectDoesNotExist raising
    after the outer transaction has a broken savepoint.
    """
    from tenants.models import ChurchSetting

    # Fetch settings via the manager to avoid cached_property issues inside
    # the atomic block when the object was just created.
    try:
        hq_settings = ChurchSetting.objects.get(church=hq_church)
    except ChurchSetting.DoesNotExist:
        return

    try:
        campus_settings = ChurchSetting.objects.get(church=campus_church)
    except ChurchSetting.DoesNotExist:
        return

    INHERIT_FIELDS = [
        "date_format",
        "time_format",
        "committed_attendance_threshold",
        "committed_attendance_window_weeks",
        "enable_guest_module",
        "enable_music_module",
        "enable_media_module",
        "enable_children_module",
        "enable_youth_module",
        "enable_teenagers_module",
        "enable_sms_birthday",
        "enable_sms_bulk",
        "enable_welcome_screen",
        "campus_growth_stages",
        "vision_statement",
        "mission_statement",
        "denominational_affiliation",
        "core_values",
        "thankyou_first_visit_message",
        "thankyou_second_visit_message",
        "send_first_visit_thankyou",
        "send_second_visit_thankyou",
        "lms_config",
        "workforce_config",
        # Username suffix — campus members' username displays as user.<campus-slug>
        "username_prefix",
        "username_use_church_slug_suffix",
    ]

    changed = []
    for field in INHERIT_FIELDS:
        if not hasattr(campus_settings, field):
            continue
        val = getattr(hq_settings, field, None)
        if val is not None:
            setattr(campus_settings, field, val)
            changed.append(field)

    if changed:
        campus_settings.save(update_fields=[*changed, "updated_at"])


# ── Provision campus leader ───────────────────────────────────────────────────


def _provision_leader_in_campus(hq_leader_member, campus_church):
    """
    Given an HQ ChurchMember (the designated campus leader), ensure they
    have a ChurchMember record in the campus-church with is_admin=True.

    The underlying CustomUser is shared — same login credentials work for
    both the HQ church and the campus-church. The middleware scopes context
    per request.church on each request.
    """
    from accounts.models import ChurchMember

    user = hq_leader_member.user

    campus_member, created = ChurchMember.raw_objects.get_or_create(
        church=campus_church,
        user=user,
        defaults={
            "is_admin": True,
            "is_active": True,
        },
    )

    if not created and not campus_member.is_admin:
        campus_member.is_admin = True
        campus_member.save(update_fields=["is_admin", "updated_at"])

    # Bootstrap admin tier access inside the campus-church
    try:
        from bootstrap.services import _ensure_admin_bootstrap

        _ensure_admin_bootstrap(campus_church, admin_member=campus_member)
    except Exception:
        # Never block leader provisioning over optional tier wiring.
        pass

    logger.info(
        "Campus leader %s provisioned as admin in %s",
        user.username,
        campus_church.slug,
    )
    return campus_member


# ── HQ override enforcement ───────────────────────────────────────────────────


def apply_hq_overrides(campus_church, override_payload: dict):
    """
    HQ admin pushes restrictions down to a campus-church.

    override_payload keys (all optional):
        disable_modules:        list[str]
        read_only:              bool
        restrict_member_create: bool
        custom_message:         str
    """
    campus_church.hq_override = override_payload
    campus_church.save(update_fields=["hq_override", "updated_at"])

    campus_record = getattr(campus_church, "campus_record", None)
    if campus_record:
        wc = dict(campus_record.workspace_config or {})
        wc["hq_override"] = override_payload
        campus_record.workspace_config = wc
        campus_record.save(update_fields=["workspace_config"])
