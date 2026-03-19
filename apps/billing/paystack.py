"""
billing/paystack.py

Thin Paystack API wrapper for manual-renewal billing.

Requires in settings / .env:
    PAYSTACK_SECRET_KEY   sk_live_... or sk_test_...
    PAYSTACK_PUBLIC_KEY   pk_live_... or pk_test_...
"""

import hashlib
import hmac
import uuid

import requests
from django.conf import settings


PAYSTACK_BASE = "https://api.paystack.co"


def _headers():
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def generate_reference():
    return f"churchforce-{uuid.uuid4().hex[:16]}"


def initialize_transaction(*, email, amount_kobo, reference, callback_url, metadata=None):
    """
    Initialize a Paystack transaction. Returns dict with authorization_url,
    access_code, reference.

    metadata should carry: church_id, plan_name, interval — these are echoed
    back in the webhook so we can activate the right subscription without
    trusting anything from the client side.
    """
    response = requests.post(
        f"{PAYSTACK_BASE}/transaction/initialize",
        headers=_headers(),
        json={
            "email": email,
            "amount": amount_kobo,
            "reference": reference,
            "callback_url": callback_url,
            "metadata": metadata or {},
        },
        timeout=15,
    )
    data = response.json()
    if not data.get("status"):
        raise PaystackError(data.get("message", "Paystack initialization failed."))
    return data["data"]


def verify_transaction(reference):
    """
    Verify a transaction by reference. Returns full Paystack transaction dict.
    Raises PaystackError if not found or not successful.

    Always verify before activating anything — never trust the reference alone.
    """
    response = requests.get(
        f"{PAYSTACK_BASE}/transaction/verify/{reference}",
        headers=_headers(),
        timeout=15,
    )
    data = response.json()
    if not data.get("status"):
        raise PaystackError(data.get("message", "Verification failed."))
    tx = data["data"]
    if tx["status"] != "success":
        raise PaystackError(
            f"Transaction {reference} not successful (status: {tx['status']})."
        )
    return tx


def verify_webhook_signature(payload_bytes, signature):
    """
    Verify the X-Paystack-Signature header against the raw request body.
    Returns True if genuine, False otherwise.
    Always call this first in the webhook view.
    """
    secret = settings.PAYSTACK_SECRET_KEY.encode("utf-8")
    expected = hmac.new(secret, payload_bytes, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


class PaystackError(Exception):
    pass