"""
accounts/member_service.py

Utilities and guest-conversion helpers for member creation.

What lives here:
    - generate_temp_password()     — credential utility, used by forms + pipeline
    - generate_username()          — credential utility, used by forms + pipeline
    - promote_guest_to_member()    — converts a GuestEntry to a ChurchMember;
                                     has no form equivalent since it derives
                                     profile data from the guest record

What does NOT live here:
    - create_member() — removed. CustomUserCreationForm.save() handles the
      full admin-driven member creation flow including credentials, ChurchMember,
      WorkforceMember, roles, and unit assignments. Use the form instead.

The guest-to-workforce pipeline (guests/pipeline.py) calls
promote_guest_to_member() from induct_member() as its final step.
"""

import random
import string

from django.db import transaction
from accounts.username_utils import derive_username_base, format_username


# ─────────────────────────────────────────────────────────────────────────────
# Credential utilities
# ─────────────────────────────────────────────────────────────────────────────

def generate_temp_password(length=10):
    """
    Generate a readable temporary password.
    Avoids visually ambiguous characters (0, O, l, 1, I) for easy sharing
    verbally or via WhatsApp.
    """
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    return "".join(random.choices(chars, k=length))


def generate_username(full_name: str, church_slug: str = ""):
    """
    Derive a unique username from full name.
    Format: firstname.lastname  (e.g. john.doe)
    Falls back to firstname only if single name given.
    Appends a counter on collision.
    """
    from accounts.models import CustomUser

    base = derive_username_base(full_name or "member")

    username = base
    counter = 1
    while CustomUser.objects.filter(username=username).exists():
        username = f"{base}{counter}"
        counter += 1

    return username


def generate_formatted_username(full_name: str, church):
    from accounts.models import CustomUser

    base = derive_username_base(full_name or "member")
    candidate_base = base
    counter = 1
    candidate = format_username(candidate_base, church=church)
    while CustomUser.objects.filter(username__iexact=candidate).exists():
        candidate_base = f"{base}{counter}"
        candidate = format_username(candidate_base, church=church)
        counter += 1
    return candidate


# ─────────────────────────────────────────────────────────────────────────────
# Guest conversion
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def manual_promote_guest_to_member(
    *,
    guest_entry,
    church,
    workforce_stage=None,
    workforce_role=None,
    unit=None,
    unit_role=None,
    created_by,
):
    """
    Convert a GuestEntry into a ChurchMember + WorkforceMember.

    Used by the induction pipeline (guests/pipeline.py:induct_member) as
    its final step. Can also be called directly for manual admin overrides
    when a guest skips the full induction process.

    Derives profile data (name, phone, email) from the guest record so
    admins don't have to re-enter information already captured at guest
    registration.

    Returns the same dict as CustomUserCreationForm.save() would:
        {user, member, workforce_member, username, temp_password}

    The caller (induct_member or the admin view) is responsible for
    sharing the credentials with the new member.
    """
    from accounts.models import CustomUser, ChurchMember
    from workforce.models import (
        WorkforceMember,
        WorkforceMembershipRole,
        WorkforceStage,
    )
    from units.models import UnitMembership
    from permissions.models import MembershipRole
    from django.contrib.auth.hashers import make_password

    full_name = getattr(guest_entry, "full_name", "") or ""
    phone = getattr(guest_entry, "phone", None)
    email = getattr(guest_entry, "email", None) or ""

    # ── Guard: already has an account ────────────────────────────────
    if email and CustomUser.objects.filter(email__iexact=email).exists():
        raise ValueError(
            f"An account with email '{email}' already exists. "
            "This guest may already be a member."
        )

    # ── Create user ───────────────────────────────────────────────────
    username = generate_formatted_username(full_name, church)
    temp_password = generate_temp_password()

    user = CustomUser.objects.create_user(
        username=username,
        email=email,
        password=temp_password,
        full_name=full_name,
        phone_number=phone,
        is_active=True,
    )

    # ── ChurchMember ──────────────────────────────────────────────────
    member = ChurchMember.raw_objects.create(
        church=church,
        user=user,
        is_active=True,
    )

    # ── WorkforceMember ───────────────────────────────────────────────
    stage = workforce_stage or WorkforceStage.raw_objects.filter(
        church=church,
        is_active=True,
    ).order_by("order").first()

    workforce_member = None

    if stage:
        workforce_member = WorkforceMember.raw_objects.create(
            church=church,
            member=member,
            stage=stage,
            is_active=True,
        )

        if workforce_role:
            WorkforceMembershipRole.raw_objects.create(
                church=church,
                workforce_member=workforce_member,
                role=workforce_role,
            )

        if unit:
            membership = UnitMembership.raw_objects.create(
                church=church,
                unit=unit,
                workforce_member=workforce_member,
                is_active=True,
            )
            if unit_role:
                MembershipRole.raw_objects.create(
                    church=church,
                    membership=membership,
                    role=unit_role,
                )

    # ── Link guest record to member for history ───────────────────────
    # Only if GuestEntry has a converted_to FK (add if not present)
    if hasattr(guest_entry, "converted_to"):
        guest_entry.converted_to = member
        guest_entry.save(update_fields=["converted_to"])

    return {
        "user": user,
        "member": member,
        "workforce_member": workforce_member,
        "username": username,
        "temp_password": temp_password,
    }
