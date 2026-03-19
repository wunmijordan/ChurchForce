from django.db import models


class ChurchTemplate(models.Model):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class TemplateGuestStatus(models.Model):
    template = models.ForeignKey(
        ChurchTemplate,
        on_delete=models.CASCADE,
        related_name="guest_statuses"
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=30, default="gray")
    order = models.PositiveIntegerField(default=0)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]


class TemplateService(models.Model):
    template = models.ForeignKey(
        ChurchTemplate,
        on_delete=models.CASCADE,
        related_name="services"
    )
    name = models.CharField(max_length=150)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]


class TemplateVisitPurpose(models.Model):
    template = models.ForeignKey(
        ChurchTemplate,
        on_delete=models.CASCADE,
        related_name="purposes"
    )
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)


class TemplateVisitChannel(models.Model):
    template = models.ForeignKey(
        ChurchTemplate,
        on_delete=models.CASCADE,
        related_name="channels"
    )
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)


class TemplateUnit(models.Model):
    template = models.ForeignKey(
        ChurchTemplate,
        on_delete=models.CASCADE,
        related_name="units"
    )
    name = models.CharField(max_length=150)


class TemplateWorkforceStage(models.Model):
    template = models.ForeignKey(
        ChurchTemplate,
        on_delete=models.CASCADE,
        related_name="workforce_stages"
    )
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.name
    

class TemplateWorkforceRole(models.Model):
    template = models.ForeignKey(
        ChurchTemplate,
        on_delete=models.CASCADE,
        related_name="workforce_roles"
    )
    name = models.CharField(max_length=100)
    permissions = models.JSONField(
        default=dict,
        blank=True
    )
    is_leadership = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.name
    

class TemplateUnitRole(models.Model):
    template = models.ForeignKey(
        ChurchTemplate,
        on_delete=models.CASCADE,
        related_name="unit_roles"
    )
    unit_name = models.CharField(
        max_length=150,
        help_text="Must match the TemplateUnit name"
    )
    name = models.CharField(max_length=100)
    permissions = models.JSONField(
        default=dict,
        blank=True
    )
    is_leadership = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.unit_name} → {self.name}"