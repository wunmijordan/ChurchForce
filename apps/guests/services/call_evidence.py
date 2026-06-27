from __future__ import annotations

from datetime import datetime
from typing import Any

from django.conf import settings
from django.utils import timezone

from guests.models import GuestCallEvidence, GuestStatus
from units.models import UnitMembership


def _parse_dt(value: Any):
    if not value:
        return None
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return None
        txt = txt.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(txt)
            return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except ValueError:
            return None
    return None


def _status_from_payload(payload: dict):
    raw = str(payload.get("status") or payload.get("call_status") or "").lower().strip()
    mapping = {
        "initiated": GuestCallEvidence.STATUS_INITIATED,
        "ringing": GuestCallEvidence.STATUS_RINGING,
        "answered": GuestCallEvidence.STATUS_ANSWERED,
        "completed": GuestCallEvidence.STATUS_COMPLETED,
        "no_answer": GuestCallEvidence.STATUS_NO_ANSWER,
        "busy": GuestCallEvidence.STATUS_BUSY,
        "failed": GuestCallEvidence.STATUS_FAILED,
    }
    return mapping.get(raw, GuestCallEvidence.STATUS_INITIATED)


def ingest_call_event(*, church, guest, provider: str, payload: dict):
    metadata = payload.get("metadata") or {}
    call_id = str(
        payload.get("provider_call_id")
        or payload.get("call_id")
        or payload.get("id")
        or ""
    ).strip()
    if not call_id:
        raise ValueError("Missing provider call id.")

    assignee = None
    assignee_id = metadata.get("assignee_membership_id") or payload.get("assignee_membership_id")
    if assignee_id:
        try:
            assignee = UnitMembership.raw_objects.filter(
                church=church,
                id=int(assignee_id),
                is_active=True,
            ).first()
        except (TypeError, ValueError):
            assignee = None

    defaults = {
        "guest": guest,
        "assignee": assignee,
        "from_number": str(payload.get("from") or payload.get("from_number") or "").strip(),
        "to_number": str(payload.get("to") or payload.get("to_number") or "").strip(),
        "started_at": _parse_dt(payload.get("started_at")),
        "answered_at": _parse_dt(payload.get("answered_at")),
        "ended_at": _parse_dt(payload.get("ended_at")),
        "duration_seconds": max(0, int(payload.get("duration") or payload.get("duration_seconds") or 0)),
        "call_status": _status_from_payload(payload),
        "recording_url": str(payload.get("recording_url") or "").strip(),
        "transcript": str(payload.get("transcript") or "").strip(),
        "raw_payload": payload,
        "is_active": True,
    }

    evidence, created = GuestCallEvidence.raw_objects.get_or_create(
        church=church,
        provider=provider,
        provider_call_id=call_id,
        defaults=defaults,
    )
    if not created:
        for key, value in defaults.items():
            setattr(evidence, key, value)
        evidence.save()
    return evidence


def verify_call_evidence(evidence: GuestCallEvidence):
    expected = (evidence.guest.phone_number or "").strip()
    to_number = (evidence.to_number or "").strip()
    if expected and to_number and expected not in to_number and to_number not in expected:
        evidence.verified_contact = False
        evidence.verification_reason = "Destination number does not match guest phone."
        evidence.save(update_fields=["verified_contact", "verification_reason", "updated_at"])
        return evidence

    valid_status = evidence.call_status in {
        GuestCallEvidence.STATUS_ANSWERED,
        GuestCallEvidence.STATUS_COMPLETED,
    }
    min_duration = int(getattr(settings, "GUEST_CALL_MIN_DURATION_SECONDS", 25) or 25)
    if valid_status and int(evidence.duration_seconds or 0) >= min_duration:
        evidence.verified_contact = True
        evidence.verification_reason = (
            f"Verified answered call with duration >= {min_duration}s."
        )
    else:
        evidence.verified_contact = False
        evidence.verification_reason = (
            f"Call not verified: status={evidence.call_status}, duration={evidence.duration_seconds}s."
        )
    evidence.save(update_fields=["verified_contact", "verification_reason", "updated_at"])
    return evidence


def maybe_enrich_ai_confidence(evidence: GuestCallEvidence):
    """
    Optional lightweight enrichment hook for dev/prod parity.
    Uses deterministic heuristics until an external model is configured.
    """
    if not bool(getattr(settings, "GUEST_CALL_AI_ENRICH_ENABLED", False)):
        return evidence
    if evidence.ai_contact_confidence is not None:
        return evidence

    status_boost = 0.65 if evidence.call_status in {
        GuestCallEvidence.STATUS_ANSWERED,
        GuestCallEvidence.STATUS_COMPLETED,
    } else 0.25
    duration_boost = min(0.3, (int(evidence.duration_seconds or 0) / 180.0))
    transcript_boost = 0.05 if (evidence.transcript or "").strip() else 0.0
    score = round(min(0.99, status_boost + duration_boost + transcript_boost), 3)

    evidence.ai_contact_confidence = score
    evidence.ai_summary = (
        "Heuristic confidence (dev hook): "
        f"status={evidence.call_status}, duration={evidence.duration_seconds}s."
    )
    evidence.save(update_fields=["ai_contact_confidence", "ai_summary", "updated_at"])
    return evidence


def auto_in_contact_enabled(church):
    if bool(getattr(settings, "GUEST_CALL_AUTO_IN_CONTACT_ENABLED", False)):
        return True
    settings_obj = getattr(church, "settings", None)
    if not settings_obj:
        return False
    cfg = dict(getattr(settings_obj, "workforce_config", {}) or {})
    return bool(cfg.get("auto_in_contact_from_calls", False))


def maybe_advance_to_in_contact(evidence: GuestCallEvidence):
    if not evidence.verified_contact:
        return False
    guest = evidence.guest
    if not auto_in_contact_enabled(guest.church):
        return False
    if not guest.status or guest.status.slug != GuestStatus.SLUG_NEW_GUEST:
        return False
    in_contact = GuestStatus.raw_objects.filter(
        church=guest.church,
        slug=GuestStatus.SLUG_IN_CONTACT,
        is_active=True,
    ).first()
    if not in_contact:
        return False
    guest.status = in_contact
    guest.save(update_fields=["status", "updated_at"])
    return True
