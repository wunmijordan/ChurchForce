from django import template

register = template.Library()


@register.filter
def status_color(guest_status):
    """
    Return a Tabler CSS color name for a GuestStatus object or slug string.
    Usage: {{ guest.status|status_color }}
    """
    if not guest_status:
        return "secondary"

    slug = getattr(guest_status, "slug", None) or str(guest_status).lower()

    mapping = {
        "new-guest":   "blue",
        "in-contact":  "cyan",
        "committed":   "orange",
        "planted":     "green",
        "not-planted": "red",
    }
    return mapping.get(slug, "secondary")


@register.filter
def attr(obj, field_name):
    """
    Safely get an attribute from a model instance or a dict.
    Usage: {{ obj|attr:"field_name" }}
    """
    if isinstance(obj, dict):
        return obj.get(field_name)
    return getattr(obj, field_name, None)


@register.filter
def get_item(dictionary, key):
    """Dict lookup in templates. Usage: {{ my_dict|get_item:key }}"""
    if dictionary and key is not None:
        return dictionary.get(key)
    return ""


@register.filter
def detect_social_media_type(handle_url):
    """
    Detect social media platform from a URL or handle.
    Returns: 'linkedin' | 'whatsapp' | 'instagram' | 'twitter' | 'tiktok' | ''
    """
    if not handle_url:
        return ""

    url = handle_url.strip().lower()
    platform_map = {
        "linkedin.com":  "linkedin",
        "wa.me":         "whatsapp",
        "whatsapp.com":  "whatsapp",
        "instagram.com": "instagram",
        "twitter.com":   "twitter",
        "x.com":         "twitter",
        "tiktok.com":    "tiktok",
    }
    for base, platform in platform_map.items():
        if base in url:
            return platform
    return ""
