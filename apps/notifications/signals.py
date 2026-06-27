"""
notifications/signals.py

Multi-tenant notification signals.

Recipient selection strategy:
    Old code used is_project_admin() / get_user_role() / UnitMembership
    to find recipients — all of which referenced deleted single-tenant models.

    New strategy:
        - Admins = members with "dashboard.admin" permission in the church
          (resolved via PermissionResolver or get_admin_members() helper)
        - Unit members = UnitMembership queryset scoped to the relevant unit
        - All recipients are ChurchMember objects, not CustomUser

    All signals are scoped to request.church via the core.request_context
    ContextVar set by ChurchContextMiddleware. If church context is missing
    (e.g. management commands), signals do nothing gracefully.
"""

import re
from datetime import date, datetime, time

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

from notifications.middleware import get_current_user
from notifications.utils import (
    get_admin_members,
    member_full_name,
    notify_members,
    user_full_name,
    guest_full_name,
)
from tenants.time_utils import format_church_datetime

User = get_user_model()


def _current_church():
    """Return church from current request context, or None."""
    from core.request_context import get_current_church

    return get_current_church()


def _member_for_user(user, church):
    """Return a ChurchMember for either a CustomUser or ChurchMember input."""
    if not user or not church:
        return None
    from accounts.models import ChurchMember

    if isinstance(user, ChurchMember):
        return user if user.church_id == church.id and user.is_active else None
    return ChurchMember.raw_objects.filter(
        church=church, user=user, is_active=True
    ).first()


# ─────────────────────────────────────────────────────────────────────────────
# Guest signals
# ─────────────────────────────────────────────────────────────────────────────


@receiver(pre_save, sender="guests.GuestEntry")
def cache_old_assignment(sender, instance, **kwargs):
    """Cache the current assigned_to before save so we can detect reassignment."""
    if instance.pk:
        try:
            old = sender.raw_objects.get(pk=instance.pk)
            instance._old_assigned_to = old.assigned_to
        except sender.DoesNotExist:
            instance._old_assigned_to = None
    else:
        instance._old_assigned_to = None


@receiver(post_save, sender="guests.GuestEntry")
def notify_guest_creation_or_assignment(sender, instance, created, **kwargs):
    church = instance.church
    if not church:
        return

    ts = format_church_datetime(timezone.localtime(), church)
    guest_name = guest_full_name(instance)
    custom_id = getattr(instance, "custom_id", "N/A")
    link = reverse("guests:guest_list")
    registrant = get_current_user()
    creator_name = user_full_name(
        _member_for_user(registrant, church) if registrant else None
    )
    old_assigned = getattr(instance, "_old_assigned_to", None)
    new_assigned = instance.assigned_to  # UnitMembership or None

    # Resolve ChurchMember for the new assignee
    new_assigned_member = None
    if new_assigned:
        try:
            new_assigned_member = new_assigned.workforce_member.member
        except AttributeError:
            pass

    admins = get_admin_members(church)

    if created:
        guest_count = sender.raw_objects.filter(church=church).count()
        msg = (
            f"{guest_name} ({custom_id})\n"
            f"Registered by: {creator_name}, at {ts}.\n"
            f"Guest count: {guest_count}."
        )
        if new_assigned_member:
            msg += f"\nAssigned to: {member_full_name(new_assigned_member)}."

        recipients = [a for a in admins if a != new_assigned_member]
        notify_members(recipients, "Guest Created", msg, church, link, is_success=True)

        if new_assigned_member:
            notify_members(
                [new_assigned_member],
                "Guest Assigned",
                f"I have been assigned: {guest_name} ({custom_id}), at {ts}.",
                church,
                link,
                is_success=True,
            )
        return

    # Reassignment
    if old_assigned != new_assigned:
        new_name = (
            member_full_name(new_assigned_member) if new_assigned_member else "no one"
        )
        others_msg = (
            f"{guest_name} ({custom_id}) has been reassigned to {new_name}, at {ts}."
        )
        admin_recipients = [a for a in admins if a != new_assigned_member]
        notify_members(
            admin_recipients,
            "Guest Reassigned",
            others_msg,
            church,
            link,
            is_urgent=True,
        )

        if new_assigned_member:
            notify_members(
                [new_assigned_member],
                "Guest Reassigned",
                f"I have been reassigned: {guest_name} ({custom_id}), at {ts}.",
                church,
                link,
                is_success=True,
            )


@receiver(post_delete, sender="guests.GuestEntry")
def notify_guest_deletion(sender, instance, **kwargs):
    church = instance.church
    if not church:
        return

    deleter = get_current_user()
    ts = format_church_datetime(timezone.localtime(), church)
    guest_name = guest_full_name(instance)
    deleter_name = user_full_name(
        _member_for_user(deleter, church) if deleter else None
    )
    custom_id = getattr(instance, "custom_id", "N/A")
    guest_count = sender.raw_objects.filter(church=church).count()
    link = reverse("guests:guest_list")

    description = (
        f"{guest_name} ({custom_id})\n"
        f"Deleted by: {deleter_name}, at {ts}.\n"
        f"Guest count: {guest_count}."
    )
    notify_members(
        get_admin_members(church),
        "Guest Deleted",
        description,
        church,
        link,
        is_urgent=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Review signals
# ─────────────────────────────────────────────────────────────────────────────


@receiver(post_save, sender="guests.Review")
def notify_review_submission(sender, instance, created, **kwargs):
    if not created:
        return

    church = instance.church
    if not church:
        return

    reviewer = instance.reviewer  # ChurchMember
    guest = instance.guest
    ts = format_church_datetime(timezone.localtime(), church)
    guest_name = guest_full_name(guest)
    link = reverse("guests:guest_list")

    admins = [a for a in get_admin_members(church) if a != reviewer]

    # Notify owner of the guest (if different from reviewer)
    owner_member = None
    if guest.assigned_to:
        try:
            owner_member = guest.assigned_to.workforce_member.member
        except AttributeError:
            pass

    recipients = list(
        {
            m.pk: m
            for m in admins
            + ([owner_member] if owner_member and owner_member != reviewer else [])
        }.values()
    )
    if recipients:
        notify_members(
            recipients,
            "Review Submitted",
            f"{member_full_name(reviewer)} submitted a review for {guest_name}, at {ts}.",
            church,
            link,
            is_success=True,
        )

    # Notify parent reviewer if this is a reply
    if instance.parent and instance.parent.reviewer != reviewer:
        notify_members(
            [instance.parent.reviewer],
            "Review Reply",
            f"{member_full_name(reviewer)} replied to your review for {guest_name}, at {ts}.",
            church,
            link,
            is_success=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# User / member signals
# ─────────────────────────────────────────────────────────────────────────────


@receiver(post_save, sender=User)
def notify_user_creation(sender, instance, created, **kwargs):
    if not created:
        return
    church = _current_church()
    if not church:
        return
    ts = format_church_datetime(timezone.localtime(), church)
    link = reverse("accounts:user_list")
    notify_members(
        get_admin_members(church),
        "New Member Added",
        f"New member: {user_full_name(instance)}, at {ts}.",
        church,
        link,
        is_success=True,
    )


@receiver(post_delete, sender=User)
def notify_user_deletion(sender, instance, **kwargs):
    church = _current_church()
    if not church:
        return
    ts = format_church_datetime(timezone.localtime(), church)
    link = reverse("accounts:user_list")
    notify_members(
        get_admin_members(church),
        "Member Removed",
        f"Member removed: {user_full_name(instance)}, at {ts}.",
        church,
        link,
        is_urgent=True,
    )


@receiver(user_logged_in)
def notify_user_login(sender, request, user, **kwargs):
    church = getattr(request, "church", None)
    if not church:
        return

    member = _member_for_user(user, church)
    if not member:
        return

    ts = format_church_datetime(timezone.localtime(), church)
    link = reverse("accounts:user_list")
    name = user_full_name(user)

    # Self-notification for admins only
    from permissions.services.resolver import PermissionResolver

    resolver = PermissionResolver(user, church)
    is_admin = user.is_superuser or resolver.can("dashboard.admin")

    if is_admin:
        notify_members(
            [member],
            "Login",
            f"I just logged in, at {ts}.",
            church,
            link,
            is_urgent=True,
        )

    # Notify other admins
    other_admins = [a for a in get_admin_members(church) if a != member]
    if other_admins:
        notify_members(
            other_admins, "Member Login", f"{name} logged in, at {ts}.", church, link
        )


# ─────────────────────────────────────────────────────────────────────────────
# UserSettings auto-create on ChurchMember creation
# ─────────────────────────────────────────────────────────────────────────────


@receiver(post_save, sender="accounts.ChurchMember")
def create_user_settings(sender, instance, created, **kwargs):
    """Auto-create UserSettings when a new ChurchMember is created."""
    if not created:
        return
    from notifications.models import UserSettings

    UserSettings.raw_objects.get_or_create(
        member=instance,
        defaults={"church": instance.church},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Chat message signals
# ─────────────────────────────────────────────────────────────────────────────


def _detect_mentioned_members(text, church, sender_membership=None):
    """
    Detect @mentioned ChurchMembers in message text.
    Scoped to this church. Optionally excludes sender's own membership.
    """
    if not text or "@" not in text:
        return []

    from units.models import UnitMembership
    from accounts.models import ChurchMember

    memberships = UnitMembership.raw_objects.filter(
        church=church, is_active=True
    ).select_related("workforce_member__member__user")

    mentioned = []
    seen_member_ids = set()

    for m in memberships:
        try:
            u = m.workforce_member.member.user
            name = (u.full_name or u.username or "").strip()
            if not name:
                continue
            pattern = (
                rf"@(?:{re.escape(u.title)}\s+)?{re.escape(name)}"
                if u.title
                else rf"@{re.escape(name)}"
            )
            if re.search(pattern, text, re.IGNORECASE):
                member = m.workforce_member.member
                if member.pk not in seen_member_ids:
                    mentioned.append(member)
                    seen_member_ids.add(member.pk)
        except AttributeError:
            continue

    return mentioned


@receiver(pre_save, sender="workforce.ChatMessage")
def cache_old_pin(sender, instance, **kwargs):
    if instance.pk:
        old = sender.raw_objects.filter(pk=instance.pk).first()
        instance._old_pinned = old.pinned if old else False
        instance._old_pinned_by = old.pinned_by if old else None
    else:
        instance._old_pinned = False
        instance._old_pinned_by = None


@receiver(post_save, sender="workforce.ChatMessage")
def create_chat_notification(sender, instance, created, **kwargs):
    church = instance.church
    if not church:
        return

    just_pinned = instance.pinned and not getattr(instance, "_old_pinned", False)
    ts_source = instance.pinned_at if just_pinned else instance.created_at
    ts = format_church_datetime(ts_source, church) if ts_source else ""
    link = reverse("workforce:chat_room")
    room = instance.room
    unit = room.unit if room and room.unit else None
    unit_name = unit.name if unit else "ChurchForce"

    sender_member = instance.sender
    sender_name = member_full_name(sender_member) if sender_member else "Someone"

    # Message preview
    if instance.message:
        preview = instance.message[:50]
    elif instance.file:
        preview = "(Attachment)"
    elif instance.guest_card:
        preview = f"(Guest: {instance.guest_card.full_name})"
    else:
        preview = "(No content)"

    notified_pks = set()

    # ── Pinned message ────────────────────────────────────────────────
    if just_pinned and instance.pinned_by:
        pinner_member = instance.pinned_by

        mentioned = _detect_mentioned_members(instance.message, church, instance.sender)

        if pinner_member:
            notify_members(
                [pinner_member],
                f"📌 Pinned ({unit_name})",
                f"I pinned a message, at {ts}.",
                church,
                link,
                is_success=True,
            )
            notified_pks.add(pinner_member.pk)

        for m in mentioned:
            if m.pk not in notified_pks:
                notify_members(
                    [m],
                    f"📌 Pinned ({unit_name})",
                    f"{member_full_name(pinner_member)} pinned a message you were mentioned in, at {ts}.",
                    church,
                    link,
                    is_success=True,
                )
                notified_pks.add(m.pk)

        admins = [a for a in get_admin_members(church) if a.pk not in notified_pks]
        if admins:
            notify_members(
                admins,
                f"📌 Pinned ({unit_name})",
                f"{member_full_name(pinner_member)} pinned a message, at {ts}.",
                church,
                link,
                is_success=True,
            )
            notified_pks.update(a.pk for a in admins)

        return

    # ── Mentions ──────────────────────────────────────────────────────
    if instance.message and "@" in instance.message:
        mentioned = _detect_mentioned_members(instance.message, church, instance.sender)
        for m in mentioned:
            if m.pk not in notified_pks:
                notify_members(
                    [m],
                    f"Mentioned ({unit_name})",
                    f"{sender_name} mentioned you in a message, at {ts}.",
                    church,
                    link,
                    is_success=True,
                )
                notified_pks.add(m.pk)
        return

    # ── Regular message — notify unit members ─────────────────────────
    if not created:
        return

    if unit:
        from units.models import UnitMembership

        unit_memberships = UnitMembership.raw_objects.filter(
            church=church, unit=unit, is_active=True
        ).select_related("workforce_member__member__user")

        recipients = []
        for m in unit_memberships:
            try:
                member = m.workforce_member.member
                if member.pk not in notified_pks and member != sender_member:
                    recipients.append(member)
                    notified_pks.add(member.pk)
            except AttributeError:
                continue

        if recipients:
            notify_members(
                recipients,
                f"Chat ({unit_name})",
                f"{sender_name}:\n{preview}\n{ts}",
                church,
                link,
                is_success=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Event signals
# ─────────────────────────────────────────────────────────────────────────────


@receiver(post_save, sender="services.Event")
def notify_unit_on_event_create(sender, instance, created, **kwargs):
    if not created:
        return

    church = instance.church
    if not church:
        return

    # Format event time
    if instance.is_recurring_weekly:
        ts = f"Every {instance.get_day_of_week_display()}"
    elif instance.date:
        event_dt = datetime.combine(instance.date, instance.time or time.min)
        if timezone.is_naive(event_dt):
            event_dt = timezone.make_aware(event_dt)
        ts = format_church_datetime(event_dt, church, separator=" — ")
    else:
        ts = "TBD"

    unit = getattr(instance, "unit", None)
    unit_name = unit.name if unit else "ChurchForce"

    creator_member = _member_for_user(instance.created_by, church)
    creator_name = member_full_name(creator_member) if creator_member else "Unknown"

    msg = (
        f"{creator_name} created a new event:\n"
        f"{instance.name} ({instance.attendance_mode} {instance.event_type}) — {ts}"
    )

    notified_pks = set()

    # Notify unit members
    if unit:
        from units.models import UnitMembership

        unit_members_qs = UnitMembership.raw_objects.filter(
            church=church, unit=unit, is_active=True
        ).select_related("workforce_member__member__user")

        recipients = []
        for m in unit_members_qs:
            try:
                member = m.workforce_member.member
                if not member.user.is_superuser:
                    recipients.append(member)
                    notified_pks.add(member.pk)
            except AttributeError:
                continue

        notify_members(
            recipients,
            f"{unit_name} Event",
            msg,
            church,
            reverse("dashboard:dashboard"),
            is_success=True,
        )
    else:
        # Church-wide event — notify all active members
        from accounts.models import ChurchMember

        all_members = ChurchMember.raw_objects.filter(
            church=church, is_active=True
        ).exclude(user__is_superuser=True)
        notify_members(
            list(all_members),
            f"{unit_name} Event",
            msg,
            church,
            reverse("dashboard:dashboard"),
            is_success=True,
        )
        notified_pks.update(m.pk for m in all_members)

    # Notify admins
    admin_recipients = [
        a for a in get_admin_members(church) if a.pk not in notified_pks
    ]
    notify_members(
        admin_recipients,
        f"{unit_name} Event",
        msg,
        church,
        reverse("dashboard:admin_dashboard"),
        is_success=True,
    )
    notified_pks.update(a.pk for a in admin_recipients)

    # Creator confirmation
    if creator_member and creator_member.pk not in notified_pks:
        notify_members(
            [creator_member],
            f"{unit_name} Event",
            f"I created a new event: {instance.name} ({instance.attendance_mode} {instance.event_type}) on {ts}.",
            church,
            reverse("dashboard:dashboard"),
            is_success=True,
        )
