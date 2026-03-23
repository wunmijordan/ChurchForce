from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def render_user_avatar(user, size="avatar-sm"):
    """
    Render a user avatar with profile image or fallback to initials.
    Works with CustomUser which has .image (CloudinaryField) and .initials.
    """
    if user and getattr(user, "image", None) and hasattr(user.image, "url"):
        return format_html(
            '<span class="avatar {}" style="background-image: url(\'{}\');"></span>',
            size,
            user.image.url,
        )

    initials = getattr(user, "initials", None) or "?"
    return format_html(
        '<span class="avatar {} bg-secondary text-white d-flex align-items-center '
        'justify-content-center fw-bold">{}</span>',
        size,
        initials,
    )


@register.simple_tag
def member_avatar(member, size="avatar-sm"):
    """
    Render avatar for a ChurchMember (delegates to the member's user).
    Usage: {% member_avatar membership.workforce_member.member %}
    """
    if not member:
        return format_html(
            '<span class="avatar {} bg-secondary text-white d-flex align-items-center '
            'justify-content-center fw-bold">?</span>',
            size,
        )
    user = getattr(member, "user", None)
    if user and getattr(user, "image", None) and hasattr(user.image, "url"):
        return format_html(
            '<span class="avatar {}" style="background-image: url(\'{}\');"></span>',
            size,
            user.image.url,
        )
    initials = getattr(user, "initials", None) or getattr(member, "initials", "?")
    return format_html(
        '<span class="avatar {} bg-secondary text-white d-flex align-items-center '
        'justify-content-center fw-bold">{}</span>',
        size,
        initials,
    )
