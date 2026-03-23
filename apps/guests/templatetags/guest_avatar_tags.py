from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def render_guest_avatar(guest, size="avatar-md"):
    """
    Render a guest avatar with their picture or fallback to initials.
    Works with GuestEntry which has .picture (CloudinaryField) and .initials.
    """
    if guest and getattr(guest, "picture", None) and hasattr(guest.picture, "url"):
        return format_html(
            '<span class="avatar {}" style="background-image: url(\'{}\');"></span>',
            size,
            guest.picture.url,
        )
    initials = getattr(guest, "initials", None) or "G"
    return format_html(
        '<span class="avatar {} bg-primary text-white d-flex align-items-center '
        'justify-content-center fw-bold">{}</span>',
        size,
        initials,
    )
