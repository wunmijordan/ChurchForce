from django.db.models.signals import post_save
from django.dispatch import receiver

from tenants.models import Church
from bootstrap.models import ChurchTemplate
from bootstrap.services import apply_template_to_church, ensure_default_template


@receiver(post_save, sender=Church)
def create_church_settings(sender, instance, created, **kwargs):
    """Auto-create ChurchSetting when a new church is provisioned."""
    if not created:
        return
    from tenants.models import ChurchSetting
    ChurchSetting.objects.get_or_create(church=instance)


@receiver(post_save, sender=Church)
def bootstrap_church(sender, instance, created, **kwargs):
    """
    Seed lookup data for a newly created church from the default template.

    Guards against double-seeding: onboarding.provision_church() also calls
    apply_template_to_church() explicitly (for test/command reliability), so
    this signal checks whether seeding already happened before running.
    """
    if not created:
        return

    template = (
        instance.template
        or ChurchTemplate.objects.filter(is_default=True, is_active=True).first()
        or ensure_default_template()
    )
    if not template:
        return

    # Guard: if units already exist, bootstrap ran (via provision_church)
    from units.models import ChurchUnit
    if ChurchUnit.raw_objects.filter(church=instance).exists():
        return

    apply_template_to_church(template, instance)


