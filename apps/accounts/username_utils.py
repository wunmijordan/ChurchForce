from django.utils.text import slugify


def username_settings(church):
    settings_obj = getattr(church, "settings", None) if church else None
    prefix = (getattr(settings_obj, "username_prefix", "") or "").strip()
    use_slug_suffix = bool(
        getattr(settings_obj, "username_use_church_slug_suffix", False)
    )
    slug_suffix = ""
    if use_slug_suffix and church and getattr(church, "slug", ""):
        slug_suffix = f".{church.slug}"
    return {
        "prefix": prefix,
        "slug_suffix": slug_suffix,
    }


def normalize_username_base(value):
    raw = (value or "").strip()
    if not raw:
        return ""
    cleaned = slugify(raw.replace(".", " ").replace("_", " ").replace("@", " "))
    parts = [part for part in cleaned.split("-") if part]
    if not parts:
        return ""
    return ".".join(parts[:2]) if len(parts) >= 2 else parts[0]


def format_username(base_username, church=None):
    base = (base_username or "").strip().lower()
    if not church or not base:
        return base
    cfg = username_settings(church)
    return f"{cfg['prefix']}{base}{cfg['slug_suffix']}"


def strip_username_decoration(username, church=None):
    value = (username or "").strip()
    if not value:
        return ""
    cfg = username_settings(church)
    if cfg["prefix"] and value.startswith(cfg["prefix"]):
        value = value[len(cfg["prefix"]) :]
    elif value.startswith("@"):
        value = value[1:]
    if cfg["slug_suffix"] and value.endswith(cfg["slug_suffix"]):
        value = value[: -len(cfg["slug_suffix"])]
    return value.strip().lower()


def derive_username_base(full_name):
    cleaned = slugify(full_name or "member")
    parts = [part for part in cleaned.split("-") if part]
    base = ".".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "member")
    return base[:28]


def username_preview(church):
    cfg = username_settings(church)
    return {"prefix": cfg["prefix"], "suffix": cfg["slug_suffix"]}


def login_username_candidates(username, church=None):
    """Return all candidate usernames to try when authenticating against *church*.

    Always produces both the raw value typed AND the bare/decorated variants
    for the current church, so members can log in with either format.
    No sibling / HQ church resolution happens here — the auth backend is
    scoped to request.church and a user must have a ChurchMember row for that
    specific church to be allowed in.
    """
    raw = (username or "").strip()
    if not raw:
        return []
    candidates = [raw]
    if church:
        base = strip_username_decoration(raw, church=church)
        if base:
            decorated = format_username(base, church=church)
            for value in [decorated, base]:
                if value and value not in candidates:
                    candidates.append(value)
    return candidates
