from django.utils import timezone


def _normalize_time_format(fmt):
    if not fmt:
        return fmt

    if "%S" in fmt:
        return fmt

    replacements = {
        "%H:%M %p": "%H:%M:%S %p",
        "%I:%M %p": "%I:%M:%S %p",
        "%H:%M": "%H:%M:%S",
        "%I:%M": "%I:%M:%S",
    }
    for source, target in replacements.items():
        if source in fmt:
            return fmt.replace(source, target)

    return fmt


def get_effective_date_format(church, default="%b. %d, %Y"):
    if not church:
        return default

    settings_obj = getattr(church, "settings", None)
    if getattr(church, "parent_church_id", None):
        hq = getattr(church, "hq_church", None)
        if hq is not None:
            settings_obj = getattr(hq, "settings", settings_obj)

    return getattr(settings_obj, "date_format", None) or default


def get_effective_time_format(church, default="%H:%M:%S"):
    if not church:
        return _normalize_time_format(default)

    settings_obj = getattr(church, "settings", None)
    if getattr(church, "parent_church_id", None):
        hq = getattr(church, "hq_church", None)
        if hq is not None:
            settings_obj = getattr(hq, "settings", settings_obj)

    return _normalize_time_format(getattr(settings_obj, "time_format", None) or default)


def format_time_value(value, church, default="%H:%M:%S"):
    if not value:
        return None

    fmt = get_effective_time_format(church, default=default)
    try:
        if timezone.is_aware(value):
            value = timezone.localtime(value)
    except Exception:
        pass
    return value.strftime(fmt) if hasattr(value, "strftime") else str(value)


def format_church_datetime(value, church, default_date="%b. %d, %Y", default_time="%H:%M:%S", separator=" - "):
    if not value:
        return None

    try:
        if timezone.is_aware(value):
            value = timezone.localtime(value)
    except Exception:
        pass

    if hasattr(value, "date") and hasattr(value, "time"):
        date_fmt = get_effective_date_format(church, default=default_date)
        time_fmt = get_effective_time_format(church, default=default_time)
        return f"{value.strftime(date_fmt)}{separator}{value.strftime(time_fmt)}"

    return format_time_value(value, church, default=default_time)
