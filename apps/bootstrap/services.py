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

GLOBAL_ADMIN_PERMISSION_KEYS = [
    "dashboard.admin",
    "accounts.manage_users",
    "attendance.view_all",
    "events.manage_all",
    "events.view_all",
    "team.view_all",
    "messaging.send",
    "messaging.send_bulk",
    "messaging.view_log",
]

GLOBAL_ADMIN_PERMISSIONS = {key: True for key in GLOBAL_ADMIN_PERMISSION_KEYS}

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
        ("New", 1),
        ("Active", 2),
        ("Leader", 3),
    ],
    "workforce_roles": [
        ("Admin", True, 1, GLOBAL_ADMIN_PERMISSIONS),
        ("Worker", False, 2, {}),
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
    for name, is_leadership, order, permissions in DEFAULT_TEMPLATE_DATA["workforce_roles"]:
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
            "is_terminal": True,   # inducted — pipeline complete
        },
        {
            "name": "Not Planted",
            "slug": GuestStatus.SLUG_NOT_PLANTED,
            "color": "red",
            "order": 5,
            "is_default": False,
            "is_terminal": True,   # opted out / dormant — stops progression
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
    for stage in template.workforce_stages.all():
        WorkforceStage.raw_objects.get_or_create(
            church=church,
            name=stage.name,
            defaults={"order": stage.order, "is_active": True},
        )

    # ── Workforce roles ───────────────────────────────────────────────
    for role in template.workforce_roles.all():
        WorkforceRole.raw_objects.get_or_create(
            church=church,
            name=role.name,
            defaults={
                "permissions": role.permissions,
                "is_leadership": role.is_leadership,
                "order": role.order,
                "is_active": True,
            },
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
            ("Membership orientation class",  True,  1),
            ("One-on-one with pastor/leader", True,  2),
            ("Membership interview",          True,  3),
            ("Induction Sunday",              False, 4),
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
        lms_course, lms_created = LMSCourse.raw_objects.get_or_create(
            church=church,
            course_type="induction",
            membership_track=track,
            defaults={
                "title":          f"{church.name} New Member Induction",
                "description":    "Complete this course to become a full workforce member.",
                "delivery_mode":  "physical",
                "passing_score":  70,
                "strict_sequence": True,
                "is_active":       True,
            },
        )
        if lms_created:
            DEFAULT_MODULES = [
                ("Welcome & Church Vision",           "text",       1, True,  False),
                ("Church Values & Culture",           "text",       2, True,  False),
                ("Attendance & Commitment Policy",    "text",       3, True,  False),
                ("Induction Assessment",              "assessment", 4, True,  True),
            ]
            for title, ctype, order, required, attach in DEFAULT_MODULES:
                LMSModule.raw_objects.get_or_create(
                    church=church,
                    course=lms_course,
                    title=title, # Title should be in the lookup to avoid duplicates
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
        from units.models import Campus
        hq_campus, _ = Campus.raw_objects.get_or_create(
            church=church,
            slug="hq",
            defaults={
                "name": f"{church.name} (HQ)",
                "growth_stage": "headquarters",
                "is_active": True,
                "contributes_to_parent_metrics": False,
            },
        )
    except Exception:
        hq_campus = None

    # -- Final wiring: Admin membership, chat rooms, and campus link -----
    from accounts.models import ChurchMember
    from units.models import ChatRoom, UnitMembership, CampusMembership
    from workforce.models import WorkforceMember, WorkforceMembershipRole

    if not admin_member and admin_user:
        admin_member = ChurchMember.raw_objects.filter(
            church=church, user=admin_user, is_active=True
        ).first()

    if not admin_member:
        admin_member = ChurchMember.raw_objects.filter(
            church=church, is_admin=True, is_active=True
        ).first()

    if admin_member:
        # Ensure workforce member exists for permission resolver
        stage = WorkforceStage.raw_objects.filter(
            church=church, is_active=True
        ).order_by("order", "id").first()
        if not stage:
            stage = WorkforceStage.raw_objects.create(
                church=church, name="Active", order=1, is_active=True
            )

        wf_member, _ = WorkforceMember.raw_objects.get_or_create(
            church=church,
            member=admin_member,
            defaults={"stage": stage},
        )

        # Ensure an admin workforce role exists and assign it
        admin_role = WorkforceRole.raw_objects.filter(
            church=church, name__iexact="admin"
        ).first()
        if not admin_role:
            admin_role = WorkforceRole.raw_objects.filter(
                church=church, is_leadership=True
            ).order_by("order", "id").first()
        if not admin_role:
            admin_role, _ = WorkforceRole.raw_objects.get_or_create(
                church=church,
                name="Admin",
                defaults={
                    "permissions": GLOBAL_ADMIN_PERMISSIONS,
                    "is_leadership": True,
                    "order": 1,
                    "is_active": True,
                },
            )

        WorkforceMembershipRole.raw_objects.get_or_create(
            church=church, workforce_member=wf_member, role=admin_role
        )

        # Link Admin to HQ campus
        if hq_campus:
            CampusMembership.raw_objects.get_or_create(
                church=church,
                campus=hq_campus,
                member=admin_member,
                defaults={"is_primary": True},
            )

        # Create ChatRooms for each unit and auto-join admin
        for unit_obj in unit_map.values():
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












