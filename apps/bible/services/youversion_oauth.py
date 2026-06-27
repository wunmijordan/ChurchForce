"""
bible/services/youversion_oauth.py

YouVersion OAuth 2.0 / PKCE helpers.

Callback URL note — multi-tenant:
  The YouVersion redirect_uri must be a single, fixed URL registered in the
  developer dashboard. We use {SITE_URL}/bible/youversion/callback/ where
  SITE_URL is your platform root (e.g. https://churchforce.app).

  BUT in your app, the actual URL is /{church_slug}/bible/youversion/callback/
  because of the tenant slug prefix added by TenantMiddleware.

  Resolution:
    - build_authorization_url() receives the `request` object and calls
      request.build_absolute_uri(reverse("bible:youversion_callback"))
      which produces the correct slug-prefixed URL automatically.
    - Register THAT URL in the YouVersion dashboard, e.g.:
        https://churchforce.app/my-church/bible/youversion/callback/
      For multi-tenant use you can register a wildcard or list each slug, OR
      (recommended) add a global non-scoped alias at the root:
        path("bible/youversion/callback/", views.youversion_oauth_callback)
      in churchforce/urls.py (outside the slug-prefixed group) and register
      https://churchforce.app/bible/youversion/callback/ in the dashboard.
      The view resolves the member from the session, so no slug needed.

Credentials:
  - YOUVERSION_APP_KEY      : existing key for Bible content API (X-YVP-App-Key)
  - YOUVERSION_CLIENT_ID    : OAuth client ID from developers.youversion.com
  - YOUVERSION_CLIENT_SECRET: leave blank if YouVersion issues a PKCE-only client
"""

import base64
import hashlib
import logging
import os
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_AUTH_URL  = "https://accounts.youversion.com/oauth/authorize"
_TOKEN_URL = "https://accounts.youversion.com/oauth/token"
_API_BASE  = "https://api.youversion.com/v1"
_SCOPES    = "identity highlights bookmarks notes"


# -- PKCE helpers -------------------------------------------------------------

def _random_b64url(n: int = 32) -> str:
    return base64.urlsafe_b64encode(os.urandom(n)).rstrip(b"=").decode()


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# -- Configuration ------------------------------------------------------------

def _client_id() -> str:
    return getattr(settings, "YOUVERSION_CLIENT_ID", "") or ""


def _client_secret() -> str:
    return getattr(settings, "YOUVERSION_CLIENT_SECRET", "") or ""


def _app_key() -> str:
    return getattr(settings, "YOUVERSION_APP_KEY", "") or ""


def is_oauth_configured() -> bool:
    """True if OAuth sign-in can be offered. CLIENT_SECRET may be blank."""
    return bool(_client_id() and _app_key())


def _build_callback_uri(request=None) -> str:
    """
    Build the redirect_uri for OAuth.

    With a request object: uses request.build_absolute_uri() so the slug
    prefix (e.g. /my-church/bible/youversion/callback/) is included — this
    is what you register in the YouVersion dashboard for dev.

    Without a request object (token refresh etc.): falls back to the
    global non-scoped URL using SITE_URL setting.
    """
    if request is not None:
        try:
            from django.urls import reverse
            path = reverse("bible:youversion_callback")
            return request.build_absolute_uri(path)
        except Exception:
            pass
    site_url = getattr(settings, "SITE_URL", "http://localhost:8000").rstrip("/")
    return f"{site_url}/bible/youversion/callback/"


# -- Public API ---------------------------------------------------------------

def build_authorization_url(member, request=None) -> str:
    """
    Generate a PKCE authorization URL and persist the in-flight state.
    Pass `request` so the callback URI is built correctly for this tenant.
    """
    from bible.models import YouVersionOAuthToken

    state     = _random_b64url(24)
    verifier  = _random_b64url(48)
    challenge = _s256_challenge(verifier)
    callback  = _build_callback_uri(request)

    existing_token = (
        YouVersionOAuthToken.objects.filter(member=member)
        .values_list("access_token", flat=True)
        .first() or ""
    )

    YouVersionOAuthToken.objects.update_or_create(
        member=member,
        defaults={
            "oauth_state":    state,
            "code_verifier":  verifier,
            "access_token":   existing_token,
            # Store the callback URI used so exchange_code can reproduce it exactly
            "_callback_uri":  callback,
        },
    )

    params = {
        "response_type":         "code",
        "client_id":             _client_id(),
        "redirect_uri":          callback,
        "scope":                 _SCOPES,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


def exchange_code(member, code: str, returned_state: str, request=None) -> bool:
    """Exchange auth code for tokens. Returns True on success."""
    from bible.models import YouVersionOAuthToken

    try:
        token_row = YouVersionOAuthToken.objects.get(member=member)
    except YouVersionOAuthToken.DoesNotExist:
        logger.warning("youversion_oauth: no in-flight token row for member %s", member.pk)
        return False

    if token_row.oauth_state != returned_state:
        logger.warning("youversion_oauth: state mismatch for member %s", member.pk)
        return False

    # Use the stored callback URI so it exactly matches what was sent to YouVersion
    stored_callback = getattr(token_row, "_callback_uri", None) or _build_callback_uri(request)

    payload = {
        "grant_type":    "authorization_code",
        "client_id":     _client_id(),
        "redirect_uri":  stored_callback,
        "code":          code,
        "code_verifier": token_row.code_verifier,
    }
    secret = _client_secret()
    if secret:
        payload["client_secret"] = secret

    resp = requests.post(_TOKEN_URL, data=payload, timeout=15)
    if not resp.ok:
        logger.warning("youversion_oauth: token exchange failed %s -- %s",
                       resp.status_code, resp.text[:300])
        return False

    _store_tokens(token_row, resp.json())
    fetch_profile(member)
    sync_highlights(member)
    return True


def refresh_access_token(member) -> bool:
    from bible.models import YouVersionOAuthToken

    try:
        token_row = YouVersionOAuthToken.objects.get(member=member)
    except YouVersionOAuthToken.DoesNotExist:
        return False

    if not token_row.refresh_token:
        return False

    payload = {
        "grant_type":    "refresh_token",
        "client_id":     _client_id(),
        "refresh_token": token_row.refresh_token,
    }
    secret = _client_secret()
    if secret:
        payload["client_secret"] = secret

    resp = requests.post(_TOKEN_URL, data=payload, timeout=15)
    if not resp.ok:
        logger.warning("youversion_oauth: refresh failed %s -- %s",
                       resp.status_code, resp.text[:300])
        return False

    _store_tokens(token_row, resp.json())
    return True


def fetch_profile(member) -> dict:
    token = _get_valid_token(member)
    if not token:
        return {}

    resp = requests.get(f"{_API_BASE}/users/me", headers=_auth_headers(token), timeout=10)
    if not resp.ok:
        logger.warning("youversion_oauth: profile fetch failed %s", resp.status_code)
        return {}

    data      = resp.json()
    user_data = data.get("data") or data

    from bible.models import YouVersionOAuthToken
    YouVersionOAuthToken.objects.filter(member=member).update(
        yv_user_id    = str(user_data.get("id") or ""),
        yv_username   = user_data.get("name") or user_data.get("username") or "",
        yv_avatar_url = user_data.get("avatar_url") or "",
    )
    return user_data


def sync_highlights(member) -> int:
    token = _get_valid_token(member)
    if not token:
        return 0

    resp = requests.get(
        f"{_API_BASE}/highlights",
        headers=_auth_headers(token),
        params={"page_size": 250},
        timeout=15,
    )
    if not resp.ok:
        logger.warning("youversion_oauth: highlights fetch failed %s", resp.status_code)
        return 0

    from bible.models import BibleHighlight

    try:
        church = member.church
    except Exception:
        return 0

    COLOR_MAP = {
        "yellow": "yellow", "pink": "pink", "blue": "blue",
        "green": "green", "purple": "purple", "orange": "yellow",
    }

    count = 0
    for h in (resp.json().get("data") or []):
        refs = h.get("references") or [{}]
        usfm = refs[0].get("usfm") or ""
        parts = usfm.split(".")
        if len(parts) < 3:
            continue
        book_id = parts[0].upper()
        try:
            chapter      = int(parts[1])
            verse_number = int(parts[2])
        except (ValueError, IndexError):
            continue

        color_raw = (h.get("color") or "yellow").lower()
        color     = COLOR_MAP.get(color_raw, "yellow")
        note_obj  = h.get("note") or {}
        note      = (note_obj.get("content") or "") if isinstance(note_obj, dict) else ""

        try:
            BibleHighlight.raw_objects.update_or_create(
                church=church, member=member,
                book_id=book_id, chapter=chapter, verse_number=verse_number,
                defaults={"color": color, "note": note, "book_name": book_id},
            )
            count += 1
        except Exception as exc:
            logger.debug("youversion_oauth: highlight upsert failed - %s", exc)

    logger.info("youversion_oauth: synced %d highlights for member %s", count, member.pk)
    return count


def get_connection_status(member) -> dict:
    from bible.models import YouVersionOAuthToken

    try:
        row = YouVersionOAuthToken.objects.get(member=member)
        if row.access_token:
            return {
                "connected":     True,
                "yv_username":   row.yv_username,
                "yv_avatar_url": row.yv_avatar_url,
                "expired":       row.is_expired,
            }
    except YouVersionOAuthToken.DoesNotExist:
        pass
    return {"connected": False}


def disconnect(member) -> None:
    from bible.models import YouVersionOAuthToken
    YouVersionOAuthToken.objects.filter(member=member).delete()


# -- Internal helpers ---------------------------------------------------------

def _store_tokens(token_row, data: dict) -> None:
    from datetime import timedelta

    expires_in           = data.get("expires_in")
    token_row.access_token  = data.get("access_token", "")
    token_row.refresh_token = data.get("refresh_token", "") or token_row.refresh_token
    token_row.expires_at    = (
        timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None
    )
    token_row.oauth_state   = ""
    token_row.code_verifier = ""
    token_row.save()


def _get_valid_token(member) -> str | None:
    from bible.models import YouVersionOAuthToken

    try:
        row = YouVersionOAuthToken.objects.get(member=member)
    except YouVersionOAuthToken.DoesNotExist:
        return None

    if not row.access_token:
        return None

    if row.is_expired:
        if not refresh_access_token(member):
            return None
        row.refresh_from_db()

    return row.access_token


def _auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-YVP-App-Key": _app_key(),
        "Accept":        "application/json",
    }
