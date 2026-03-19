from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    if dictionary and key is not None:
        return dictionary.get(key)
    return ""


@register.filter
def detect_social_media_type(handle_url):
    """Detect social media platform from URL. Returns platform key or ''."""
    if not handle_url:
        return ""
    url = handle_url.strip().lower()
    for base, platform in {
        "linkedin.com": "linkedin",
        "wa.me": "whatsapp",
        "whatsapp.com": "whatsapp",
        "instagram.com": "instagram",
        "twitter.com": "twitter",
        "x.com": "twitter",
        "tiktok.com": "tiktok",
    }.items():
        if base in url:
            return platform
    return ""