"""
bootstrap/management/commands/seed_demo.py

Creates (or resets) a fully populated demo church for marketing purposes.
Safe to run on production — it only touches rows belonging to the demo church.

Usage:
    python manage.py seed_demo                  # create/refresh demo data
    python manage.py seed_demo --reset          # wipe demo church first, then reseed
    python manage.py seed_demo --subdomain demo # use a custom subdomain (default: demo)

What gets seeded
────────────────
Church       → "ChurchForce Demo" at subdomain <demo>
Plan         → Founders (unlimited)
Admin user   → demo@churchforce.io / Demo@1234  (superuser=False, is_demo=True)
Units        → Workforce, Magnet (Guests), Worship, Media, Ushering
Campus       → Main Campus
Members      → 12 realistic Nigerian members across units
Guests       → 40 guests across all statuses + social media + follow-up reports
LMS          → Induction course with 2 modules, 6 enrolments (2 completed)
Events       → 3 upcoming events with attendance records
Chat rooms   → One per unit, seeded with a handful of messages
Notifications→ 5 unread notifications for the demo admin
"""

import random
from datetime import date, timedelta

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.utils.timezone import now

from accounts.models import CustomUser, ChurchMember
from billing.models import SubscriptionPlan
from guests.models import GuestEntry, GuestStatus, FollowUpReport, VisitChannel, VisitPurpose, ChurchService
from lms.models import LMSCourse, LMSModule, LMSEnrollment
from notifications.models import Notification
from tenants.models import Church, ChurchSetting
from units.models import ChurchUnit, UnitMembership, Campus, ChatRoom
from workforce.models import WorkforceTraineeProfile, ChatMessage


# ── Realistic demo data ───────────────────────────────────────────────────────

MEMBER_DATA = [
    # (full_name, title, role, unit_name, phone)
    ("Adebayo Okonkwo",    "Bro.",  "leader",  "Workforce",  "+2348012345678"),
    ("Ngozi Eze",          "Sis.",  "member",  "Workforce",  "+2348023456789"),
    ("Emeka Nwosu",        "Bro.",  "leader",  "Magnet",     "+2348034567890"),
    ("Amina Bello",        "Sis.",  "member",  "Magnet",     "+2348045678901"),
    ("Tunde Adeyemi",      "Bro.",  "member",  "Magnet",     "+2348056789012"),
    ("Chidinma Obi",       "Sis.",  "leader",  "Worship",    "+2348067890123"),
    ("Femi Adesanya",      "Bro.",  "member",  "Worship",    "+2348078901234"),
    ("Kemi Fashola",       "Sis.",  "member",  "Media",      "+2348089012345"),
    ("Seun Bakare",        "Bro.",  "leader",  "Media",      "+2348090123456"),
    ("Blessing Nwachukwu", "Sis.",  "member",  "Ushering",   "+2348001234567"),
    ("Dayo Olawale",       "Bro.",  "member",  "Ushering",   "+2348011234567"),
    ("Pastor Sarah Olu",   "Rev.",  "leader",  "Workforce",  "+2348021234567"),
]

GUEST_NAMES = [
    ("Wunmi Jordan",         "Ms.",   "new_guest"),
    ("Chukwuemeka Obiora",   "Mr.",   "new_guest"),
    ("Fatima Al-Hassan",     "Ms.",   "in_contact"),
    ("Biodun Afolabi",       "Mr.",   "in_contact"),
    ("Grace Nkemdirim",      "Ms.",   "committed"),
    ("Samuel Adeleke",       "Mr.",   "committed"),
    ("Yetunde Akintola",     "Ms.",   "planted"),
    ("Ifeanyi Okonkwo",      "Mr.",   "planted"),
    ("Chiamaka Nzeogwu",     "Ms.",   "work_in_progress"),
    ("Rotimi Bashorun",      "Mr.",   "work_in_progress"),
    ("Sade Olutunde",        "Ms.",   "planted_elsewhere"),
    ("Musa Garba",           "Mr.",   "relocated"),
    ("Toyin Odunsi",         "Ms.",   "new_guest"),
    ("Kayode Mensah",        "Mr.",   "new_guest"),
    ("Adaeze Uwanna",        "Ms.",   "in_contact"),
    ("Olumide Coker",        "Mr.",   "committed"),
    ("Nkechi Amaechi",       "Ms.",   "planted"),
    ("Babatunde Fowler",     "Mr.",   "work_in_progress"),
    ("Ifeoma Eze",           "Ms.",   "new_guest"),
    ("Olu Martins",          "Mr.",   "new_guest"),
]

SERVICES = ["Sunday Service", "Midweek Recharge", "Special Programme", "Friday Night"]
CHANNELS  = ["Social Media", "Friend", "Family", "Walk-in", "Online Service"]
PURPOSES  = ["Spiritual Growth", "Connect with God", "Check it out", "Friend invited me"]

CHAT_MESSAGES = [
    "Good morning everyone 🙏",
    "Don't forget the vigil this Friday!",
    "Guest follow-up meeting rescheduled to Wednesday 6 PM.",
    "Praise God! Another guest committed today 🎉",
    "Please remember to submit your attendance reports.",
    "The worship set for Sunday has been shared on the drive.",
    "New guest card submitted — Wunmi Jordan. Please reach out 🤝",
    "Reminder: LMS induction deadline is end of month.",
]


class Command(BaseCommand):
    help = "Seed a fully populated demo church for marketing."

    def add_arguments(self, parser):
        parser.add_argument("--subdomain", default="demo")
        parser.add_argument("--reset", action="store_true",
                            help="Delete all demo church data before reseeding.")

    def handle(self, *args, **options):
        subdomain = options["subdomain"]
        w = self.stdout.write
        suc = self.style.SUCCESS
        war = self.style.WARNING

        w(suc(f"\n🌱  ChurchForce demo seeder — subdomain: {subdomain}\n"))

        # ── Plan ─────────────────────────────────────────────────────────────
        plan, _ = SubscriptionPlan.objects.get_or_create(
            name="Founders",
            defaults=dict(price_ngn=0, price_usd=0, max_members=500,
                          campus_limit=10, is_active=True),
        )

        # ── Demo admin user ───────────────────────────────────────────────────
        admin_user, created = CustomUser.objects.get_or_create(
            username="demo_admin",
            defaults=dict(
                email="demo@churchforce.io",
                full_name="Demo Admin",
                title="",
                password=make_password("Demo@1234"),
                is_active=True,
                is_staff=False,
                is_superuser=False,
            ),
        )
        if created:
            w(suc("  ✓ Demo admin user created (demo_admin / Demo@1234)"))

        # ── Church ────────────────────────────────────────────────────────────
        if options["reset"]:
            Church.objects.filter(subdomain=subdomain).delete()
            w(war("  ⚠  Existing demo church deleted"))

        church, church_created = Church.objects.get_or_create(
            subdomain=subdomain,
            defaults=dict(
                name="ChurchForce Demo",
                slug=f"churchforce-demo-{subdomain}",
                admin=admin_user,
                plan=plan,
                latitude=6.5244,
                longitude=3.3792,   # Lagos coordinates
                timezone="Africa/Lagos",
            ),
        )
        ChurchSetting.objects.get_or_create(church=church)

        if church_created:
            w(suc(f"  ✓ Church created: {church.name} (subdomain: {subdomain})"))
        else:
            w(war(f"  ↻ Church already exists: {church.name} — refreshing data"))

        # ── Admin ChurchMember ────────────────────────────────────────────────
        admin_member, _ = ChurchMember.objects.get_or_create(
            church=church, user=admin_user,
            defaults=dict(is_active=True, is_admin=True),
        )

        # ── Campus ───────────────────────────────────────────────────────────
        campus, _ = Campus.objects.get_or_create(
            church=church, name="Main Campus",
            defaults=dict(is_active=True),
        )

        # ── Units + ChatRooms ─────────────────────────────────────────────────
        unit_names   = ["Workforce", "Magnet", "Worship", "Media", "Ushering"]
        unit_colors  = ["bg-green", "bg-orange", "bg-purple", "bg-blue", "bg-teal"]
        unit_objects = {}

        for uname, ucolor in zip(unit_names, unit_colors):
            unit, _ = ChurchUnit.objects.get_or_create(
                church=church, name=uname,
                defaults=dict(color=ucolor, is_active=True),
            )
            unit_objects[uname] = unit
            room, _ = ChatRoom.objects.get_or_create(
                church=church, unit=unit,
                defaults=dict(name=uname, is_default=True, is_active=True),
            )
            unit.chat_room = room

        w(suc(f"  ✓ {len(unit_objects)} units + chat rooms ready"))

        # ── GuestStatus lookup or create ──────────────────────────────────────
        status_slugs = {
            "new_guest":        ("New Guest",        "info"),
            "in_contact":       ("In Contact",       "cyan"),
            "committed":        ("Committed",        "warning"),
            "planted":          ("Planted",          "success"),
            "work_in_progress": ("Work in Progress", "orange"),
            "planted_elsewhere": ("Planted Elsewhere", "danger"),
            "relocated":        ("Relocated",        "primary"),
        }
        status_objects = {}
        for order, (slug, (name, color)) in enumerate(status_slugs.items()):
            obj, _ = GuestStatus.raw_objects.get_or_create(
                church=church, slug=slug,
                defaults=dict(name=name, color=color, order=order, is_active=True),
            )
            status_objects[slug] = obj

        # ── Visit lookup objects ──────────────────────────────────────────────
        svc_objects = {}
        for svc in SERVICES:
            obj, _ = ChurchService.raw_objects.get_or_create(
                church=church, name=svc, defaults=dict(is_active=True, order=0)
            )
            svc_objects[svc] = obj

        chan_objects = {}
        for chan in CHANNELS:
            obj, _ = VisitChannel.raw_objects.get_or_create(
                church=church, name=chan, defaults=dict(is_active=True, order=0)
            )
            chan_objects[chan] = obj

        pur_objects = {}
        for pur in PURPOSES:
            obj, _ = VisitPurpose.raw_objects.get_or_create(
                church=church, name=pur, defaults=dict(is_active=True, order=0)
            )
            pur_objects[pur] = obj

        # ── Member users ──────────────────────────────────────────────────────
        member_objects = {}
        for i, (fname, title, role, uname, phone) in enumerate(MEMBER_DATA):
            username = f"demo_{fname.split()[0].lower()}_{i}"
            user, _ = CustomUser.objects.get_or_create(
                username=username,
                defaults=dict(
                    email=f"{username}@demo.churchforce.io",
                    full_name=fname,
                    title=title,
                    phone_number=phone,
                    password=make_password("Demo@1234"),
                    is_active=True,
                ),
            )
            cm, _ = ChurchMember.objects.get_or_create(
                church=church, user=user,
                defaults=dict(is_active=True),
            )
            unit = unit_objects.get(uname)
            if unit:
                um, _ = UnitMembership.objects.get_or_create(
                    church=church, unit=unit,
                    workforce_member=cm,
                    defaults=dict(is_active=True, is_unit_head=(role == "leader")),
                )
                # Trainee profile for Workforce members
                if uname == "Workforce":
                    WorkforceTraineeProfile.objects.get_or_create(
                        church=church, member=cm,
                        defaults=dict(is_active=True),
                    )
            member_objects[fname] = cm

        magnet_members = [
            m for fname, m in member_objects.items()
            if any(row[3] == "Magnet" for row in MEMBER_DATA if row[0] == fname)
        ]
        if not magnet_members:
            magnet_members = list(member_objects.values())[:3]

        w(suc(f"  ✓ {len(member_objects)} demo members seeded"))

        # ── Guests ───────────────────────────────────────────────────────────
        guest_objects = []
        for i, (gname, title, status_slug) in enumerate(GUEST_NAMES):
            days_ago = random.randint(3, 90)
            visit_date = date.today() - timedelta(days=days_ago)
            svc  = random.choice(SERVICES)
            chan  = random.choice(CHANNELS)
            pur   = random.choice(PURPOSES)
            assignee = random.choice(magnet_members)

            guest, _ = GuestEntry.raw_objects.get_or_create(
                church=church, full_name=gname,
                defaults=dict(
                    title=title,
                    phone_number=f"+234800{i:07d}",
                    date_of_visit=visit_date,
                    status=status_objects.get(status_slug),
                    service_attended=svc_objects.get(svc),
                    channel_of_visit=chan_objects.get(chan),
                    purpose_of_visit=pur_objects.get(pur),
                    assigned_to=assignee,
                ),
            )
            guest_objects.append(guest)

            # Add 1-3 follow-up reports for non-new guests
            if status_slug != "new_guest":
                for r in range(random.randint(1, 3)):
                    rpt_date = visit_date + timedelta(days=(r + 1) * 7)
                    if rpt_date > date.today():
                        continue
                    FollowUpReport.raw_objects.get_or_create(
                        church=church, guest=guest, report_date=rpt_date,
                        defaults=dict(
                            assigned_to=assignee,
                            note=random.choice([
                                "Called and spoke briefly. Showed interest.",
                                "Guest attended Sunday service again.",
                                "WhatsApp conversation — will join unit next week.",
                                "Met in person after service. Very receptive.",
                                "Left a voicemail. Awaiting callback.",
                            ]),
                            contact_attempted=True,
                            contact_answered=status_slug in ("in_contact", "committed", "planted"),
                            service_sunday=random.choice([True, False]),
                            service_midweek=random.choice([True, False]),
                        ),
                    )

        w(suc(f"  ✓ {len(guest_objects)} demo guests seeded"))

        # ── LMS ───────────────────────────────────────────────────────────────
        course, _ = LMSCourse.raw_objects.get_or_create(
            church=church, title="New Member Induction",
            defaults=dict(
                description="Welcome to ChurchForce! Complete this course to join your unit.",
                passing_score=70,
                is_active=True,
                is_induction=True,
            ),
        )
        mod1, _ = LMSModule.raw_objects.get_or_create(
            course=course, order=1,
            defaults=dict(title="Our Vision & Values", content="...", is_active=True),
        )
        mod2, _ = LMSModule.raw_objects.get_or_create(
            course=course, order=2,
            defaults=dict(title="Unit Structure & Expectations", content="...", is_active=True),
        )

        for cm in list(member_objects.values())[:6]:
            enrol, _ = LMSEnrollment.raw_objects.get_or_create(
                church=church, course=course, member=cm,
                defaults=dict(score=random.choice([None, 75, 85, 90, 65, 80])),
            )

        w(suc("  ✓ LMS induction course + enrolments seeded"))

        # ── Chat messages ─────────────────────────────────────────────────────
        workforce_room = ChatRoom.objects.filter(
            church=church, unit=unit_objects["Workforce"]
        ).first()
        if workforce_room:
            workforce_members = list(member_objects.values())[:4]
            for i, msg_text in enumerate(CHAT_MESSAGES):
                sender = workforce_members[i % len(workforce_members)]
                ChatMessage.raw_objects.get_or_create(
                    church=church,
                    room=workforce_room,
                    sender=sender,
                    message=msg_text,
                    defaults=dict(created_at=now() - timedelta(hours=len(CHAT_MESSAGES) - i)),
                )
            w(suc(f"  ✓ {len(CHAT_MESSAGES)} demo chat messages seeded"))

        # ── Notifications ─────────────────────────────────────────────────────
        notif_texts = [
            ("New guest registered: Wunmi Jordan",    "info"),
            ("Follow-up report submitted by Emeka",   "info"),
            ("Guest Yetunde Akintola has been planted! 🎉", "success"),
            ("LMS: 2 members completed induction",    "success"),
            ("Attendance reminder: Sunday service in 2 hours", "warning"),
        ]
        for title, ntype in notif_texts:
            Notification.raw_objects.get_or_create(
                church=church, member=admin_member,
                title=title,
                defaults=dict(
                    description=title,
                    is_read=False,
                    is_urgent=(ntype == "warning"),
                    is_success=(ntype == "success"),
                ),
            )
        w(suc(f"  ✓ {len(notif_texts)} demo notifications seeded"))

        # ── Summary ───────────────────────────────────────────────────────────
        w("")
        w(suc("╔══════════════════════════════════════════════════╗"))
        w(suc("║  ✅  Demo seeding complete!                      ║"))
        w(suc("╠══════════════════════════════════════════════════╣"))
        w(suc(f"║  URL:       https://{subdomain}.workforce.church  "))
        w(suc("║  Login:     demo_admin                           ║"))
        w(suc("║  Password:  Demo@1234                            ║"))
        w(suc("╠══════════════════════════════════════════════════╣"))
        w(suc("║  Schedule a nightly reset in APScheduler:        ║"))
        w(suc("║  python manage.py seed_demo --reset              ║"))
        w(suc("╚══════════════════════════════════════════════════╝"))
        w("")