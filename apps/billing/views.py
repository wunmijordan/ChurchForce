"""
billing/views.py

Full Paystack payment flow for manual-renewal SaaS billing.

Views:
    pricing           — public plan cards page
    upgrade           — plan selection form (POST only)
    initiate_payment  — creates PaymentRecord, hits Paystack, redirects client
    payment_callback  — Paystack redirects here after payment (verify + activate)
    webhook           — Paystack POSTs here on charge.success (authoritative)
    portal            — subscription management for logged-in admins
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from billing.models import ChurchSubscription, PaymentRecord, SubscriptionPlan
from billing.paystack import (
    PaystackError,
    generate_reference,
    initialize_transaction,
    verify_transaction,
    verify_webhook_signature,
)
from billing.services import activate_subscription_from_payment

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Plan cards (public)
# ─────────────────────────────────────────────────────────────────────────────

PLAN_CARDS = [
    {
        "key": "trial",
        "label": "Free Trial",
        "monthly_price": 0,
        "duration": "14 days",
        "description": "Full access to every feature. No card required.",
        "features": [
            "All core features",
            "Up to 50 workforce members",
            "Guest management",
            "Real-time messaging",
            "Push notifications",
        ],
        "highlight": False,
    },
    {
        "key": "saas",
        "label": "SaaS",
        "monthly_price": 15000,
        "duration": "/ month",
        "description": "Your own subdomain. Full multi-campus support.",
        "features": [
            "Everything in Trial",
            "yourchurch.workforce.church subdomain",
            "Multi-campus support",
            "Priority support",
            "Unlimited members",
        ],
        "highlight": True,
    },
    {
        "key": "white_label",
        "label": "White Label",
        "monthly_price": 45000,
        "duration": "/ month",
        "description": "Your own domain, your own brand. Dedicated infrastructure.",
        "features": [
            "Everything in SaaS",
            "Custom domain (app.yourchurch.org)",
            "Dedicated database",
            "White-label branding",
            "Dedicated onboarding support",
        ],
        "highlight": False,
    },
]

INTERVAL_LABELS = {
    "monthly":  "Monthly",
    "annual":   "Annual (10% off)",
    "biannual": "Bi-annual (20% off)",
}


def pricing(request):
    """
    Public pricing page. Shows plan cards with interval pricing.
    Template: billing/pricing.html
    """
    church = getattr(request, "church", None)
    current_plan = None

    if church:
        from billing.services import get_plan_name
        current_plan = get_plan_name(church)

    # Attach computed prices for all intervals to each plan card
    plans_with_prices = []
    for card in PLAN_CARDS:
        plan_obj = SubscriptionPlan.objects.filter(name=card["key"]).first()
        prices = {}
        if plan_obj:
            for interval in ("monthly", "annual", "biannual"):
                prices[interval] = plan_obj.price_for_interval(interval)
        plans_with_prices.append({**card, "prices": prices})

    return render(request, "billing/pricing.html", {
        "plans": plans_with_prices,
        "interval_labels": INTERVAL_LABELS,
        "current_plan": current_plan,
        "church": church,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Upgrade form — plan + interval selection
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def upgrade(request):
    """
    Handles plan + interval selection.
    GET  → redirect to pricing.
    POST → validate selection, render confirmation page.
    Template: billing/upgrade_confirm.html
    """
    church = getattr(request, "church", None)
    if not church:
        return redirect("tenants:signup")

    if request.method == "GET":
        return redirect("billing:pricing")

    plan_key = request.POST.get("plan", "").strip()
    interval = request.POST.get("interval", "monthly").strip()
    subdomain = request.POST.get("subdomain", "").strip()
    custom_domain = request.POST.get("custom_domain", "").strip()

    if plan_key not in ("saas", "white_label"):
        messages.error(request, "Please select a valid plan.")
        return redirect("billing:pricing")

    if interval not in ("monthly", "annual", "biannual"):
        interval = "monthly"

    plan_obj = SubscriptionPlan.objects.filter(name=plan_key).first()
    if not plan_obj:
        messages.error(request, "Selected plan is not available. Please contact support.")
        return redirect("billing:pricing")

    amount = plan_obj.price_for_interval(interval)

    # Validate required routing field
    if plan_key == "saas" and not subdomain:
        messages.error(request, "Please enter your desired subdomain.")
        return redirect("billing:pricing")

    if plan_key == "white_label" and not custom_domain:
        messages.error(request, "Please enter your custom domain.")
        return redirect("billing:pricing")

    return render(request, "billing/upgrade_confirm.html", {
        "church": church,
        "plan": plan_obj,
        "plan_key": plan_key,
        "interval": interval,
        "interval_label": INTERVAL_LABELS[interval],
        "amount": amount,
        "subdomain": subdomain,
        "custom_domain": custom_domain,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Initiate payment — create PaymentRecord, redirect to Paystack
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def initiate_payment(request):
    """
    Creates a pending PaymentRecord, initializes the Paystack transaction,
    and redirects the client to Paystack's hosted payment page.

    Expects POST fields: plan, interval, subdomain/custom_domain (as relevant).
    """
    church = getattr(request, "church", None)
    if not church:
        messages.error(request, "No organisation context found.")
        return redirect("billing:pricing")

    plan_key = request.POST.get("plan", "").strip()
    interval = request.POST.get("interval", "monthly").strip()
    subdomain = request.POST.get("subdomain", "").strip()
    custom_domain = request.POST.get("custom_domain", "").strip()

    plan_obj = SubscriptionPlan.objects.filter(name=plan_key).first()
    if not plan_obj or plan_key not in ("saas", "white_label"):
        messages.error(request, "Invalid plan selected.")
        return redirect("billing:pricing")

    amount_ngn = plan_obj.price_for_interval(interval)

    if amount_ngn == 0:
        messages.error(request, "This plan has no charge. Contact support.")
        return redirect("billing:pricing")

    reference = generate_reference()
    callback_url = request.build_absolute_uri(
        reverse("billing:payment_callback")
    )

    # Metadata is echoed back in the webhook — carry everything needed
    # to activate the subscription without trusting any client-side data.
    metadata = {
        "church_id": church.id,
        "church_name": church.name,
        "plan_name": plan_key,
        "interval": interval,
        "subdomain": subdomain,
        "custom_domain": custom_domain,
    }

    # Create a pending PaymentRecord before hitting Paystack
    # so we never lose track of an initiated transaction.
    payment = PaymentRecord.objects.create(
        church=church,
        plan=plan_obj,
        interval=interval,
        reference=reference,
        amount_ngn=amount_ngn,
        status="pending",
        raw_payload={"metadata": metadata},
    )

    try:
        tx = initialize_transaction(
            email=request.user.email,
            amount_kobo=plan_obj.price_in_kobo(interval),
            reference=reference,
            callback_url=callback_url,
            metadata=metadata,
        )
    except PaystackError as exc:
        payment.status = "failed"
        payment.save(update_fields=["status", "updated_at"])
        logger.error("Paystack init failed for %s: %s", reference, exc)
        messages.error(
            request,
            "We could not connect to the payment provider. Please try again.",
        )
        return redirect("billing:pricing")

    # Redirect client to Paystack hosted payment page
    return redirect(tx["authorization_url"])


# ─────────────────────────────────────────────────────────────────────────────
# Payment callback — Paystack redirects here after the client pays
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def payment_callback(request):
    """
    Paystack redirects the client here after payment (success or failure).
    This is a UX path — we verify and activate if successful, but the
    webhook is the authoritative activation path.

    Template: billing/payment_result.html
    """
    reference = request.GET.get("reference", "").strip()
    church = getattr(request, "church", None)

    if not reference or not church:
        messages.error(request, "Invalid payment reference.")
        return redirect("billing:pricing")

    payment = PaymentRecord.objects.filter(
        reference=reference, church=church
    ).first()

    if not payment:
        messages.error(request, "Payment record not found.")
        return redirect("billing:pricing")

    # Already processed by webhook — just show the result
    if payment.status == "success":
        return render(request, "billing/payment_result.html", {
            "success": True,
            "church": church,
            "payment": payment,
            "is_white_label": payment.plan.white_label,
        })

    # Verify with Paystack
    try:
        tx = verify_transaction(reference)
    except PaystackError as exc:
        logger.warning("Callback verify failed for %s: %s", reference, exc)
        payment.status = "failed"
        payment.save(update_fields=["status", "updated_at"])
        return render(request, "billing/payment_result.html", {
            "success": False,
            "church": church,
            "payment": payment,
            "error": str(exc),
        })

    # Activate
    payment.status = "success"
    payment.paystack_event = "charge.success"
    payment.raw_payload = tx
    payment.save(update_fields=["status", "paystack_event", "raw_payload", "updated_at"])

    activate_subscription_from_payment(payment)

    return render(request, "billing/payment_result.html", {
        "success": True,
        "church": church,
        "payment": payment,
        "is_white_label": payment.plan.white_label,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Webhook — Paystack POSTs here on charge.success (authoritative path)
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def webhook(request):
    """
    Paystack webhook endpoint.

    Security: always verify the X-Paystack-Signature header first.
    Register this URL in your Paystack dashboard:
        https://dashboard.paystack.com/#/settings/developer

    Idempotent: safe to receive the same event multiple times.
    Returns 200 quickly — Paystack retries on non-2xx.
    """
    payload_bytes = request.body
    signature = request.headers.get("X-Paystack-Signature", "")

    if not verify_webhook_signature(payload_bytes, signature):
        logger.warning("Webhook: invalid signature — possible spoofed request.")
        return HttpResponse(status=400)

    try:
        event = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    event_type = event.get("event")
    data = event.get("data", {})

    logger.info("Paystack webhook received: %s", event_type)

    # We only act on charge.success for manual renewals
    if event_type != "charge.success":
        return HttpResponse(status=200)

    reference = data.get("reference", "")
    if not reference:
        return HttpResponse(status=200)

    # Look up the PaymentRecord we created during initiation
    try:
        payment = PaymentRecord.objects.get(reference=reference)
    except PaymentRecord.DoesNotExist:
        # Paystack sent a webhook for a reference we don't know —
        # log and return 200 so Paystack doesn't keep retrying.
        logger.warning("Webhook: unknown reference %s", reference)
        return HttpResponse(status=200)

    # Idempotency guard — already processed
    if payment.status == "success":
        return HttpResponse(status=200)

    # Verify independently — never trust webhook data alone
    try:
        tx = verify_transaction(reference)
    except PaystackError as exc:
        logger.error("Webhook verify failed for %s: %s", reference, exc)
        payment.status = "failed"
        payment.paystack_event = event_type
        payment.save(update_fields=["status", "paystack_event", "updated_at"])
        return HttpResponse(status=200)

    # Update record and activate subscription
    payment.status = "success"
    payment.paystack_event = event_type
    payment.raw_payload = tx
    payment.save(update_fields=["status", "paystack_event", "raw_payload", "updated_at"])

    try:
        activate_subscription_from_payment(payment)
    except Exception as exc:
        logger.error(
            "Webhook: subscription activation failed for %s: %s",
            reference, exc,
        )
        # Don't return 4xx — we've logged it and the record is marked success.
        # Admin can manually resolve from the PaymentRecord.

    return HttpResponse(status=200)


# ─────────────────────────────────────────────────────────────────────────────
# Subscription portal — logged-in admin view
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def portal(request):
    """
    Subscription management portal. Shows current plan, expiry,
    payment history, and upgrade options.
    Template: billing/portal.html
    """
    church = getattr(request, "church", None)
    if not church:
        return redirect("tenants:signup")

    from billing.services import (
        get_subscription, get_plan_name,
        is_founders_plan, subscription_valid,
    )

    sub = get_subscription(church)
    recent_payments = PaymentRecord.objects.filter(
        church=church
    ).order_by("-created_at")[:10]

    return render(request, "billing/portal.html", {
        "church": church,
        "subscription": sub,
        "plan_name": get_plan_name(church),
        "is_founders": is_founders_plan(church),
        "is_valid": subscription_valid(church),
        "days_remaining": sub.days_until_expiry() if sub else None,
        "recent_payments": recent_payments,
        "plans": PLAN_CARDS,
        "interval_labels": INTERVAL_LABELS,
    })
