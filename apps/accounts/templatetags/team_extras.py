# accounts/templatetags/team_extras.py
import re
from django import template

register = template.Library()


@register.filter
def roles_for_team(raw, team_id):
    """
    Given a raw team_role string and a team_id integer,
    return a list of role names that belong to this team.
    Format understood: "11:Head of Unit,7:Member" or plain "Member".
    """
    if raw is None:
        return []

    text = str(raw).strip()
    if not text:
        return []

    parts = [p.strip() for p in re.split(r"\s*,\s*", text) if p.strip()]
    matches = []
    fallbacks = []

    for part in parts:
        if ":" in part:
            left, right = part.split(":", 1)
            left, right = left.strip(), right.strip()
            if left.isdigit():
                try:
                    if int(left) == int(team_id):
                        matches.append(right)
                    continue
                except Exception:
                    pass
            fallbacks.append(right)
        else:
            if not re.fullmatch(r"\d+", part):
                fallbacks.append(part)

    if matches:
        return matches
    if fallbacks:
        return fallbacks

    cleaned = re.sub(r"\b\d+:", "", text).strip(" ,")
    return [cleaned] if cleaned and not re.fullmatch(r"[\d\s,:]+", cleaned) else []
