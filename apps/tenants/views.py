"""
tenants/views.py

Public-facing tenant views:
    signup                  — self-serve church registration (trial-first)
    trial_expired           — wall shown when 14-day trial ends
    subscription_inactive   — wall shown when a paid subscription lapses
"""

from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import render, redirect
from django.utils.text import slugify
from django import forms


# ─────────────────────────────────────────────────────────────────────────────
# Signup form
# ─────────────────────────────────────────────────────────────────────────────

class ChurchSignupForm(forms.Form):
    """
    Minimal self-serve signup form.
    Plan selection happens AFTER signup on the billing/pricing page.
    Coordinates are set by the admin via Django admin after signup.
    """

    # Organisation
    church_name = forms.CharField(
        max_length=255,
        label="Organisation name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Grace Chapel"}),
    )
    url_handle = forms.SlugField(
        max_length=63,
        label="URL handle",
        help_text=(
            "Letters, numbers and hyphens only. "
            "This becomes your address: workforce.church/your-handle/"
        ),
        widget=forms.TextInput(attrs={"placeholder": "grace-chapel"}),
    )

    # Admin user
    full_name = forms.CharField(
        max_length=255,
        label="Your full name",
        widget=forms.TextInput(attrs={"placeholder": "John Doe"}),
    )
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"placeholder": "john@gracechapel.org"}),
    )
    password = forms.CharField(
        label="Password",
        min_length=8,
        widget=forms.PasswordInput(),
    )
    password_confirm = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(),
    )

    def clean_url_handle(self):
        value = slugify(self.cleaned_data.get("url_handle", ""))
        if not value:
            raise forms.ValidationError("Please enter a valid URL handle.")
        return value

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Passwords do not match.")
        return cleaned


# ─────────────────────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────────────────────

def signup(request):
    """
    Public self-serve signup. No login required.

    Creates the church on the trial plan, creates the admin user,
    and logs them in. The admin is then directed to the billing/pricing
    page to choose a plan (or they can explore the app on trial first).

    Template: tenants/signup.html
    """
    if request.user.is_authenticated:
        return redirect("post_login_redirect")

    form = ChurchSignupForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data

        try:
            from tenants.onboarding import provision_church

            church, user = provision_church(
                church_name=cd["church_name"],
                slug=cd["url_handle"],
                admin_full_name=cd["full_name"],
                admin_email=cd["email"],
                admin_password=cd["password"],
            )

        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, "tenants/signup.html", {"form": form})

        except Exception:
            messages.error(
                request,
                "Something went wrong while setting up your account. "
                "Please try again or contact support if the problem persists.",
            )
            return render(request, "tenants/signup.html", {"form": form})

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")

        messages.success(
            request,
            f"Welcome to ChurchForce, {church.name}! "
            f"Your 14-day free trial has started.",
        )

        # Nudge toward plan selection — not forced, they can explore first
        return redirect("billing:pricing")

    return render(request, "tenants/signup.html", {"form": form})


def trial_expired(request):
    """
    Shown when a trial has ended and the tenant hasn't upgraded.
    Links to the billing pricing page.

    Template: tenants/trial_expired.html
    """
    church = getattr(request, "church", None)
    return render(request, "tenants/trial_expired.html", {
        "church": church,
        "pricing_url": "billing:pricing",
    })


def subscription_inactive(request):
    """
    Shown when a paid subscription lapses (non-trial).

    Template: tenants/subscription_inactive.html
    """
    church = getattr(request, "church", None)
    return render(request, "tenants/subscription_inactive.html", {
        "church": church,
        "portal_url": "billing:portal",
    })