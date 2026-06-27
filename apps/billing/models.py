from django.db import models
from django.utils import timezone
from tenants.models import Church


class SubscriptionPlan(models.Model):

    PLAN_CHOICES = [
        ("trial", "Trial"),
        ("saas", "SaaS"),
        ("white_label", "White Label"),
        ("founders_saas_lifetime", "Founders SaaS Lifetime"),
        ("founders_white_label_lifetime", "Founders White Label Lifetime"),
    ]

    name = models.CharField(max_length=50, choices=PLAN_CHOICES, unique=True)
    multi_campus = models.BooleanField(default=False)
    white_label = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    # Base monthly price in Naira. 0 = free / lifetime.
    monthly_price_ngn = models.PositiveIntegerField(
        default=0,
        help_text="Monthly price in Naira. Set 0 for free or lifetime plans.",
    )

    # Campus limits — controls multi-campus feature per plan tier.
    # 0 = no campuses (trial); -1 = unlimited; positive = fixed cap.
    campus_limit = models.SmallIntegerField(
        default=0,
        help_text=(
            "Maximum number of campuses. '0' = none (trial); '-1' = unlimited; 'n' = capped."
        ),
    )
    campus_price_ngn = models.PositiveIntegerField(
        default=0,
        help_text="Additional cost in Naira per campus above the plan's base allowance.",
    )
    annual_discount_pct = models.PositiveSmallIntegerField(
        default=10,
        help_text="Percentage discount for annual billing.",
    )
    biannual_discount_pct = models.PositiveSmallIntegerField(
        default=20,
        help_text="Percentage discount for bi-annual billing.",
    )

    @property
    def is_founders(self):
        return self.name in (
            "founders_saas_lifetime",
            "founders_white_label_lifetime",
        )

    def __str__(self):
        return self.name

    def interval_multiplier(self, interval):
        if interval == "annual":
            return 12 * (1 - (max(0, min(self.annual_discount_pct, 100)) / 100))
        if interval == "biannual":
            return 24 * (1 - (max(0, min(self.biannual_discount_pct, 100)) / 100))
        return 1

    def price_for_interval(self, interval):
        """
        Return the total charge in Naira for a given billing interval.

        Discounts are configurable per plan:
            - annual_discount_pct
            - biannual_discount_pct
        """
        if self.monthly_price_ngn == 0:
            return 0

        return int(self.monthly_price_ngn * self.interval_multiplier(interval))

    def price_in_kobo(self, interval):
        """Paystack expects amounts in kobo (1 NGN = 100 kobo)."""
        return self.price_for_interval(interval) * 100


class ChurchSubscription(models.Model):

    INTERVAL_CHOICES = [
        ("monthly", "Monthly"),
        ("annual", "Annual"),
        ("biannual", "Bi-annual"),
    ]

    church = models.OneToOneField(
        Church,
        on_delete=models.CASCADE,
        related_name="churchsubscription",
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    interval = models.CharField(
        max_length=20,
        choices=INTERVAL_CHOICES,
        default="monthly",
    )
    is_trial = models.BooleanField(default=True)
    started_at = models.DateTimeField(auto_now_add=True)
    trial_expires_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # Renewal reminder flags — reset after each successful payment
    reminder_7d_sent = models.BooleanField(default=False)
    reminder_3d_sent = models.BooleanField(default=False)
    reminder_1d_sent = models.BooleanField(default=False)

    # Paystack recurring billing identifiers
    # Populated by webhook after first successful charge.
    # Used to match future invoice.update / subscription.disable events.
    paystack_subscription_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Paystack subscription code for recurring billing.",
    )
    paystack_email_token = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Paystack email token for manage-subscription link.",
    )

    def __str__(self):
        return f"{self.church.name} — {self.plan.name}"

    def is_valid(self):
        if self.plan.name in (
            "founders_saas_lifetime",
            "founders_white_label_lifetime",
        ):
            return True

        if self.is_trial:
            return bool(
                self.trial_expires_at and timezone.now() <= self.trial_expires_at
            )

        return self.is_active and (
            self.expires_at is None or timezone.now() <= self.expires_at
        )

    def days_until_expiry(self):
        """Returns days remaining. None for lifetime plans."""
        if self.plan.name in (
            "founders_saas_lifetime",
            "founders_white_label_lifetime",
        ):
            return None
        expiry = self.trial_expires_at if self.is_trial else self.expires_at
        if not expiry:
            return None
        return max(0, (expiry - timezone.now()).days)

    def reset_reminder_flags(self):
        self.reminder_7d_sent = False
        self.reminder_3d_sent = False
        self.reminder_1d_sent = False
        self.save(
            update_fields=[
                "reminder_7d_sent",
                "reminder_3d_sent",
                "reminder_1d_sent",
            ]
        )


class PaymentRecord(models.Model):
    """
    Immutable audit log for every payment attempt.
    Never delete rows from this table.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("abandoned", "Abandoned"),
    ]

    church = models.ForeignKey(
        Church,
        on_delete=models.PROTECT,
        related_name="payment_records",
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    interval = models.CharField(
        max_length=20,
        choices=ChurchSubscription.INTERVAL_CHOICES,
        default="monthly",
    )
    reference = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Paystack transaction reference.",
    )
    amount_ngn = models.PositiveIntegerField(help_text="Amount charged in Naira.")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    paystack_event = models.CharField(
        max_length=100,
        blank=True,
        help_text="Paystack event type, e.g. charge.success",
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full Paystack webhook payload for audit.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "-created_at"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.church.name} | {self.reference} | {self.status}"
