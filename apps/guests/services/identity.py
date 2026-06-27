# guests/services/identity.py

from django.db import transaction


@transaction.atomic
def ensure_member_identity(guest):
    """
    Guarantees a GuestEntry has a corresponding ChurchMember + CustomUser.

    Called during workforce interest form submission.
    Creates a user account (or reuses an existing email match), transfers
    all available biodata, then sends login credentials via SMS/WhatsApp
    to the phone number on the interest form.

    Safe to call multiple times — returns the existing member if already created.
    """
    from accounts.member_service import (
        generate_formatted_username,
        generate_temp_password,
    )
    from accounts.models import CustomUser, ChurchMember

    if getattr(guest, "converted_to", None):
        return guest.converted_to

    church = guest.church
    email = (guest.email or "").strip()
    full_name = (guest.full_name or "").strip()
    phone = (guest.phone_number or "").strip()

    user = None
    temp_password = None

    if email:
        user = CustomUser.objects.filter(email__iexact=email).first()

    if not user:
        username = generate_formatted_username(full_name, church)
        temp_password = generate_temp_password()

        user = CustomUser.objects.create_user(
            username=username,
            email=email,
            password=temp_password,
            full_name=full_name,
            is_active=True,
        )
        _transfer_biodata(user, guest)
    else:
        # Existing user — don't overwrite their password
        _transfer_biodata(user, guest)

    # Mark: this ChurchMember is from the guest pipeline — defer custom_id
    # until the member graduates from trainee to workforce (assign_custom_id called there)
    from accounts.models import ChurchMember as _CM

    _CM._skip_custom_id = True
    try:
        member, created = ChurchMember.raw_objects.get_or_create(
            church=church,
            user=user,
            defaults={"is_active": True},
        )
    finally:
        _CM._skip_custom_id = False

    guest.converted_to = member
    guest.save(update_fields=["converted_to"])

    # Send credentials only for freshly created accounts
    if temp_password and phone:
        _send_credentials_sms(
            phone=phone,
            full_name=full_name,
            username=user.username,
            temp_password=temp_password,
            church=church,
        )

    return member


def _transfer_biodata(user, guest):
    """Copy available biodata from guest record to CustomUser, only for blank fields."""
    changed = []
    biodata_map = {
        "title": getattr(guest, "title", None),
        "full_name": getattr(guest, "full_name", None),
        "phone_number": getattr(guest, "phone_number", None),
        "date_of_birth": getattr(guest, "date_of_birth", None),
        "marital_status": getattr(guest, "marital_status", None),
    }
    for user_field, guest_value in biodata_map.items():
        if not guest_value:
            continue
        if not getattr(user, user_field, None):
            setattr(user, user_field, guest_value)
            changed.append(user_field)
    if changed:
        user.save(update_fields=changed)


def _send_credentials_sms(*, phone, full_name, username, temp_password, church):
    """
    Send login credentials to the new member via SMS/WhatsApp.

    Uses the church's configured SMS provider (messaging.sms.send_sms).
    Message is intentionally terse so it reads clearly on any device.
    """
    try:
        from messaging.sms import send_sms
        from django.conf import settings

        app_name = getattr(settings, "APP_NAME", "ChurchForce")
        app_domain = getattr(settings, "APP_DOMAIN", "workforce.church")
        login_url = f"https://{app_domain}/{church.slug}/accounts/login/"

        first_name = (full_name or "").split()[0] if full_name else "there"

        message = (
            f"Hi {first_name}! Your {church.name} workforce portal account has been created.\n"
            f"Username: {username}\n"
            f"Password: {temp_password}\n"
            f"Login: {login_url}\n"
            f"Please change your password after logging in."
        )

        send_sms(
            phone=phone,
            message=message,
            church=church,
            category="reminder",
            recipient_name=full_name,
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error(
            "Failed to send credentials SMS to %s: %s", phone, exc
        )


def ensure_member_identity_with_creds(guest):
    """
    Same as ensure_member_identity but also returns the temp_password
    so the caller can display it in a credentials popup.
    Returns (member, temp_password_or_None).

    Username is fully decorated (prefix+base+suffix) via
    generate_formatted_username so it matches what appears on the
    credentials display and login form.

    Guest title is transferred to the new user so the credentials
    page can show e.g. "Pastor John Doe".
    """
    from accounts.member_service import (
        generate_formatted_username,
        generate_temp_password,
    )
    from accounts.models import CustomUser, ChurchMember

    if getattr(guest, "converted_to", None):
        return guest.converted_to, None

    church = guest.church
    email = (guest.email or "").strip()
    full_name = (guest.full_name or "").strip()
    phone = (guest.phone_number or "").strip()
    title = (getattr(guest, "title", "") or "").strip()

    user = None
    temp_password = None
    is_new_user = False

    if email:
        user = CustomUser.objects.filter(email__iexact=email).first()

    if not user:
        # Use generate_formatted_username so prefix/suffix are baked into
        # the stored username — identical to the main member creation path.
        username = generate_formatted_username(full_name, church)
        temp_password = generate_temp_password()
        user = CustomUser.objects.create_user(
            username=username,
            email=email,
            password=temp_password,
            full_name=full_name,
            is_active=True,
        )
        # Set title immediately so it appears on the credentials page
        if title and not getattr(user, "title", None):
            user.title = title
            user.save(update_fields=["title"])
        _transfer_biodata(user, guest)
        is_new_user = True
    else:
        _transfer_biodata(user, guest)

    from accounts.models import ChurchMember as _CM

    _CM._skip_custom_id = True
    try:
        member, _ = ChurchMember.raw_objects.get_or_create(
            church=church,
            user=user,
            defaults={"is_active": True},
        )
    finally:
        _CM._skip_custom_id = False

    guest.converted_to = member
    guest.save(update_fields=["converted_to"])

    if is_new_user and phone:
        _send_credentials_sms(
            phone=phone,
            full_name=full_name,
            username=user.username,
            temp_password=temp_password,
            church=church,
        )

    return member, temp_password
