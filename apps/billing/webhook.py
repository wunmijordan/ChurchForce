"""
billing/webhook.py

Paystack webhook receiver and event dispatcher.

Security:
    Every request is verified against x-paystack-signature (HMAC-SHA512).
    Unverified requests are rejected with 400 before any DB work.

Idempotency:
    PaymentRecord.reference is unique. Duplicate webhook deliveries
    (Paystack retries on non-200) are silently ignored.

Metadata contract:
    Payment initiations embed in Paystack's metadata field:
        church_slug   — identifies which church this payment is for
        plan          — "saas" or "white_label"
        interval      — "monthly" | "annual" | "biannual"
        subdomain     — required for saas plan
        custom_domain — required for white_label plan

Events handled:
    charge.success              → activate plan
    invoice.update              → extend subscription on renewal
    invoice.payment_failed      → mark subscription inactive
    subscription.disable        → mark subscription inactive
"""

import hashlib
import hmac
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def paystack_webhook(request):
    """
    Receives and dispatches Paystack webhook events.
    Always returns 200 after verification so Paystack stops retrying,
    even if our processing raises an exception.
    """
    if not _verify_signature(request):
        logger.warning("Paystack webhook: invalid signature rejected.")
        return HttpResponse(status=400)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        logger.error("Paystack webhook: could not parse JSON body.")
        return HttpResponse(status=400)

    event_type = payload.get("event", "")
    data       = payload.get("data", {})
    reference  = data.get("reference") or data.get("id", "")

    logger.info("Paystack webhook received: event=%s ref=%s", event_type, reference)

    try:
        _dispatch(event_type, data, payload)
    except Exception:
        logger.exception(
            "Paystack webhook: unhandled error processing event=%s ref=%s",
            event_type, reference,
        )

    return HttpResponse(status=200)


# ─────────────────────────────────────────────────────────────────────────────
# Signature verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_signature(request):
    secret = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    if not secret:
        logger.error("PAYSTACK_SECRET_KEY is not set.")
        return False
    sig_header = request.headers.get("x-paystack-signature", "")
    computed   = hmac.new(
        secret.encode("utf-8"),
        request.body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(computed, sig_header)


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch(event_type, data, raw_payload):
    handlers = {
        "charge.success":         _handle_charge_success,
        "invoice.update":         _handle_invoice_update,
        "invoice.payment_failed": _handle_payment_failed,
        "subscription.disable":   _handle_subscription_disabled,
    }
    handler = handlers.get(event_type)
    if handler:
        handler(data, raw_payload)
    else:
        logger.debug("Paystack webhook: unhandled event '%s' — ignored.", event_type)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _kobo_to_ngn(kobo):
    """Paystack sends amounts in kobo (100 kobo = 1 NGN)."""
    return (kobo or 0) // 100


def _get_plan(name):
    from billing.models import SubscriptionPlan
    return SubscriptionPlan.objects.filter(name=name).first()


def _get_church(slug):
    from tenants.models import Church
    try:
        return Church.raw_objects.get(slug=slug, is_active=True)
    except Church.DoesNotExist:
        return None


def _subscription_plan_for(church):
    """Return the church's current plan, falling back to the trial plan."""
    sub = getattr(church, "churchsubscription", None) if church else None
    if sub and sub.plan:
        return sub.plan
    return _get_plan("trial")


def _store_subscription_code(church, data):
    """
    Persist Paystack's subscription_code on ChurchSubscription so
    future invoice.update / subscription.disable events can look up
    the church without metadata (which only appears on the first charge).
    """
    from billing.models import ChurchSubscription
    subscription_code = (
        data.get("subscription_code")
        or data.get("plan_object", {}).get("subscription_code", "")
    )
    email_token = data.get("email_token", "")
    if not subscription_code:
        return
    try:
        sub = ChurchSubscription.objects.get(church=church)
        sub.paystack_subscription_code = subscription_code
        sub.paystack_email_token       = email_token
        sub.save(update_fields=["paystack_subscription_code", "paystack_email_token"])
    except ChurchSubscription.DoesNotExist:
        pass


def _reset_reminders(church):
    sub = getattr(church, "churchsubscription", None) if church else None
    if sub:
        sub.reset_reminder_flags()


# ─────────────────────────────────────────────────────────────────────────────
# Event handlers
# ─────────────────────────────────────────────────────────────────────────────

def _handle_charge_success(data, raw_payload):
    """Successful charge → activate plan."""
    from billing.models import PaymentRecord
    from billing.services import upgrade_to_saas, upgrade_to_white_label

    reference  = data.get("reference", "")
    amount_ngn = _kobo_to_ngn(data.get("amount", 0))
    metadata   = data.get("metadata") or {}

    if PaymentRecord.objects.filter(reference=reference).exists():
        logger.info("Paystack webhook: duplicate charge.success ref=%s — skipped.", reference)
        return

    church_slug   = metadata.get("church_slug")
    plan_name     = metadata.get("plan")
    interval      = metadata.get("interval", "monthly")
    subdomain     = metadata.get("subdomain", "")
    custom_domain = metadata.get("custom_domain", "")

    church        = _get_church(church_slug) if church_slug else None
    activated_plan = None

    if church and plan_name:
        expires_at = timezone.now() + timedelta(days=32)
        try:
            if plan_name == "saas":
                upgrade_to_saas(church, subdomain=subdomain, expires_at=expires_at)
                activated_plan = _get_plan("saas")
            elif plan_name == "white_label":
                upgrade_to_white_label(church, custom_domain=custom_domain, expires_at=expires_at)
                activated_plan = _get_plan("white_label")

            logger.info("Paystack webhook: activated plan='%s' for church='%s'", plan_name, church_slug)
            _store_subscription_code(church, data)
            _reset_reminders(church)

        except ValueError as exc:
            logger.error("Paystack webhook: plan activation failed for '%s': %s", church_slug, exc)

    plan_obj = activated_plan or _subscription_plan_for(church)
    if church and plan_obj:
        PaymentRecord.objects.create(
            church=church,
            plan=plan_obj,
            interval=interval,
            reference=reference,
            amount_ngn=amount_ngn,
            status="success",
            paystack_event="charge.success",
            raw_payload=raw_payload,
        )


def _handle_invoice_update(data, raw_payload):
    """Recurring renewal → extend expiry."""
    from billing.models import ChurchSubscription, PaymentRecord

    subscription_code = (
        data.get("subscription", {}).get("subscription_code")
        or data.get("subscription_code", "")
    )
    reference  = data.get("transaction", {}).get("reference") or str(data.get("id", ""))
    amount_ngn = _kobo_to_ngn(data.get("amount", 0))

    if reference and PaymentRecord.objects.filter(reference=reference).exists():
        return

    try:
        sub    = ChurchSubscription.objects.select_related("church", "plan").get(
            paystack_subscription_code=subscription_code
        )
        church = sub.church
        sub.expires_at = timezone.now() + timedelta(days=32)
        sub.is_active  = True
        sub.save(update_fields=["expires_at", "is_active"])
        _reset_reminders(church)

        logger.info("Paystack webhook: renewed subscription for church='%s' until %s", church.slug, sub.expires_at)

        PaymentRecord.objects.create(
            church=church,
            plan=sub.plan,
            interval=sub.interval,
            reference=reference,
            amount_ngn=amount_ngn,
            status="success",
            paystack_event="invoice.update",
            raw_payload=raw_payload,
        )

    except ChurchSubscription.DoesNotExist:
        logger.warning("Paystack webhook: invoice.update for unknown code='%s'", subscription_code)


def _handle_payment_failed(data, raw_payload):
    """Failed recurring charge → deactivate subscription."""
    from billing.models import ChurchSubscription, PaymentRecord

    subscription_code = (
        data.get("subscription", {}).get("subscription_code")
        or data.get("subscription_code", "")
    )
    reference  = str(data.get("id", "")) or data.get("reference", "")
    amount_ngn = _kobo_to_ngn(data.get("amount", 0))

    if reference and PaymentRecord.objects.filter(reference=reference).exists():
        return

    try:
        sub    = ChurchSubscription.objects.select_related("church", "plan").get(
            paystack_subscription_code=subscription_code
        )
        church = sub.church
        sub.is_active = False
        sub.save(update_fields=["is_active"])

        logger.warning("Paystack webhook: payment failed — deactivated subscription for church='%s'", church.slug)

        PaymentRecord.objects.create(
            church=church,
            plan=sub.plan,
            interval=sub.interval,
            reference=reference or f"failed-{subscription_code}",
            amount_ngn=amount_ngn,
            status="failed",
            paystack_event="invoice.payment_failed",
            raw_payload=raw_payload,
        )

    except ChurchSubscription.DoesNotExist:
        logger.warning("Paystack webhook: payment_failed for unknown code='%s'", subscription_code)


def _handle_subscription_disabled(data, raw_payload):
    """Subscription cancelled by client or API."""
    from billing.models import ChurchSubscription, PaymentRecord

    subscription_code = data.get("subscription_code", "")
    reference         = f"disable-{subscription_code}"

    if PaymentRecord.objects.filter(reference=reference).exists():
        return

    try:
        sub    = ChurchSubscription.objects.select_related("church", "plan").get(
            paystack_subscription_code=subscription_code
        )
        church = sub.church
        sub.is_active = False
        sub.save(update_fields=["is_active"])

        logger.info("Paystack webhook: subscription disabled for church='%s'", church.slug)

        PaymentRecord.objects.create(
            church=church,
            plan=sub.plan,
            interval=sub.interval,
            reference=reference,
            amount_ngn=0,
            status="failed",
            paystack_event="subscription.disable",
            raw_payload=raw_payload,
        )

    except ChurchSubscription.DoesNotExist:
        logger.warning("Paystack webhook: subscription.disable for unknown code='%s'", subscription_code)