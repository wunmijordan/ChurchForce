import uuid
from django.conf import settings
from accounts.models import CustomUser
from tenants.models import Church


def dev_adjust_email(email: str) -> str:
    """
    Allows reuse of same email in DEBUG by auto suffixing.
    admin@test.com -> admin+1@test.com
    """
    if not settings.DEBUG:
        return email

    base, domain = email.split("@")

    existing = CustomUser.objects.filter(
        email__startswith=base,
        email__endswith="@" + domain,
    ).count()

    if existing:
        return f"{base}+{existing}@{domain}"

    return email


def dev_adjust_slug(slug: str) -> str:
    """
    Auto-increment slug in DEBUG mode.
    grace -> grace-1 -> grace-2
    """
    if not settings.DEBUG:
        return slug

    base = slug
    counter = 1

    while Church.raw_objects.filter(slug=slug).exists():
        slug = f"{base}-{counter}"
        counter += 1

    return slug


def generate_dev_church_identity():
    """One-click random dev tenant."""
    uid = uuid.uuid4().hex[:6]

    return {
        "church_name": f"Dev Church {uid}",
        "slug": f"dev-{uid}",
        "admin_full_name": "Dev Admin",
        "admin_email": "admin@test.com",
        "admin_password": "password123",
    }