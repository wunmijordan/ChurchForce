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
        "label": "BASIC",
        "monthly_price": 0,
        "duration": "Free Forever",
        "description": "Access via workforce.church/your-church-name/",
        "features": [
            "All Features Included",
            "Unlimited Members & Campuses",
            "Guest Management & Follow-Up",
            "Real-Time Chat & Notifications",
            "Bible Reader & Devotionals",
            "Switch Routing Anytime",
        ],
        "highlight": False,
        "cta": "Use CHURCHFORCE URL",
    },
    {
        "key": "saas",
        "label": "PRO",
        "monthly_price": 0,
        "duration": "Free Forever",
        "description": "Your own church-name.workforce.church Address.",
        "features": [
            "Branded Sub-Domain URL",
            "Multi-Campus Support",
            "Priority Support Channel",
        ],
        "highlight": True,
        "cta": "Use a Sub-Domain",
        "campus_limit": -1,
        "campus_price_ngn": 0,
    },
    {
        "key": "white_label",
        "label": "CUSTOM",
        "monthly_price": 0,
        "duration": "Free Forever",
        "description": "Your own Domain — app.yourchurch.org.",
        "features": [
            "Custom Domain Routing",
            "Hide Platform Credit",
            "Optional Dedicated Database",
        ],
        "highlight": False,
        "cta": "Use Your Custom Domain",
        "campus_limit": -1,
        "campus_price_ngn": 0,
    },
]

INTERVAL_LABELS = {
    "monthly": "Monthly",
    "annual": "Annually",
    "biannual": "Bi-Annually",
}


def pricing(request):
    """
    Public pricing page. Shows plan cards with interval pricing.
    Template: billing/pricing.html
    """
    church = getattr(request, "church", None)
    current_plan = None
    campus_limit_reached = request.GET.get("campus_limit_reached") in ("1", "true", "yes")
    campus_count = 0
    campus_limit_value = 0
    campus_overage = 0
    campus_addon_price = 0
    extra_slots = 0
    # Used for campus add-on math (SaaS-specific)
    annual_discount_pct = 0
    biannual_discount_pct = 0
    # Used for top interval badges on pricing toggle
    interval_annual_discount_pct = 0
    interval_biannual_discount_pct = 0

    if church:
        from billing.services import get_plan_name
        from billing.services import campus_limit as get_campus_limit, extra_campus_slots
        from billing.services import get_routing_display_context
        from tenants.models import Campus

        current_plan = get_plan_name(church)
        campus_count = Campus.raw_objects.filter(church=church, is_active=True).count()
        campus_limit_value = get_campus_limit(church)
        extra_slots = extra_campus_slots(church)
        if campus_limit_value > -1:
            campus_overage = max(0, campus_count - max(campus_limit_value + extra_slots, 0))

    # Attach computed prices for all intervals to each plan card
    # Auto-seed plans if missing (billing.apps.ready() handles this on
    # startup, but this is a lazy safety net for any missed reset)
    from billing.services import ensure_plans_seeded

    ensure_plans_seeded()
    saas_plan = SubscriptionPlan.objects.filter(name="saas").first()
    if saas_plan:
        annual_discount_pct = saas_plan.annual_discount_pct
        biannual_discount_pct = saas_plan.biannual_discount_pct
    badge_plan = SubscriptionPlan.objects.filter(name="white_label").first() or saas_plan
    if badge_plan:
        interval_annual_discount_pct = badge_plan.annual_discount_pct
        interval_biannual_discount_pct = badge_plan.biannual_discount_pct

    plans_with_prices = []
    fallback_multipliers = {"monthly": 1, "annual": 10.8, "biannual": 19.2}
    for card in PLAN_CARDS:
        plan_obj = SubscriptionPlan.objects.filter(name=card["key"]).first()
        prices = {}
        for interval in ("monthly", "annual", "biannual"):
            if plan_obj:
                prices[interval] = plan_obj.price_for_interval(interval)
            else:
                base = card.get("monthly_price", 0)
                prices[interval] = (
                    int(base * fallback_multipliers.get(interval, 1)) if base else 0
                )
        if card["key"] == "saas" and plan_obj:
            campus_addon_price = getattr(plan_obj, "campus_price_ngn", 0)
            annual_discount_pct = getattr(plan_obj, "annual_discount_pct", annual_discount_pct)
            biannual_discount_pct = getattr(plan_obj, "biannual_discount_pct", biannual_discount_pct)
        plans_with_prices.append(
            {
                **card,
                "prices": prices,
                "annual_discount_pct": getattr(plan_obj, "annual_discount_pct", annual_discount_pct)
                if plan_obj
                else annual_discount_pct,
                "biannual_discount_pct": getattr(plan_obj, "biannual_discount_pct", biannual_discount_pct)
                if plan_obj
                else biannual_discount_pct,
            }
        )

    routing_ctx = get_routing_display_context(church) if church else {}

    return render(
        request,
        "billing/pricing.html",
        {
            "plans": plans_with_prices,
            "interval_labels": INTERVAL_LABELS,
            "current_plan": current_plan,
            "church": church,
            "campus_limit_reached": campus_limit_reached,
            "campus_count": campus_count,
            "campus_limit_value": campus_limit_value,
            "campus_overage": campus_overage,
            "campus_addon_price": campus_addon_price,
            "extra_campus_slots": extra_slots,
            "annual_discount_pct": annual_discount_pct,
            "biannual_discount_pct": biannual_discount_pct,
            "interval_annual_discount_pct": interval_annual_discount_pct,
            "interval_biannual_discount_pct": interval_biannual_discount_pct,
            **routing_ctx,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Upgrade form — plan + interval selection
# ─────────────────────────────────────────────────────────────────────────────


@login_required
@require_POST
def upgrade(request):
    """
    Apply a routing tier switch instantly (all tiers are free).
    POST fields: plan, subdomain (saas), custom_domain (white_label).
    """
    church = getattr(request, "church", None)
    if not church:
        return redirect("tenants:signup")

    plan_key = request.POST.get("plan", "").strip()
    subdomain = request.POST.get("subdomain", "").strip()
    custom_domain = request.POST.get("custom_domain", "").strip()

    if plan_key not in ("trial", "saas", "white_label"):
        messages.error(request, "Please select a valid routing option.")
        return redirect("billing:pricing")

    from billing.services import ROUTING_PLAN_LABELS, apply_routing_plan

    try:
        apply_routing_plan(
            church,
            plan_key,
            subdomain=subdomain,
            custom_domain=custom_domain,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("billing:pricing")

    label = ROUTING_PLAN_LABELS.get(plan_key, plan_key)
    messages.success(request, f"Routing updated to {label}.")
    return redirect("billing:pricing")


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
    try:
        campus_addon_qty = max(0, int(request.POST.get("campus_addon_qty", "0") or 0))
    except (TypeError, ValueError):
        campus_addon_qty = 0

    plan_obj = SubscriptionPlan.objects.filter(name=plan_key).first()
    if not plan_obj or plan_key not in ("saas", "white_label"):
        messages.error(request, "Invalid plan selected.")
        return redirect("billing:pricing")

    amount_ngn = plan_obj.price_for_interval(interval)
    if plan_key == "saas" and campus_addon_qty:
        amount_ngn += int(
            plan_obj.campus_price_ngn
            * plan_obj.interval_multiplier(interval)
            * campus_addon_qty
        )

    # Free routing tiers — apply instantly (Paystack reserved for future paid tiers).
    if amount_ngn == 0:
        from billing.services import ROUTING_PLAN_LABELS, apply_routing_plan

        try:
            apply_routing_plan(
                church,
                plan_key,
                subdomain=subdomain,
                custom_domain=custom_domain,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("billing:pricing")
        label = ROUTING_PLAN_LABELS.get(plan_key, plan_key)
        messages.success(request, f"Routing updated to {label}.")
        return redirect("billing:pricing")

    reference = generate_reference()
    callback_url = request.build_absolute_uri(reverse("billing:payment_callback"))

    # Metadata is echoed back in the webhook — carry everything needed
    # to activate the subscription without trusting any client-side data.
    metadata = {
        "church_id": church.id,
        "church_name": church.name,
        "plan_name": plan_key,
        "interval": interval,
        "subdomain": subdomain,
        "custom_domain": custom_domain,
        "campus_addon_qty": campus_addon_qty,
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
            amount_kobo=amount_ngn * 100,
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

    payment = PaymentRecord.objects.filter(reference=reference, church=church).first()

    if not payment:
        messages.error(request, "Payment record not found.")
        return redirect("billing:pricing")

    # Already processed by webhook — just show the result
    if payment.status == "success":
        return render(
            request,
            "billing/payment_result.html",
            {
                "success": True,
                "church": church,
                "payment": payment,
                "is_white_label": payment.plan.white_label,
            },
        )

    # Verify with Paystack
    try:
        tx = verify_transaction(reference)
    except PaystackError as exc:
        logger.warning("Callback verify failed for %s: %s", reference, exc)
        payment.status = "failed"
        payment.save(update_fields=["status", "updated_at"])
        return render(
            request,
            "billing/payment_result.html",
            {
                "success": False,
                "church": church,
                "payment": payment,
                "error": str(exc),
            },
        )

    # Activate
    payment.status = "success"
    payment.paystack_event = "charge.success"
    payment.raw_payload = tx
    payment.save(
        update_fields=["status", "paystack_event", "raw_payload", "updated_at"]
    )

    activate_subscription_from_payment(payment)

    return render(
        request,
        "billing/payment_result.html",
        {
            "success": True,
            "church": church,
            "payment": payment,
            "is_white_label": payment.plan.white_label,
        },
    )


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
    payment.save(
        update_fields=["status", "paystack_event", "raw_payload", "updated_at"]
    )

    try:
        activate_subscription_from_payment(payment)
    except Exception as exc:
        logger.error(
            "Webhook: subscription activation failed for %s: %s",
            reference,
            exc,
        )
        # Don't return 4xx — we've logged it and the record is marked success.
        # Admin can manually resolve from the PaymentRecord.

    return HttpResponse(status=200)


# ─────────────────────────────────────────────────────────────────────────────
# Legacy portal URL → Account routing tab
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def portal(request):
    """Legacy URL — redirect to Account → Routing."""
    from django.urls import reverse

    church = getattr(request, "church", None)
    if not church:
        return redirect("tenants:signup")
    return redirect(f"{reverse('tenants:account')}?section=subscription")
