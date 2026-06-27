"""
core/support_views.py — AI Support Bot and Workforce management views
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render
 
from core.ai_skills import (
    QUICK_PROMPTS_GLOBAL,
    QUICK_PROMPTS_MEMBER,
    _resolve_member_tier,
)
 
 
@login_required
def support_bot(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden()
 
    from accounts.models import ChurchMember
 
    member = ChurchMember.raw_objects.filter(
        church=church, user=request.user, is_active=True
    ).first()
 
    tier = _resolve_member_tier(member)
    is_admin_tier = tier <= 2
 
    return render(
        request,
        "core/support/support_bot.html",
        {
            "page_title": "Support",
            "quick_prompts": QUICK_PROMPTS_GLOBAL if is_admin_tier else QUICK_PROMPTS_MEMBER,
            "is_admin_tier": is_admin_tier,
            "church": church,
        },
    )
