import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)


def send_workforce_interest_form(guest):
    """
    Creates interest record and generates secure form link.
    """

    from guests.models import WorkforceInterest

    interest, created = WorkforceInterest.raw_objects.get_or_create(
        church=guest.church,
        guest=guest,
    )

    if created or not interest.expires_at:
        interest.mark_expiry(days=14)
        interest.save(update_fields=["expires_at"])

    link = reverse(
        "guests:workforce_interest_form",
        kwargs={"token": interest.token},
    )

    full_url = f"{guest.church.get_absolute_url()}{link}"

    church_name = getattr(guest.church, "name", "your church")
    message = (
        f"Hi {guest.full_name},\n\n"
        f"You have been invited to complete your workforce interest form for {church_name}.\n"
        f"Use this secure link to continue:\n{full_url}"
    )

    if guest.email:
        try:
            send_mail(
                subject=f"{church_name} Workforce Interest Form",
                message=message,
                from_email=getattr(
                    settings, "DEFAULT_FROM_EMAIL", "noreply@churchforce.io"
                ),
                recipient_list=[guest.email],
                fail_silently=True,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send workforce interest email to guest=%s: %s",
                guest.pk,
                exc,
            )

    if guest.phone_number:
        try:
            from messaging.sms import send_sms

            send_sms(
                phone=guest.phone_number,
                message=message,
                church=guest.church,
                category="reminder",
                recipient_name=guest.full_name,
                guest=guest,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send workforce interest SMS to guest=%s: %s",
                guest.pk,
                exc,
            )

    return full_url


def send_interest_reminder(interest, reminder=1):

    guest = interest.guest

    link = f"{guest.church.get_absolute_url()}/workforce-interest/{interest.token}/"

    # TODO:
    # future:
    # send email / whatsapp

    print(f"Reminder {reminder} sent to {guest.full_name}: {link}")
