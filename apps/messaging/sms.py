"""
messaging/sms.py

Thin SMS provider abstraction layer.

Usage:
    from messaging.sms import send_sms

    send_sms(
        phone="08012345678",
        message="Happy birthday, John!",
        church=church,
        category="birthday",
        recipient_name="John Doe",
    )

Provider is selected via settings.SMS_PROVIDER:
    "termii"         → Termii (Nigerian provider, recommended)
    "africastalking" → Africa's Talking
    "twilio"         → Twilio
    "stub"           → No-op (logs only, no real SMS — default for dev/test)

All providers write a MessageLog record regardless of success or failure.

To add a new provider:
    1. Create _send_via_yourprovider(phone, message, church) in this file
    2. Add it to PROVIDERS dict at the bottom
    3. Set SMS_PROVIDER = "yourprovider" in settings
"""

import logging
from django.conf import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def send_sms(phone, message, church, category="other",
             recipient_name="", guest=None, message_obj=None):
    """
    Send an SMS and record it in MessageLog.

    Args:
        phone:          Recipient phone number (E.164 or local format).
        message:        SMS body text.
        church:         Church instance — used for scoping the MessageLog.
        category:       One of "manual", "birthday", "reminder", "other".
        recipient_name: Display name for the log record.
        guest:          Optional GuestEntry FK for the log.
        message_obj:    Optional GuestMessage FK for the log.

    Returns:
        MessageLog instance.
    """
    from messaging.models import MessageLog

    provider_name = getattr(settings, "SMS_PROVIDER", "stub")
    provider_fn   = PROVIDERS.get(provider_name, _send_stub)

    reference   = ""
    error       = ""
    status      = "pending"

    if not phone:
        logger.warning("send_sms: no phone number provided for %s — skipped.", recipient_name)
        error  = "No phone number."
        status = "failed"
    else:
        try:
            reference = provider_fn(phone, message, church) or ""
            status    = "sent"
            logger.info(
                "SMS sent via %s to %s [%s] category=%s",
                provider_name, phone, recipient_name, category,
            )
        except SMSError as exc:
            error  = str(exc)
            status = "failed"
            logger.error(
                "SMS failed via %s to %s [%s]: %s",
                provider_name, phone, recipient_name, exc,
            )

    log = MessageLog.raw_objects.create(
        church=church,
        message=message_obj,
        guest=guest,
        phone=phone or "",
        recipient_name=recipient_name or "",
        body=message,
        status=status,
        category=category,
        provider=provider_name,
        provider_reference=reference,
        error_message=error,
    )

    return log


# ─────────────────────────────────────────────────────────────────────────────
# Provider implementations
# ─────────────────────────────────────────────────────────────────────────────

class SMSError(Exception):
    """Raised by provider functions on failure."""
    pass


def _send_stub(phone, message, church):
    """
    No-op provider for development and testing.
    Logs the message but never sends a real SMS.
    Set SMS_PROVIDER = "stub" in settings (this is the default).
    """
    logger.debug("[SMS STUB] To: %s | Message: %s", phone, message[:80])
    return "stub-ref"


def _send_termii(phone, message, church):
    """
    Termii SMS provider (https://termii.com).
    Nigerian provider — supports local numbers without country code prefix.

    Required settings:
        TERMII_API_KEY    — your Termii API key
        TERMII_SENDER_ID  — registered sender ID (e.g. "ChurchForce")
    """
    import requests

    api_key   = getattr(settings, "TERMII_API_KEY", "")
    sender_id = getattr(settings, "TERMII_SENDER_ID", "ChurchForce")

    if not api_key:
        raise SMSError("TERMII_API_KEY is not configured.")

    # Normalise Nigerian numbers to E.164
    normalised = _normalise_ng_number(phone)

    payload = {
        "to":      normalised,
        "from":    sender_id,
        "sms":     message,
        "type":    "plain",
        "channel": "generic",
        "api_key": api_key,
    }

    try:
        response = requests.post(
            "https://api.ng.termii.com/api/sms/send",
            json=payload,
            timeout=15,
        )
        data = response.json()
        if data.get("code") != "ok":
            raise SMSError(f"Termii error: {data.get('message', 'Unknown error')}")
        return data.get("message_id", "")
    except requests.RequestException as exc:
        raise SMSError(f"Termii request failed: {exc}") from exc


def _send_africastalking(phone, message, church):
    """
    Africa's Talking SMS provider.

    Required settings:
        AFRICASTALKING_USERNAME  — your AT username
        AFRICASTALKING_API_KEY   — your AT API key
        AFRICASTALKING_SENDER_ID — optional sender ID
    """
    try:
        import africastalking
    except ImportError:
        raise SMSError("africastalking package is not installed. Run: pip install africastalking")

    username = getattr(settings, "AFRICASTALKING_USERNAME", "")
    api_key  = getattr(settings, "AFRICASTALKING_API_KEY", "")
    sender   = getattr(settings, "AFRICASTALKING_SENDER_ID", "")

    if not username or not api_key:
        raise SMSError("AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY are required.")

    africastalking.initialize(username, api_key)
    sms = africastalking.SMS

    try:
        response = sms.send(message, [phone], sender_id=sender or None)
        recipients = response.get("SMSMessageData", {}).get("Recipients", [])
        if recipients:
            return recipients[0].get("messageId", "")
        return ""
    except Exception as exc:
        raise SMSError(f"Africa's Talking error: {exc}") from exc


def _send_twilio(phone, message, church):
    """
    Twilio SMS provider.

    Required settings:
        TWILIO_ACCOUNT_SID — your Twilio account SID
        TWILIO_AUTH_TOKEN  — your Twilio auth token
        TWILIO_FROM_NUMBER — your Twilio phone number (E.164)
    """
    try:
        from twilio.rest import Client
        from twilio.base.exceptions import TwilioRestException
    except ImportError:
        raise SMSError("twilio package is not installed. Run: pip install twilio")

    sid      = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    token    = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    from_num = getattr(settings, "TWILIO_FROM_NUMBER", "")

    if not sid or not token or not from_num:
        raise SMSError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER are required.")

    client = Client(sid, token)

    try:
        msg = client.messages.create(to=phone, from_=from_num, body=message)
        return msg.sid
    except TwilioRestException as exc:
        raise SMSError(f"Twilio error: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# Provider registry
# ─────────────────────────────────────────────────────────────────────────────

PROVIDERS = {
    "stub":           _send_stub,
    "termii":         _send_termii,
    "africastalking": _send_africastalking,
    "twilio":         _send_twilio,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_ng_number(phone):
    """
    Normalise a Nigerian phone number to E.164 format (+234...).
    Handles common formats: 08012345678, 8012345678, +2348012345678.
    Returns the number unchanged if it already looks like E.164.
    """
    if not phone:
        return phone
    p = phone.strip().replace(" ", "").replace("-", "")
    if p.startswith("+"):
        return p
    if p.startswith("234"):
        return f"+{p}"
    if p.startswith("0") and len(p) == 11:
        return f"+234{p[1:]}"
    if len(p) == 10:
        return f"+234{p}"
    return p  # return as-is if pattern unrecognised