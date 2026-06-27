from django.contrib.auth.backends import ModelBackend


class ChurchUsernameBackend(ModelBackend):
    """
    Tenant-aware username authentication.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        from accounts.models import CustomUser
        from accounts.username_utils import login_username_candidates

        if username is None:
            username = kwargs.get(CustomUser.USERNAME_FIELD)
        if username is None or password is None:
            return None

        church = getattr(request, "church", None) if request else None
        candidates = login_username_candidates(username, church=church)

        user = None
        for candidate in candidates:
            try:
                if church:
                    user = (
                        CustomUser.objects.filter(
                            church_memberships__church=church,
                            church_memberships__is_active=True,
                            username__iexact=candidate,
                        )
                        .distinct()
                        .get()
                    )
                else:
                    user = CustomUser.objects.get(username__iexact=candidate)
                break
            except CustomUser.DoesNotExist:
                continue
            except CustomUser.MultipleObjectsReturned:
                user = (
                    CustomUser.objects.filter(username__iexact=candidate)
                    .order_by("id")
                    .first()
                )
                break

        if not user:
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
