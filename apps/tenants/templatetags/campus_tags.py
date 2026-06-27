"""
tenants/templatetags/campus_tags.py

Template tags and filters for multi-campus church hierarchy.
"""

from django import template
from django.conf import settings as django_settings

register = template.Library()


@register.filter(name="campus_church_url")
def campus_church_url(church_or_campus):
    """
    Return the public URL for a campus-church (Church instance)
    or a Campus instance (uses campus.campus_church).

    Usage:
        {{ campus_church|campus_church_url }}
        {{ led_campus.campus_church|campus_church_url }}
    """
    if church_or_campus is None:
        return ""

    # Accept either a Church or a Campus object
    from tenants.models import Church, Campus

    if isinstance(church_or_campus, Campus):
        church = getattr(church_or_campus, "campus_church", None)
        if church is None:
            return ""
    elif isinstance(church_or_campus, Church):
        church = church_or_campus
    else:
        return ""

    if church.custom_domain:
        return f"https://{church.custom_domain}"

    app_domains = getattr(django_settings, "APP_DOMAINS", None) or ["workforce.church"]
    app_domain = app_domains[0]

    # In dev, always use path routing regardless of subdomain field
    if django_settings.DEBUG:
        return f"http://{app_domain}/{church.slug}"

    if church.subdomain:
        return f"https://{church.subdomain}.{app_domain}"

    return f"https://{app_domain}/{church.slug}"


@register.filter(name="is_hq_disabled")
def is_hq_disabled(module_name, hq_override):
    """
    Return True if the given module is force-disabled by HQ override.

    Usage:
        {% if "guests"|is_hq_disabled:hq_override %}
    """
    if not hq_override or not isinstance(hq_override, dict):
        return False
    disabled = hq_override.get("disable_modules", [])
    return module_name in disabled


@register.filter(name="lookup")
def lookup(form_or_dict, key):
    """
    Return a bound form field by name, or a dict value by key.

    Usage (form field):
        {% with fld=form|lookup:"enable_guest_module" %}{{ fld }}{% endwith %}

    Usage (dict):
        {{ my_dict|lookup:"some_key" }}
    """
    if hasattr(form_or_dict, "__getitem__"):
        try:
            return form_or_dict[key]
        except (KeyError, TypeError):
            return ""
    return getattr(form_or_dict, key, "")


@register.filter(name="split")
def split_filter(value, delimiter=","):
    """
    Split a string by delimiter and return a list.

    Usage:
        {% for fname in "a,b,c"|split:"," %}…{% endfor %}
    """
    if not value:
        return []
    return [part.strip() for part in str(value).split(delimiter)]


@register.simple_tag(takes_context=True)
def campus_breadcrumb(context):
    """
    Render a campus-church breadcrumb string, e.g.:
        Gateway Nation › Abuja Campus

    Returns empty string for HQ / standalone churches.
    """
    church = context.get("church")
    if not church or not getattr(church, "parent_church_id", None):
        return ""
    hq = context.get("hq_church")
    hq_name = hq.name if hq else "HQ"
    return f"{hq_name} › {church.name}"
