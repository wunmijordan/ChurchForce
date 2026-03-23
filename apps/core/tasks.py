from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

def send_welcome_email(user_email, church_name, admin_name, subdomain):
    subject = f"Welcome to ChurchForce, {church_name}!"
    from_email = 'ChurchForce <hello@churchforce.io>'

    login_url = f"https://{subdomain}.workforce.church/dashboard"
    
    context = {
        'church_name': church_name,
        'admin_name': admin_name,
        'login_url': login_url,
    }

    # Render HTML and create plain text version
    html_content = render_to_string('core/emails/welcome_email.html', context)
    text_content = strip_tags(html_content)

    msg = EmailMultiAlternatives(subject, text_content, from_email, [user_email])
    msg.attach_alternative(html_content, "text/html")
    
    try:
        msg.send(fail_silently=False)
    except Exception as e:
        # Log this so you know if your SMTP (SendGrid/Mailgun) is down
        print(f"Failed to send welcome email to {user_email}: {e}")
