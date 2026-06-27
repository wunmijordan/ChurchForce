from django.db import transaction
from django.utils.text import slugify

from guests.models import (
    GuestStatus,
    VisitPurpose,
    VisitChannel,
    ChurchService,
    MembershipTrack,
    MembershipTrackStep,
)
from units.models import ChurchUnit
from workforce.models import WorkforceStage, WorkforceRole
from permissions.models import UnitRole

from permissions.registry import (
    permissions_for_tier as _perms_for_tier,
    TIER_ADMIN as _TIER_ADMIN,
)

# Legacy constant — kept for template data compatibility.
# The authoritative source is permissions/registry.py.
GLOBAL_ADMIN_PERMISSION_KEYS = list(_perms_for_tier(_TIER_ADMIN).keys())
GLOBAL_ADMIN_PERMISSIONS = _perms_for_tier(_TIER_ADMIN)

DEFAULT_TEMPLATE_DATA = {
    "name": "Default System Template",
    "description": "System defaults for new churches.",
    "services": [
        ("Sunday Service", 1),
        ("Midweek Recharge", 2),
        ("Special Programme", 3),
        ("Friday Night", 4),
    ],
    "purposes": [
        ("Spiritual Growth", 1),
        ("Connect with God", 2),
        ("Check it out", 3),
        ("Friend invited me", 4),
    ],
    "channels": [
        ("Social Media", 1),
        ("Friend", 2),
        ("Family", 3),
        ("Walk-in", 4),
        ("Online Service", 5),
    ],
    "workforce_stages": [
        ("Probationer", 1),
        ("Active", 2),
        ("Leader", 3),
    ],
    "workforce_roles": [
        ("Pastor", True, 1, GLOBAL_ADMIN_PERMISSIONS),
        ("Admin", True, 2, GLOBAL_ADMIN_PERMISSIONS),
        ("Member", False, 4, {}),
    ],
}


def ensure_default_template():
    """
    Ensure a system default ChurchTemplate exists with baseline lookup data.
    Returns the template (existing or newly created).
    """
    from bootstrap.models import (
        ChurchTemplate,
        TemplateService,
        TemplateVisitPurpose,
        TemplateVisitChannel,
        TemplateWorkforceStage,
        TemplateWorkforceRole,
    )

    template = ChurchTemplate.objects.filter(is_default=True, is_active=True).first()
    if not template:
        template, _ = ChurchTemplate.objects.get_or_create(
            name=DEFAULT_TEMPLATE_DATA["name"],
            defaults={
                "description": DEFAULT_TEMPLATE_DATA["description"],
                "is_default": True,
                "is_system": True,
                "is_active": True,
            },
        )
        if not template.is_default:
            template.is_default = True
            template.is_system = True
            template.is_active = True
            template.save(update_fields=["is_default", "is_system", "is_active"])

    # Services
    for name, order in DEFAULT_TEMPLATE_DATA["services"]:
        TemplateService.objects.get_or_create(
            template=template,
            name=name,
            defaults={"order": order},
        )

    # Visit purposes
    for name, order in DEFAULT_TEMPLATE_DATA["purposes"]:
        TemplateVisitPurpose.objects.get_or_create(
            template=template,
            name=name,
            defaults={"order": order},
        )

    # Visit channels
    for name, order in DEFAULT_TEMPLATE_DATA["channels"]:
        TemplateVisitChannel.objects.get_or_create(
            template=template,
            name=name,
            defaults={"order": order},
        )

    # Workforce stages
    for name, order in DEFAULT_TEMPLATE_DATA["workforce_stages"]:
        TemplateWorkforceStage.objects.get_or_create(
            template=template,
            name=name,
            defaults={"order": order},
        )

    # Workforce roles
    for name, is_leadership, order, permissions in DEFAULT_TEMPLATE_DATA[
        "workforce_roles"
    ]:
        TemplateWorkforceRole.objects.get_or_create(
            template=template,
            name=name,
            defaults={
                "is_leadership": is_leadership,
                "order": order,
                "permissions": permissions,
            },
        )

    return template


def _ensure_admin_bootstrap(
    church,
    *,
    admin_user=None,
    admin_member=None,
    hq_campus=None,
):
    """
    Idempotently wire the founding tenant admin into the tier-1 permission path.

    This runs both during full template application and after signup flows where
    the church may already have been seeded by a post-save signal.
    """
    from accounts.models import ChurchMember
    from tenants.models import CampusMembership
    from units.models import ChatRoom, ChurchUnit, UnitMembership
    from workforce.models import WorkforceMember, WorkforceMembershipRole

    if not admin_member and admin_user:
        admin_member = ChurchMember.raw_objects.filter(
            church=church,
            user=admin_user,
            is_active=True,
        ).first()

    if not admin_member:
        admin_member = ChurchMember.raw_objects.filter(
            church=church,
            is_admin=True,
            is_active=True,
        ).first()

    if not admin_member:
        return

    if not admin_member.is_active or not admin_member.is_admin:
        admin_member.is_active = True
        admin_member.is_admin = True
        admin_member.save(update_fields=["is_active", "is_admin", "updated_at"])

    stage = (
        WorkforceStage.raw_objects.filter(
            church=church,
            slug=WorkforceStage.SLUG_ACTIVE,
            is_active=True,
        ).first()
        or WorkforceStage.raw_objects.filter(church=church, is_active=True)
        .order_by("order", "id")
        .first()
    )
    if not stage:
        stage = WorkforceStage.raw_objects.create(
            church=church,
            name="Active",
            slug=WorkforceStage.SLUG_ACTIVE,
            order=2,
            is_active=True,
        )

    wf_member, created = WorkforceMember.raw_objects.get_or_create(
        church=church,
        member=admin_member,
        defaults={"stage": stage, "is_active": True},
    )
    if not created:
        changed = []
        if not wf_member.is_active:
            wf_member.is_active = True
            changed.append("is_active")
        if not wf_member.stage and stage:
            wf_member.stage = stage
            changed.append("stage")
        if changed:
            wf_member.save(update_fields=[*changed, "updated_at"])

    from permissions.registry import TIER_ADMIN, permissions_for_tier

    WorkforceRole.raw_objects.get_or_create(
        church=church,
        name="Pastor",
        defaults={
            "tier": TIER_ADMIN,
            "scope": "global",
            "permissions": permissions_for_tier(TIER_ADMIN),
            "is_leadership": True,
            "order": 1,
            "is_active": True,
        },
    )
    admin_role, _ = WorkforceRole.raw_objects.get_or_create(
        church=church,
        name="Admin",
        defaults={
            "tier": TIER_ADMIN,
            "scope": "global",
            "permissions": permissions_for_tier(TIER_ADMIN),
            "is_leadership": True,
            "order": 2,
            "is_active": True,
        },
    )
    if not admin_role.is_active:
        admin_role.is_active = True
        admin_role.save(update_fields=["is_active", "updated_at"])

    WorkforceMembershipRole.raw_objects.get_or_create(
        church=church,
        workforce_member=wf_member,
        role=admin_role,
    )

    if hq_campus:
        CampusMembership.raw_objects.get_or_create(
            church=church,
            campus=hq_campus,
            member=admin_member,
            defaults={"is_primary": True},
        )

    for unit_obj in ChurchUnit.raw_objects.filter(church=church, is_active=True):
        ChatRoom.raw_objects.get_or_create(
            church=church,
            unit=unit_obj,
            defaults={"name": unit_obj.name, "is_default": True, "is_active": True},
        )
        UnitMembership.raw_objects.get_or_create(
            church=church,
            unit=unit_obj,
            workforce_member=wf_member,
            defaults={"is_active": True},
        )


def apply_template_to_church(template, church, *, admin_user=None, admin_member=None):

    # ── Guest statuses ────────────────────────────────────────────────
    # The five pipeline statuses are always seeded regardless of whether
    # they exist in the template, so the pipeline always has something
    # to work with. Template statuses are merged on top.

    PIPELINE_STATUSES = [
        {
            "name": "New Guest",
            "slug": GuestStatus.SLUG_NEW_GUEST,
            "color": "blue",
            "order": 1,
            "is_default": True,
            "is_terminal": False,
        },
        {
            "name": "In-Contact",
            "slug": GuestStatus.SLUG_IN_CONTACT,
            "color": "cyan",
            "order": 2,
            "is_default": False,
            "is_terminal": False,
        },
        {
            "name": "Committed",
            "slug": GuestStatus.SLUG_COMMITTED,
            "color": "orange",
            "order": 3,
            "is_default": False,
            "is_terminal": False,
        },
        {
            "name": "Planted",
            "slug": GuestStatus.SLUG_PLANTED,
            "color": "green",
            "order": 4,
            "is_default": False,
            "is_terminal": True,  # inducted — pipeline complete
        },
        {
            "name": "Not Planted",
            "slug": GuestStatus.SLUG_NOT_PLANTED,
            "color": "red",
            "order": 5,
            "is_default": False,
            "is_terminal": True,  # opted out / dormant — stops progression
        },
    ]

    seeded_slugs = set()

    for s in PIPELINE_STATUSES:
        GuestStatus.raw_objects.get_or_create(
            church=church,
            slug=s["slug"],
            defaults={
                "name": s["name"],
                "color": s["color"],
                "order": s["order"],
                "is_default": s["is_default"],
                "is_terminal": s["is_terminal"],
                "is_active": True,
            },
        )
        seeded_slugs.add(s["slug"])

    # Additional statuses from the template (church-custom, non-pipeline)
    for status in template.guest_statuses.all():
        slug = slugify(status.name)
        if slug in seeded_slugs:
            continue  # don't overwrite pipeline statuses
        GuestStatus.raw_objects.get_or_create(
            church=church,
            slug=slug,
            defaults={
                "name": status.name,
                "color": status.color,
                "order": status.order + 10,  # place after pipeline statuses
                "is_default": status.is_default,
                "is_terminal": False,
                "is_active": True,
            },
        )

    # ── Services ──────────────────────────────────────────────────────
    for service in template.services.all():
        ChurchService.raw_objects.get_or_create(
            church=church,
            name=service.name,
            defaults={"order": service.order, "is_active": True},
        )

    # ── Visit purposes ────────────────────────────────────────────────
    for purpose in template.purposes.all():
        VisitPurpose.raw_objects.get_or_create(
            church=church,
            name=purpose.name,
            defaults={"order": purpose.order, "is_active": True},
        )

    # ── Visit channels ────────────────────────────────────────────────
    for channel in template.channels.all():
        VisitChannel.raw_objects.get_or_create(
            church=church,
            name=channel.name,
            defaults={"order": channel.order, "is_active": True},
        )

    # ── Units ─────────────────────────────────────────────────────────
    unit_map = {}
    for unit in template.units.all():
        obj, _ = ChurchUnit.raw_objects.get_or_create(
            church=church,
            name=unit.name,
            defaults={
                "description": getattr(unit, "description", ""),
                "is_active": True,
            },
        )
        unit_map[unit.name] = obj

    # ── Workforce stages ──────────────────────────────────────────────
    # Three system stages, identified by slug. Order 0-2 reserved.
    # Custom stages start at order 3.
    #
    # "Inductee" is NOT seeded here — it belongs exclusively to
    # WorkforceTraineeProfile (the guest→workforce pipeline).
    # A trainee becomes a WorkforceMember at Probationer stage only
    # after completing the LMS induction course.
    #
    # Probationer — first real workforce stage. Default for new WF members
    #               auto-promoted from the trainee pipeline, AND used for
    #               disciplinary demotion.
    # Active      — full active member; auto-set when probation clears.
    # Leader      — manually assigned by admin.

    SYSTEM_STAGES = [
        # (slug, name, order, is_default, is_locked, is_order_locked, description)
        (
            "probationer",
            "Probationer",
            0,
            True,
            True,
            True,
            "First workforce stage. Auto-assigned on promotion from trainee pipeline, "
            "or used for disciplinary demotion by admin.",
        ),
        (
            "active",
            "Active",
            1,
            False,
            True,
            True,
            "Full active workforce member — reached automatically when probation period ends.",
        ),
        (
            "leader",
            "Leader",
            2,
            False,
            True,
            False,
            "Manually assigned by admin for members who qualify for a leadership role.",
        ),
    ]

    for (
        slug,
        name,
        order,
        is_default,
        is_locked,
        is_order_locked,
        description,
    ) in SYSTEM_STAGES:
        existing = WorkforceStage.raw_objects.filter(church=church, slug=slug).first()
        if not existing:
            WorkforceStage.raw_objects.create(
                church=church,
                name=name,
                slug=slug,
                order=order,
                is_default=is_default,
                is_locked=is_locked,
                is_order_locked=is_order_locked,
                description=description,
                is_active=True,
            )

    # Custom stages from template (order starts at 4 to sit after system stages)
    for stage in template.workforce_stages.all():
        if not WorkforceStage.raw_objects.filter(
            church=church, name__iexact=stage.name
        ).exists():
            WorkforceStage.raw_objects.create(
                church=church,
                name=stage.name,
                slug="",
                order=stage.order + 3,
                is_default=False,
                is_locked=False,
                is_order_locked=False,
                is_active=True,
            )

    # ── Workforce roles ───────────────────────────────────────────────
    # We seed the complete 5-tier hierarchy so every church starts with
    # a clean, configurable permission structure.
    # Admins can rename roles and customise individual permission keys
    # in Settings → Workforce. The tier field remains authoritative.
    #
    # Tier 0 (Superuser) is Django-level — never seeded here.
    # "Member" is the locked base role all workforce members receive.

    from permissions.registry import (
        TIER_ADMIN,
        TIER_SUB_ADMIN,
        TIER_ASSISTANT_PASTOR,
        TIER_CUSTOM,
        permissions_for_tier,
    )

    SYSTEM_WORKFORCE_ROLES = [
        # (name, tier, scope, is_leadership, is_default, is_locked, order)
        # Pastor: Tier 1 — most powerful per-tenant role. Full church access.
        ("Pastor", TIER_ADMIN, "global", True, False, False, 1),
        ("Admin", TIER_ADMIN, "global", True, False, False, 2),
        # Assistant Pastor: Tier 3 — church-wide oversight, below Sub-Admin.
        # Sits between Sub-Admin and Overseer; global scope, no admin powers.
        ("Assistant Pastor", TIER_ASSISTANT_PASTOR, "global", True, False, False, 3),
        ("Member", TIER_CUSTOM, "unit", False, True, True, 4),
    ]

    for (
        r_name,
        r_tier,
        r_scope,
        r_lead,
        r_default,
        r_locked,
        r_order,
    ) in SYSTEM_WORKFORCE_ROLES:
        existing = WorkforceRole.raw_objects.filter(church=church, name=r_name).first()
        if not existing:
            WorkforceRole.raw_objects.create(
                church=church,
                name=r_name,
                tier=r_tier,
                scope=r_scope,
                permissions=permissions_for_tier(r_tier),
                is_leadership=r_lead,
                is_default=r_default,
                is_locked=r_locked,
                order=r_order,
                is_active=True,
            )
        else:
            # Backfill tier/scope if this is an existing install without them
            changed = False
            if (
                not hasattr(existing, "tier")
                or existing.tier == TIER_CUSTOM
                and r_tier != TIER_CUSTOM
            ):
                existing.tier = r_tier
                changed = True
            # Only backfill scope if the target scope is "global" and the role
            # is a privileged tier (1-3). Never force-upgrade tier-7 to global.
            from permissions.registry import TIER_ASSISTANT_PASTOR

            if not hasattr(existing, "scope") or (
                existing.scope == "unit"
                and r_scope == "global"
                and r_tier <= TIER_ASSISTANT_PASTOR
            ):
                existing.scope = r_scope
                changed = True
            if changed:
                existing.save(update_fields=["tier", "scope"])

    # Also process any additional roles from the template (custom church roles)
    for role in template.workforce_roles.all():
        # Skip if already seeded above
        if WorkforceRole.raw_objects.filter(church=church, name=role.name).exists():
            continue
        role_tier = getattr(role, "tier", TIER_CUSTOM)
        WorkforceRole.raw_objects.create(
            church=church,
            name=role.name,
            tier=role_tier,
            # Only tiers 1-3 get global scope; custom roles are unit-scoped
            scope="global" if role_tier <= TIER_ASSISTANT_PASTOR else "unit",
            permissions=role.permissions or {},
            is_leadership=role.is_leadership,
            order=role.order + 10,
            is_active=True,
        )

    # ── Unit roles ────────────────────────────────────────────────────
    for role in template.unit_roles.all():
        unit = unit_map.get(role.unit_name)
        if not unit:
            continue
        UnitRole.raw_objects.get_or_create(
            church=church,
            unit=unit,
            name=role.name,
            defaults={
                "permissions": role.permissions,
                "is_leadership": role.is_leadership,
                "order": role.order,
                "is_active": True,
            },
        )

    # ── Default membership track ──────────────────────────────────────
    # Seed a default track so the pipeline works out of the box.
    # Churches can adjust threshold/window in Settings → Membership Tracks.
    track, created = MembershipTrack.raw_objects.get_or_create(
        church=church,
        name="General Membership",
        defaults={
            "description": "Default pathway from committed guest to workforce member.",
            "attendance_threshold": 4,
            "attendance_window_weeks": 6,
            "is_default": True,
            "is_active": True,
        },
    )

    if created:
        DEFAULT_STEPS = [
            ("Membership orientation class", True, 1),
            ("One-on-one with pastor/leader", True, 2),
            ("Membership interview", True, 3),
            ("Induction Sunday", False, 4),
        ]
        for name, required, order in DEFAULT_STEPS:
            MembershipTrackStep.raw_objects.create(
                church=church,
                track=track,
                name=name,
                is_required=required,
                order=order,
                is_active=True,
            )

    # ── Default LMS induction course ──────────────────────────────────
    # Seed a basic induction LMS course linked to the default track.
    try:
        from lms.models import LMSCourse, LMSModule

        from django.utils.text import slugify as _slugify

        _lms_title = f"{church.name} New Member Induction"
        _lms_slug = _slugify(_lms_title)[:100] or "new-member-induction"

        lms_course, lms_created = LMSCourse.raw_objects.get_or_create(
            church=church,
            course_type="induction",
            membership_track=track,
            defaults={
                "title": _lms_title,
                "slug": _lms_slug,
                "description": "Complete this course to become a full workforce member.",
                "delivery_mode": "physical",
                "passing_score": 70,
                "strict_sequence": True,
                "is_active": True,
            },
        )
        # Back-fill slug if this is an existing course without one
        if not lms_course.slug:
            lms_course.slug = _lms_slug
            lms_course.save(update_fields=["slug"])
        if lms_created:
            DEFAULT_MODULES = [
                ("Welcome & Church Vision", "text", 1, True, False),
                ("Church Values & Culture", "text", 2, True, False),
                ("Attendance & Commitment Policy", "text", 3, True, False),
                ("Induction Assessment", "assessment", 4, True, True),
            ]
            for title, ctype, order, required, attach in DEFAULT_MODULES:
                LMSModule.raw_objects.get_or_create(
                    church=church,
                    course=lms_course,
                    title=title,  # Title should be in the lookup to avoid duplicates
                    defaults={
                        "content_type": ctype,
                        "order": order,
                        "is_required": required,
                        "requires_attachment": attach,
                        "is_active": True,
                    },
                )
    except Exception:
        pass  # LMS app may not be installed

    # ── Default HQ campus
    # Seed a headquarter campus so the campus model is populated by default.
    try:
        from tenants.models import Campus

        hq_campus, _ = Campus.raw_objects.get_or_create(
            church=church,
            slug="hq",
            defaults={
                "name": f"{church.name} (HQ)",
                "growth_stage": "Headquarters",
                "is_active": True,
                "contributes_to_parent_metrics": False,
            },
        )
    except Exception:
        hq_campus = None

    _ensure_admin_bootstrap(
        church,
        admin_user=admin_user,
        admin_member=admin_member,
        hq_campus=hq_campus,
    )

    # ── Seed baseline music content for current plan ─────────────────
    # Bible passage seeding has been removed — bible content is handled
    # entirely by the ai_skills system, not bootstrap.
    try:
        from music.services.catalog import seed_tracks_for_plan

        plan_name = "trial"
        sub = getattr(church, "churchsubscription", None)
        if sub and sub.plan:
            plan_name = sub.plan.name

        seed_tracks_for_plan(church, plan_name)
    except Exception:
        # Never block bootstrap over optional starter-content warmup.
        pass


def seed_church(church, *, admin_user=None):
    """
    Wrapper for the reset script to apply the default template
    to a newly created church.
    """
    from bootstrap.models import ChurchTemplate

    # 1. Try to get the template assigned to the church
    template = church.template

    # 2. Fallback: Get the first available template if none assigned
    if not template:
        template = (
            ChurchTemplate.objects.filter(is_active=True).first()
            or ensure_default_template()
        )

    if not template:
        print(f"  [!] No active ChurchTemplate found to seed {church.name}")
        return

    return apply_template_to_church(template, church, admin_user=admin_user)
