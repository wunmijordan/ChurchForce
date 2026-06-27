"""
workforce/services/approvals.py

Checks whether a WorkforceTraineeProfile has received all required
admin/leader approvals before promotion.

Called by evaluate_trainee_readiness() in readiness.py.

Currently: a trainee requires approval when PromotionRule.require_admin_approval
is True — which means a LeaderTask of type 'promotion_review' must exist
AND be marked completed (i.e. an admin actioned it positively).

Note: approval is checked AFTER all other readiness conditions are met.
The promotion_engine calls attempt_final_promotion() only after the admin
explicitly approves via the promotion_queue view — so for the normal flow,
this always returns True at the point it's called.

This service exists for the auto-promote path (PromotionRule.auto_promote=True)
where there is no manual approval step.
"""


def check_required_approvals(trainee):
    """
    Return True if approval requirements are satisfied.

    For auto-promote churches (PromotionRule.auto_promote=True):
        Always returns True — no admin approval needed.

    For manual-approve churches (default):
        Returns True only if a completed LeaderTask exists for this trainee.
        In practice, approve_promotion() calls attempt_final_promotion()
        immediately after completing the task, so this will always be True
        when called from promotion_engine.
    """
    try:
        from workforce.models import PromotionRule, LeaderTask

        rule = PromotionRule.raw_objects.filter(
            church=trainee.church, is_active=True, is_default=True
        ).first()

        if rule and rule.auto_promote:
            return True  # Auto-promote — no approval required

        if rule and not rule.require_admin_approval:
            return True  # Admin approval not required by this rule

        # Manual approval required — check for a completed promotion task
        return LeaderTask.raw_objects.filter(
            church=trainee.church,
            trainee=trainee,
            task_type="promotion_review",
            completed=True,
            is_active=True,
        ).exists()

    except Exception:
        return True  # Fail open
