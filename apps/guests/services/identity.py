# guests/services/identity.py

from django.db import transaction


@transaction.atomic
def ensure_member_identity(guest):
    """
    Guarantees a Guest has a ChurchMember identity.

    Safe to call multiple times.
    """

    from accounts.models import CustomUser, ChurchMember
    from accounts.utils import generate_username, generate_temp_password

    if getattr(guest, "converted_to", None):
        return guest.converted_to

    church = guest.church
    email = guest.email or ""
    full_name = guest.full_name or ""
    phone = guest.phone

    # ── reuse existing user if email exists ──
    user = None

    if email:
        user = CustomUser.objects.filter(
            email__iexact=email
        ).first()

    if not user:
        username = generate_username(full_name, church.slug)
        temp_password = generate_temp_password()

        user = CustomUser.objects.create_user(
            username=username,
            email=email,
            password=temp_password,
            full_name=full_name,
            phone_number=phone,
            is_active=True,
        )

    member, _ = ChurchMember.raw_objects.get_or_create(
        church=church,
        user=user,
        defaults={"is_active": True},
    )

    # link guest history
    guest.converted_to = member
    guest.save(update_fields=["converted_to"])

    return member