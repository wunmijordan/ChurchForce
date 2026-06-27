"""
Migration: bible reader & discussion models
- ReadingPlanEntry: add entry_type, devotional_title, devotional_body, inline_references
- New model: BibleHighlight
- New model: ReadingPlanDiscussion
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bible", "0004_clear_apibible_cache"),
        ("accounts", "0002_initial"),
        ("units", "0002_initial"),
    ]

    operations = [
        # ── ReadingPlanEntry: new fields ────────────────────────────────────
        migrations.AddField(
            model_name="readingplanentry",
            name="entry_type",
            field=models.CharField(
                choices=[("passage", "Bible Passage"), ("devotional", "Devotional / Inspirational")],
                default="passage",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="readingplanentry",
            name="devotional_title",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="readingplanentry",
            name="devotional_body",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="readingplanentry",
            name="inline_references",
            field=models.JSONField(blank=True, default=list),
        ),
        # ── BibleHighlight ──────────────────────────────────────────────────
        migrations.CreateModel(
            name="BibleHighlight",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("translation", models.CharField(default="BSB", max_length=20)),
                ("book_id", models.CharField(db_index=True, max_length=10)),
                ("book_name", models.CharField(blank=True, max_length=60)),
                ("chapter", models.PositiveSmallIntegerField()),
                ("verse_number", models.PositiveSmallIntegerField()),
                ("color", models.CharField(
                    choices=[("yellow","Yellow"),("green","Green"),("blue","Blue"),("pink","Pink"),("purple","Purple")],
                    default="yellow", max_length=10,
                )),
                ("note", models.TextField(blank=True)),
                ("church", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_highlights",
                    to="tenants.church",
                )),
                ("member", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="bible_highlights",
                    to="accounts.churchmember",
                )),
            ],
            options={
                "verbose_name": "Bible Highlight",
                "verbose_name_plural": "Bible Highlights",
            },
        ),
        migrations.AddIndex(
            model_name="biblehighlight",
            index=models.Index(fields=["member", "book_id", "chapter"], name="bible_hl_member_book_ch_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="biblehighlight",
            unique_together={("member", "translation", "book_id", "chapter", "verse_number")},
        ),
        # ── ReadingPlanDiscussion ───────────────────────────────────────────
        migrations.CreateModel(
            name="ReadingPlanDiscussion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("body", models.TextField()),
                ("scope", models.CharField(
                    choices=[("workforce","Whole Workforce"),("unit","Unit / Group")],
                    default="workforce", max_length=10,
                )),
                ("destination", models.CharField(
                    choices=[("chat","Chat Thread"),("feeds","Feeds Post")],
                    default="feeds", max_length=6,
                )),
                ("chat_room_id", models.BigIntegerField(blank=True, null=True)),
                ("chat_message_id", models.BigIntegerField(blank=True, null=True)),
                ("feed_post_id", models.BigIntegerField(blank=True, null=True)),
                ("is_admin_prompt", models.BooleanField(default=False)),
                ("author", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="plan_discussions",
                    to="accounts.churchmember",
                )),
                ("church", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="plan_discussions",
                    to="tenants.church",
                )),
                ("entry", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="discussions",
                    to="bible.readingplanentry",
                )),
                ("plan", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="discussions",
                    to="bible.readingplan",
                )),
                ("unit", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="plan_discussions",
                    to="units.churchunit",
                )),
            ],
            options={
                "verbose_name": "Plan Discussion",
                "verbose_name_plural": "Plan Discussions",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="readingplandiscussion",
            index=models.Index(fields=["plan", "entry"], name="bible_disc_plan_entry_idx"),
        ),
        migrations.AddIndex(
            model_name="readingplandiscussion",
            index=models.Index(fields=["church", "scope"], name="bible_disc_church_scope_idx"),
        ),
    ]
