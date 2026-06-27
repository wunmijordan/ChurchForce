from django.db import migrations, models


def backfill_audience_enrollments(apps, schema_editor):
    LMSCourse = apps.get_model("lms", "LMSCourse")
    LMSEnrollment = apps.get_model("lms", "LMSEnrollment")
    ChurchMember = apps.get_model("accounts", "ChurchMember")
    WorkforceMember = apps.get_model("workforce", "WorkforceMember")
    UnitMembership = apps.get_model("units", "UnitMembership")

    courses = LMSCourse.objects.filter(
        is_active=True,
        course_type__in=("workforce", "unit_training"),
    )
    for course in courses:
        member_ids = set()
        if course.course_type == "workforce" or (
            course.course_type == "unit_training" and not course.unit_id
        ):
            member_ids.update(
                WorkforceMember.objects.filter(
                    church=course.church,
                    is_active=True,
                ).values_list("member_id", flat=True)
            )
        elif course.course_type == "unit_training" and course.unit_id:
            memberships = UnitMembership.objects.filter(
                church=course.church,
                unit_id=course.unit_id,
                is_active=True,
            ).select_related("workforce_member", "trainee_profile")
            for membership in memberships:
                if membership.workforce_member_id:
                    member_ids.add(membership.workforce_member.member_id)
                elif membership.trainee_profile_id:
                    member_ids.add(membership.trainee_profile.member_id)

        first_module = (
            course.modules.filter(is_active=True).order_by("order").first()
        )
        for member in ChurchMember.objects.filter(
            church=course.church,
            id__in=member_ids,
            is_active=True,
        ):
            LMSEnrollment.objects.get_or_create(
                church=course.church,
                member=member,
                course=course,
                defaults={
                    "status": "active",
                    "current_module": first_module,
                    "is_active": True,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("lms", "0003_lmscourse_course_image_lmsmodule_discussion_enabled_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="lmsmodule",
            name="template_data",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Structured content generated from a module template, such as quiz "
                    "questions, answer options, and answer keys."
                ),
            ),
        ),
        migrations.RunPython(backfill_audience_enrollments, migrations.RunPython.noop),
    ]
