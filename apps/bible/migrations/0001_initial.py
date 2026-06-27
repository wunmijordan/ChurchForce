"""
bible/migrations/0001_initial.py
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
        ("tenants", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # BibleBadge — global, not tenant-scoped
        migrations.CreateModel(
            name="BibleBadge",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("slug", models.CharField(
                    choices=[
                        ("first_reading", "First Reading"),
                        ("streak_7", "7-Day Streak"),
                        ("streak_30", "30-Day Streak"),
                        ("plan_finisher", "Plan Finisher"),
                        ("memory_master", "Memory Verse Master"),
                        ("study_complete", "Bible Study Complete"),
                        ("verse_sharer", "Verse Sharer"),
                        ("streak_100", "100-Day Streak"),
                    ],
                    max_length=30, unique=True
                )),
                ("name", models.CharField(max_length=80)),
                ("description", models.TextField(blank=True)),
                ("icon_emoji", models.CharField(blank=True, max_length=10)),
                ("color_hex", models.CharField(default="#f59f00", max_length=7)),
                ("points", models.PositiveSmallIntegerField(default=10)),
            ],
            options={"ordering": ["slug"], "verbose_name": "Bible Badge", "verbose_name_plural": "Bible Badges"},
        ),

        # BibleTranslationCache
        migrations.CreateModel(
            name="BibleTranslationCache",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("translation_id", models.CharField(max_length=20, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("language", models.CharField(blank=True, max_length=60)),
                ("website", models.URLField(blank=True)),
                ("license_url", models.URLField(blank=True)),
                ("short_name", models.CharField(blank=True, max_length=30)),
                ("books_data", models.JSONField(default=list)),
            ],
            options={"ordering": ["translation_id"], "verbose_name": "Cached Translation"},
        ),

        # BibleChapterCache
        migrations.CreateModel(
            name="BibleChapterCache",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("translation", models.CharField(db_index=True, max_length=20)),
                ("book_id", models.CharField(db_index=True, max_length=10)),
                ("book_name", models.CharField(blank=True, max_length=60)),
                ("chapter", models.PositiveSmallIntegerField()),
                ("verses_data", models.JSONField(default=list)),
                ("verse_count", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"verbose_name": "Cached Chapter"},
        ),
        migrations.AddConstraint(
            model_name="biblechaptercache",
            constraint=models.UniqueConstraint(
                fields=["translation", "book_id", "chapter"],
                name="bible_chapter_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="biblechaptercache",
            index=models.Index(fields=["translation", "book_id", "chapter"], name="bible_chapter_lookup_idx"),
        ),

        # ReadingPlan
        migrations.CreateModel(
            name="ReadingPlan",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_readingplan_set", to="tenants.church"
                )),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("frequency", models.CharField(
                    choices=[
                        ("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"),
                        ("quarterly", "Quarterly"), ("yearly", "Yearly"),
                    ],
                    db_index=True, default="daily", max_length=10
                )),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, null=True)),
                ("translation", models.CharField(default="BSB", max_length=20)),
                ("is_published", models.BooleanField(db_index=True, default=False)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_reading_plans",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-start_date"], "verbose_name": "Reading Plan"},
        ),
        migrations.AddIndex(
            model_name="readingplan",
            index=models.Index(fields=["church", "is_published", "-start_date"], name="bible_plan_published_idx"),
        ),

        # ReadingPlanEntry
        migrations.CreateModel(
            name="ReadingPlanEntry",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_readingplanentry_set", to="tenants.church"
                )),
                ("plan", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="entries", to="bible.readingplan"
                )),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("scheduled_date", models.DateField(blank=True, db_index=True, null=True)),
                ("passage_reference", models.CharField(max_length=200)),
                ("book_id", models.CharField(blank=True, max_length=10)),
                ("chapter", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("verse_start", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("verse_end", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
            ],
            options={"ordering": ["order", "scheduled_date"], "verbose_name": "Reading Plan Entry"},
        ),
        migrations.AddIndex(
            model_name="readingplanentry",
            index=models.Index(fields=["plan", "scheduled_date"], name="bible_entry_date_idx"),
        ),
        migrations.AddIndex(
            model_name="readingplanentry",
            index=models.Index(fields=["plan", "order"], name="bible_entry_order_idx"),
        ),

        # MemoryVerse
        migrations.CreateModel(
            name="MemoryVerse",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_memoryverse_set", to="tenants.church"
                )),
                ("reference", models.CharField(max_length=100)),
                ("text", models.TextField()),
                ("translation", models.CharField(default="BSB", max_length=20)),
                ("scope", models.CharField(
                    choices=[("daily", "Daily"), ("weekly", "Weekly")],
                    default="weekly", max_length=10
                )),
                ("active_date", models.DateField(db_index=True)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("is_published", models.BooleanField(db_index=True, default=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_memory_verses",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-active_date"], "verbose_name": "Memory Verse"},
        ),
        migrations.AddIndex(
            model_name="memoryverse",
            index=models.Index(fields=["church", "is_published", "-active_date"], name="bible_memverse_idx"),
        ),

        # BibleStudyReference
        migrations.CreateModel(
            name="BibleStudyReference",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_biblestudyreference_set", to="tenants.church"
                )),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("reference", models.CharField(max_length=200)),
                ("study_notes", models.TextField(blank=True)),
                ("translation", models.CharField(default="BSB", max_length=20)),
                ("active_date", models.DateField(blank=True, db_index=True, null=True)),
                ("is_published", models.BooleanField(db_index=True, default=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_study_refs",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-active_date", "-created_at"], "verbose_name": "Bible Study Reference"},
        ),

        # MemberPlanProgress
        migrations.CreateModel(
            name="MemberPlanProgress",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_memberplanprogress_set", to="tenants.church"
                )),
                ("member", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_plan_progress", to="accounts.churchmember"
                )),
                ("entry", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="completions", to="bible.readingplanentry"
                )),
                ("plan", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="member_progress", to="bible.readingplan"
                )),
                ("completed_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"verbose_name": "Member Plan Progress"},
        ),
        migrations.AlterUniqueTogether(
            name="memberplanprogress",
            unique_together={("member", "entry")},
        ),
        migrations.AddIndex(
            model_name="memberplanprogress",
            index=models.Index(fields=["member", "plan"], name="bible_mpp_member_plan_idx"),
        ),

        # MemberPlanStreak
        migrations.CreateModel(
            name="MemberPlanStreak",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_memberplanstreak_set", to="tenants.church"
                )),
                ("member", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_streaks", to="accounts.churchmember"
                )),
                ("plan", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="member_streaks", to="bible.readingplan"
                )),
                ("current_streak", models.PositiveIntegerField(default=0)),
                ("longest_streak", models.PositiveIntegerField(default=0)),
                ("last_completed_date", models.DateField(blank=True, null=True)),
            ],
            options={"verbose_name": "Member Plan Streak"},
        ),
        migrations.AlterUniqueTogether(
            name="memberplanstreak",
            unique_together={("member", "plan")},
        ),

        # MemberMemoryVerseProgress
        migrations.CreateModel(
            name="MemberMemoryVerseProgress",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_membermemoryprogress_set", to="tenants.church"
                )),
                ("member", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="memory_verse_progress", to="accounts.churchmember"
                )),
                ("verse", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="completions", to="bible.memoryverse"
                )),
                ("completed_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"verbose_name": "Memory Verse Progress"},
        ),
        migrations.AlterUniqueTogether(
            name="membermemoryverseprogress",
            unique_together={("member", "verse")},
        ),

        # MemberStudyProgress
        migrations.CreateModel(
            name="MemberStudyProgress",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_memberstudyprogress_set", to="tenants.church"
                )),
                ("member", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="study_progress", to="accounts.churchmember"
                )),
                ("study", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="completions", to="bible.biblestudyreference"
                )),
                ("completed_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"verbose_name": "Study Reference Progress"},
        ),
        migrations.AlterUniqueTogether(
            name="memberstudyprogress",
            unique_together={("member", "study")},
        ),

        # MemberBadgeAward
        migrations.CreateModel(
            name="MemberBadgeAward",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_memberbadgeaward_set", to="tenants.church"
                )),
                ("member", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_badges", to="accounts.churchmember"
                )),
                ("badge", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="awards", to="bible.biblebadge"
                )),
                ("awarded_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-awarded_at"], "verbose_name": "Badge Award"},
        ),
        migrations.AlterUniqueTogether(
            name="memberbadgeaward",
            unique_together={("member", "badge")},
        ),
        migrations.AddIndex(
            model_name="memberbadgeaward",
            index=models.Index(fields=["church", "member"], name="bible_award_member_idx"),
        ),

        # BibleShareRecord
        migrations.CreateModel(
            name="BibleShareRecord",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("church", models.ForeignKey(
                    db_index=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_biblesharerecord_set", to="tenants.church"
                )),
                ("member", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_shares", to="accounts.churchmember"
                )),
                ("share_type", models.CharField(
                    choices=[("verse", "Bible Verse"), ("progress", "Reading Progress"), ("badge", "Badge")],
                    max_length=10
                )),
                ("destination", models.CharField(
                    choices=[("feed", "Feeds"), ("chat", "Chat")],
                    default="feed", max_length=5
                )),
                ("reference", models.CharField(blank=True, max_length=200)),
                ("verse_text", models.TextField(blank=True)),
                ("translation", models.CharField(blank=True, default="BSB", max_length=20)),
                ("badge", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="shares", to="bible.biblebadge"
                )),
                ("feed_post_id", models.BigIntegerField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Bible Share Record"},
        ),
        migrations.AddIndex(
            model_name="biblesharerecord",
            index=models.Index(fields=["church", "member", "-created_at"], name="bible_share_member_idx"),
        ),
    ]
