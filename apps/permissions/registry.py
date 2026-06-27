"""
permissions/registry.py — Single source of truth for all permission keys.

PERMISSION TIERS
────────────────
  0  SUPERUSER         Global across all tenants (Django is_superuser).
  1  CHURCH_ADMIN      Sovereign over entire church. Set via
                       ChurchMember.is_admin=True OR WorkforceRole.tier==1.
  2  SUB_ADMIN         Cross-unit operator — church-wide but below Admin.
  3  ASSISTANT_PASTOR  Pastoral assistant — church-wide visibility & oversight,
                       sits just below Sub-Admin, no admin powers.
  4  OVERSEER          Supervisory over assigned unit(s)/group(s) — midpoint
                       between global tiers and unit-level tiers. Has oversight
                       privileges over one or more units without global access.
  5  UNIT_HEAD         Full control within their assigned unit(s)/group(s).
  6  ASSISTANT         Read + limited write — assists the unit head.
  7  CUSTOM            Ad-hoc permissions for specific tasks/activities.

SCOPE
─────
  "global"  — permission applies church-wide regardless of unit.
  "unit"    — permission applies only to the roles' assigned unit(s).

GLOBAL BLEED PREVENTION
───────────────────────
Only WorkforceRole with scope="global" AND tier <= TIER_ASSISTANT_PASTOR (3)
can set global=True on a permission. Unit roles (tiers 4-7) are strictly
unit-scoped — any global-scope key in their permissions JSONField is silently
dropped by the resolver.
"""

# ── Tier constants ─────────────────────────────────────────────────────────────
TIER_SUPERUSER = 0
TIER_ADMIN = 1
TIER_SUB_ADMIN = 2
TIER_ASSISTANT_PASTOR = 3  # NEW — sits between Sub-Admin and Overseer
TIER_OVERSEER = 4
TIER_UNIT_HEAD = 5
TIER_ASSISTANT = 6
TIER_CUSTOM = 7

TIER_LABELS = {
    TIER_SUPERUSER: "Superuser",
    TIER_ADMIN: "Church Admin",
    TIER_SUB_ADMIN: "Sub-Admin",
    TIER_ASSISTANT_PASTOR: "Assistant Pastor",
    TIER_OVERSEER: "Overseer",
    TIER_UNIT_HEAD: "Unit / Group Head",
    TIER_ASSISTANT: "Assistant Head",
    TIER_CUSTOM: "Custom Role",
}

# Used in WorkforceRole field choices (global-scope roles only)
WORKFORCE_TIER_CHOICES = [
    (TIER_ADMIN, TIER_LABELS[TIER_ADMIN]),
    (TIER_SUB_ADMIN, TIER_LABELS[TIER_SUB_ADMIN]),
    (TIER_ASSISTANT_PASTOR, TIER_LABELS[TIER_ASSISTANT_PASTOR]),
    (TIER_CUSTOM, TIER_LABELS[TIER_CUSTOM]),
]

# Used in UnitRole field choices (unit-scoped roles only)
UNIT_TIER_CHOICES = [
    (TIER_OVERSEER, TIER_LABELS[TIER_OVERSEER]),
    (TIER_UNIT_HEAD, TIER_LABELS[TIER_UNIT_HEAD]),
    (TIER_ASSISTANT, TIER_LABELS[TIER_ASSISTANT]),
    (TIER_CUSTOM, TIER_LABELS[TIER_CUSTOM]),
]

# ── Permission registry ────────────────────────────────────────────────────────
# min_tier: the least-powerful tier that receives this permission by default.
# (lower number = more powerful; "min_tier=1" means only Admins get it)
# Superuser (0) always gets everything — not listed here.

PERMISSIONS = {
    # ── Dashboard ─────────────────────────────────────────────────────────
    "dashboard.view": {
        "min_tier": TIER_CUSTOM,
        "scope": "global",
        "label": "View Dashboard",
        "description": "Access the main dashboard page.",
        "category": "Dashboard",
    },
    "dashboard.admin": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Admin Dashboard",
        "description": "Access the full admin dashboard and settings.",
        "category": "Dashboard",
    },
    # ── Accounts / Members ────────────────────────────────────────────────
    "accounts.view_all_users": {
        "min_tier": TIER_ASSISTANT_PASTOR,
        "scope": "global",
        "label": "View All Members",
        "description": "See all workforce members church-wide.",
        "category": "Accounts",
    },
    "accounts.manage_users": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Manage Members",
        "description": "Create, edit, and deactivate member accounts.",
        "category": "Accounts",
    },
    # ── Units / Groups ────────────────────────────────────────────────────
    "unit.view_all": {
        "min_tier": TIER_ASSISTANT_PASTOR,
        "scope": "global",
        "label": "View All Units",
        "description": "See all units and groups church-wide.",
        "category": "Units",
    },
    "unit.manage": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Manage Units",
        "description": "Create, edit, and delete units and groups.",
        "category": "Units",
    },
    "unit.manage_members": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Manage Unit Members",
        "description": "Add/remove members within assigned unit(s).",
        "category": "Units",
    },
    "unit.set_head": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Set Unit Head",
        "description": "Designate unit heads and assistants.",
        "category": "Units",
    },
    "unit.report.submit": {
        "min_tier": TIER_CUSTOM,
        "scope": "unit",
        "label": "Submit Unit Reports",
        "description": "Submit service, task, and training reports within assigned unit/group.",
        "category": "Units",
    },
    "unit.report.review": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Review Unit Reports",
        "description": "Review and escalate reports. Overseers can do this across all supervised units.",
        "category": "Units",
    },
    # ── Guests ────────────────────────────────────────────────────────────
    "guests.view": {
        "min_tier": TIER_ASSISTANT,
        "scope": "unit",
        "label": "View Guests",
        "description": "View guests assigned to this unit.",
        "category": "Guests",
    },
    "guests.view_all": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "View All Guests",
        "description": "View all guests church-wide.",
        "category": "Guests",
    },
    "guests.create": {
        "min_tier": TIER_ASSISTANT,
        "scope": "unit",
        "label": "Create Guests",
        "description": "Register new guest entries.",
        "category": "Guests",
    },
    "guests.edit": {
        "min_tier": TIER_ASSISTANT,
        "scope": "unit",
        "label": "Edit Guests",
        "description": "Edit guest profile details for assigned guests.",
        "category": "Guests",
    },
    "guests.delete": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Delete Guests",
        "description": "Soft-delete guest records.",
        "category": "Guests",
    },
    "guests.assign_unit": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Assign Guests (Unit)",
        "description": "Assign guests to members within own unit.",
        "category": "Guests",
    },
    "guests.assign_all": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "Assign Guests (All)",
        "description": "Assign guests to any member church-wide.",
        "category": "Guests",
    },
    "guests.manage_unassigned": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "Manage Unassigned Guests",
        "description": "View and assign guests with no officer.",
        "category": "Guests",
    },
    "guests.manage_all": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Full Guest Management",
        "description": "Unrestricted guest operations church-wide.",
        "category": "Guests",
    },
    "guests.update_status": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Update Guest Status",
        "description": "Manually change a guest's pipeline status.",
        "category": "Guests",
    },
    "guests.report": {
        "min_tier": TIER_CUSTOM,
        "scope": "unit",
        "label": "Submit Follow-Up Report",
        "description": "Record a follow-up contact report for an assigned guest.",
        "category": "Guests",
    },
    "guests.review": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Make Guest Review",
        "description": "Submit a formal review on a guest's progress.",
        "category": "Guests",
    },
    "guests.export": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "Export Guests",
        "description": "Download guest data as Excel/CSV.",
        "category": "Guests",
    },
    "guests.import": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Import Guests",
        "description": "Bulk-import guest data.",
        "category": "Guests",
    },
    # ── Attendance ────────────────────────────────────────────────────────
    "attendance.view": {
        "min_tier": TIER_ASSISTANT,
        "scope": "unit",
        "label": "View Attendance",
        "description": "View attendance records for own unit.",
        "category": "Attendance",
    },
    "attendance.view_all": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "View All Attendance",
        "description": "View attendance records church-wide.",
        "category": "Attendance",
    },
    "attendance.mark": {
        "min_tier": TIER_ASSISTANT,
        "scope": "unit",
        "label": "Mark Attendance",
        "description": "Record attendance for members in own unit.",
        "category": "Attendance",
    },
    "attendance.manage_all": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Manage All Attendance",
        "description": "Edit, delete, and bulk-manage attendance records.",
        "category": "Attendance",
    },
    "attendance.clock": {
        "min_tier": TIER_CUSTOM,
        "scope": "unit",
        "label": "Clock In/Out",
        "description": "Use geo-fenced clock-in/out for service attendance.",
        "category": "Attendance",
    },
    # ── Events / Services ─────────────────────────────────────────────────
    "events.view_all": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "View All Events",
        "description": "See all events church-wide.",
        "category": "Events",
    },
    "events.manage_all": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Manage All Events",
        "description": "Create, edit, and delete events.",
        "category": "Events",
    },
    "events.manage_unit": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Manage Unit Events",
        "description": "Create and manage events for own unit.",
        "category": "Events",
    },
    # ── Training / LMS ────────────────────────────────────────────────────
    "training.view": {
        "min_tier": TIER_CUSTOM,
        "scope": "unit",
        "label": "View Trainings",
        "description": "View and enrol in available training programmes.",
        "category": "Training",
    },
    "training.manage": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Manage Unit Training",
        "description": "Create and manage trainings for own unit.",
        "category": "Training",
    },
    "training.manage_all": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Manage All Training",
        "description": "Full control over all training programmes.",
        "category": "Training",
    },
    "training.approve_absence": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Approve Attendance Leniency",
        "description": "Approve a member's request for lenient attendance.",
        "category": "Training",
    },
    "training.conduct_interview": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Conduct Interviews",
        "description": "Conduct and record training interviews.",
        "category": "Training",
    },
    # ── Workforce / Promotions ────────────────────────────────────────────
    "workforce.view": {
        "min_tier": TIER_ASSISTANT_PASTOR,
        "scope": "global",
        "label": "View Workforce",
        "description": "View workforce member profiles and stages.",
        "category": "Workforce",
    },
    "workforce.manage": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Manage Workforce",
        "description": "Create and edit workforce member records.",
        "category": "Workforce",
    },
    "workforce.promotions.review": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "Review Promotions",
        "description": "Review and action trainee promotion requests.",
        "category": "Workforce",
    },
    "workforce.promotions.approve": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Approve Promotions",
        "description": "Final approval of trainee promotions.",
        "category": "Workforce",
    },
    "workforce.disciplinary": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Disciplinary Actions",
        "description": "Demote members to probation and manage disciplinary workflows.",
        "category": "Workforce",
    },
    # ── Chat ──────────────────────────────────────────────────────────────
    "chat.view": {
        "min_tier": TIER_CUSTOM,
        "scope": "unit",
        "label": "View Chat",
        "description": "Access the chat room for assigned unit(s).",
        "category": "Chat",
    },
    "chat.view_all": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "View All Chats",
        "description": "Access chat rooms for all units.",
        "category": "Chat",
    },
    "chat.manage": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Manage Chat",
        "description": "Pin, delete, and moderate messages in own unit.",
        "category": "Chat",
    },
    # ── Messaging ─────────────────────────────────────────────────────────
    "messaging.send": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Send Messages",
        "description": "Send SMS or in-app notifications to unit members.",
        "category": "Messaging",
    },
    "messaging.send_bulk": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Send Bulk Messages",
        "description": "Broadcast messages to all members.",
        "category": "Messaging",
    },
    "messaging.view_log": {
        "min_tier": TIER_SUB_ADMIN,
        "scope": "global",
        "label": "View Message Log",
        "description": "Audit all sent messages.",
        "category": "Messaging",
    },
    # ── Music ─────────────────────────────────────────────────────────────
    "music.view": {
        "min_tier": TIER_CUSTOM,
        "scope": "unit",
        "label": "View Music Module",
        "description": "Access the music library, setlists and rehearsals for own unit.",
        "category": "Music",
    },
    "music.manage": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Manage Music",
        "description": "Create/edit tracks, chord charts, setlists and rehearsals.",
        "category": "Music",
    },
    "music.manage_roles": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Manage Music Roles",
        "description": "Assign and reassign functional roles (director, guitarist, etc.) within own unit.",
        "category": "Music",
    },
    "music.manage_all": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Manage All Music",
        "description": "Full control over the church-wide music library.",
        "category": "Music",
    },
    # ── Media ─────────────────────────────────────────────────────────────
    "media.view": {
        "min_tier": TIER_CUSTOM,
        "scope": "unit",
        "label": "View Media Module",
        "description": "Access presentations, slide templates and production schedules.",
        "category": "Media",
    },
    "media.manage": {
        "min_tier": TIER_UNIT_HEAD,
        "scope": "unit",
        "label": "Manage Media",
        "description": "Create/edit presentations, slides and production checklists.",
        "category": "Media",
    },
    "media.manage_all": {
        "min_tier": TIER_ADMIN,
        "scope": "global",
        "label": "Manage All Media",
        "description": "Full control over all media assets church-wide.",
        "category": "Media",
    },
    "media.present": {
        "min_tier": TIER_ASSISTANT,
        "scope": "unit",
        "label": "Control Live Presentation",
        "description": "Advance slides and control the live presenter view.",
        "category": "Media",
    },
}


# ── Helpers ────────────────────────────────────────────────────────────────────


def permissions_for_tier(tier: int, scope_filter: str = None) -> dict:
    """
    Return {key: True} for every permission this tier is entitled to.

    A role with tier=T gets all permissions where min_tier >= T
    (i.e. less powerful or equally-powerful tiers also hold this permission).

    Examples (lower tier = more powerful):
        permissions_for_tier(1)  → everything                — Admin
        permissions_for_tier(2)  → min_tier in [2..7]        — Sub-Admin
        permissions_for_tier(3)  → min_tier in [3..7]        — Assistant Pastor
        permissions_for_tier(4)  → min_tier in [4..7]        — Overseer
        permissions_for_tier(5)  → min_tier in [5..7]        — Unit Head
        permissions_for_tier(7)  → only min_tier==7 perms    — Custom
    """
    result = {}
    for key, meta in PERMISSIONS.items():
        if meta["min_tier"] < tier:
            continue
        if scope_filter and meta["scope"] != scope_filter:
            continue
        result[key] = True
    return result


def permissions_for_exact_tier(tier: int) -> dict:
    return {k: True for k, v in PERMISSIONS.items() if v["min_tier"] == tier}


def all_permission_keys() -> list:
    return list(PERMISSIONS.keys())


def permission_categories() -> dict:
    cats = {}
    for key, meta in PERMISSIONS.items():
        cats.setdefault(meta["category"], []).append(key)
    return cats


def get_meta(key: str) -> dict:
    return PERMISSIONS.get(key, {})
