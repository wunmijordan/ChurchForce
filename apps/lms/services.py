def first_active_module(course):
    return course.modules.filter(is_active=True).order_by("order").first()


def enroll_member(church, course, member, enrolled_by=None):
    from lms.models import LMSEnrollment

    if not church or not course or not member or not course.is_active:
        return None, False

    return LMSEnrollment.raw_objects.get_or_create(
        church=church,
        member=member,
        course=course,
        defaults={
            "status": "active",
            "current_module": first_active_module(course),
            "enrolled_by": enrolled_by,
            "is_active": True,
        },
    )


def sync_course_audience(course):
    """
    Ensure audience-scoped courses appear in Training for their members.

    Induction remains track-driven through the guest pipeline. Workforce-wide
    courses enroll full workforce members. Unit training enrolls full unit
    members and probationary/trainee unit members for the linked unit.
    """
    if not course or not course.is_active:
        return 0
    if course.course_type not in ("workforce", "unit_training"):
        return 0

    member_ids = set()
    if course.course_type == "workforce":
        from workforce.models import WorkforceMember

        member_ids.update(
            WorkforceMember.raw_objects.filter(
                church=course.church,
                is_active=True,
            ).values_list("member_id", flat=True)
        )
    elif course.course_type == "unit_training":
        if course.unit_id:
            from units.models import UnitMembership

            memberships = UnitMembership.raw_objects.filter(
                church=course.church,
                unit=course.unit,
                is_active=True,
            ).select_related("workforce_member", "trainee_profile")
            for membership in memberships:
                if membership.workforce_member_id:
                    member_ids.add(membership.workforce_member.member_id)
                elif membership.trainee_profile_id:
                    member_ids.add(membership.trainee_profile.member_id)
        else:
            from workforce.models import WorkforceMember

            member_ids.update(
                WorkforceMember.raw_objects.filter(
                    church=course.church,
                    is_active=True,
                ).values_list("member_id", flat=True)
            )

    created = 0
    from accounts.models import ChurchMember

    for member in ChurchMember.raw_objects.filter(
        church=course.church,
        id__in=member_ids,
        is_active=True,
    ):
        _, was_created = enroll_member(course.church, course, member)
        if was_created:
            created += 1
    return created


def sync_member_courses(member, church):
    if not member or not church:
        return 0

    from lms.models import LMSCourse

    created = 0
    for course in LMSCourse.raw_objects.filter(
        church=church,
        course_type="workforce",
        is_active=True,
    ):
        _, was_created = enroll_member(church, course, member)
        if was_created:
            created += 1
    return created


def sync_unit_membership_courses(unit_membership):
    if not unit_membership or not unit_membership.unit_id:
        return 0

    member = None
    if unit_membership.workforce_member_id:
        member = unit_membership.workforce_member.member
    elif unit_membership.trainee_profile_id:
        member = unit_membership.trainee_profile.member
    if not member:
        return 0

    from lms.models import LMSCourse

    created = 0
    for course in LMSCourse.raw_objects.filter(
        church=unit_membership.church,
        course_type="unit_training",
        unit=unit_membership.unit,
        is_active=True,
    ):
        _, was_created = enroll_member(unit_membership.church, course, member)
        if was_created:
            created += 1
    return created


def sync_empty_current_modules(course):
    from lms.models import LMSEnrollment

    first_module = first_active_module(course)
    if not first_module:
        return 0
    return LMSEnrollment.raw_objects.filter(
        church=course.church,
        course=course,
        current_module__isnull=True,
        status="active",
        is_active=True,
    ).update(current_module=first_module)
