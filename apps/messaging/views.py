import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from guests.models import GuestEntry, GuestStatus
from messaging.models import GuestMessage, MessageLog
from messaging.forms import BulkMessageForm
from units.models import UnitMembership


def _can(request, permission):
    if request.user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    return bool(perms and perms.can(permission))


def _sender_membership(request, church):
    """
    Resolve the request user's UnitMembership for use as the message sender.
    Returns the first active membership for this user in this church.
    """
    return (
        UnitMembership.raw_objects.filter(
            church=church,
            workforce_member__member__user=request.user,
            is_active=True,
        )
        .select_related("workforce_member__member__user")
        .first()
    )


@login_required
@require_POST
def send_bulk_message(request):
    """
    Send a bulk SMS to a filtered set of guests.
    Requires: messaging.send_bulk permission.
    """
    church = getattr(request, "church", None)
    if not church:
        messages.error(request, "No church context.")
        return redirect("guests:guest_list")

    if not _can(request, "messaging.send_bulk"):
        messages.error(request, "You do not have permission to send bulk messages.")
        return redirect("guests:guest_list")

    form = BulkMessageForm(request.POST, church=church)
    if not form.is_valid():
        messages.error(request, "Please correct the form errors.")
        return redirect("guests:guest_list")

    sender = _sender_membership(request, church)
    if not sender:
        messages.error(request, "Could not resolve your membership for this church.")
        return redirect("guests:guest_list")

    msg_obj = form.save(commit=False)
    msg_obj.church = church
    msg_obj.sender = sender
    msg_obj.save()

    # Scope recipients to this church
    status_id = form.cleaned_data.get("guest_status")
    recipients_qs = GuestEntry.raw_objects.filter(
        church=church, is_active=True, is_deleted=False
    )
    if status_id:
        recipients_qs = recipients_qs.filter(status_id=status_id)

    msg_obj.recipients.set(recipients_qs)

    # Dispatch SMS for each recipient that has a phone number
    from messaging.sms import send_sms

    sent = 0
    failed = 0
    for guest in recipients_qs.exclude(phone_number="").exclude(
        phone_number__isnull=True
    ):
        log = send_sms(
            phone=guest.phone_number,
            message=form.cleaned_data["body"],
            church=church,
            category="manual",
            recipient_name=guest.full_name,
            guest=guest,
            message_obj=msg_obj,
        )
        if log.status == "sent":
            sent += 1
        else:
            failed += 1

    # Mark the parent message
    msg_obj.send()

    messages.success(
        request,
        f"Message dispatched — {sent} sent, {failed} failed."
        + (
            " (SMS not configured — using stub provider)"
            if failed == 0 and sent == 0
            else ""
        ),
    )
    return redirect("guests:guest_list")


@login_required
def send_guest_message(request, uid):
    """
    Send an individual SMS to a single guest.
    Requires: messaging.send permission or guest assigned to the requester.
    """
    church = getattr(request, "church", None)
    if not church:
        return redirect("guests:guest_list")

    guest = get_object_or_404(GuestEntry.raw_objects.filter(church=church), uid=uid)

    # Permission: must have send permission OR be the assigned follow-up officer
    wf_member = getattr(request, "workforce_member", None)
    is_assigned = (
        wf_member
        and guest.assigned_to
        and hasattr(guest.assigned_to, "workforce_member")
        and guest.assigned_to.workforce_member_id == wf_member.id
    )

    if not (_can(request, "messaging.send") or is_assigned):
        messages.error(request, "You do not have permission to message this guest.")
        return redirect("guests:guest_list")

    if request.method != "POST":
        return redirect("guests:guest_list")

    body = request.POST.get("body", "").strip()
    subject = request.POST.get("subject", "").strip() or "No Subject"

    if not body:
        messages.error(request, "Message body cannot be empty.")
        return redirect("guests:guest_list")

    sender = _sender_membership(request, church)
    if not sender:
        messages.error(request, "Could not resolve your membership.")
        return redirect("guests:guest_list")

    msg_obj = GuestMessage.raw_objects.create(
        church=church,
        sender=sender,
        subject=subject,
        body=body,
    )
    msg_obj.recipients.add(guest)

    from messaging.sms import send_sms

    send_sms(
        phone=guest.phone_number or "",
        message=body,
        church=church,
        category="manual",
        recipient_name=guest.full_name,
        guest=guest,
        message_obj=msg_obj,
    )
    msg_obj.send()

    messages.success(request, f"Message sent to {guest.full_name}.")
    return redirect("guests:guest_list")


@login_required
def get_guests_by_status(request):
    """
    AJAX endpoint — return guests scoped to this church filtered by status.
    Requires: messaging.send_bulk or messaging.send permission.
    """
    church = getattr(request, "church", None)
    if not church:
        return JsonResponse({"error": "No church context."}, status=403)

    if not (_can(request, "messaging.send_bulk") or _can(request, "messaging.send")):
        return JsonResponse({"error": "Unauthorised."}, status=403)

    status_ids = request.GET.getlist("status[]")
    qs = GuestEntry.raw_objects.filter(church=church, is_active=True, is_deleted=False)
    if status_ids:
        qs = qs.filter(status_id__in=status_ids)

    guests = [
        {
            "id": g.id,
            "name": g.full_name,
            "phone": bool(g.phone_number),  # presence only — never expose phone to JS
        }
        for g in qs
    ]
    return JsonResponse({"guests": guests, "count": len(guests)})


@login_required
def message_log(request):
    """
    View the SMS delivery log for this church.
    Requires: messaging.view_log permission.
    """
    church = getattr(request, "church", None)
    if not church:
        return redirect("guests:guest_list")

    if not _can(request, "messaging.view_log"):
        messages.error(request, "You do not have permission to view the message log.")
        return redirect("guests:guest_list")

    logs = MessageLog.raw_objects.filter(church=church).order_by("-created_at")[:200]

    return render(
        request,
        "messaging/message_log.html",
        {
            "logs": logs,
            "page_title": "Message Log",
        },
    )
