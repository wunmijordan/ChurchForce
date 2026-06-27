import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("tenants", "0001_initial"),
        ("workforce", "0002_chatsettings"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatMessageReaction",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("reaction", models.CharField(db_index=True, max_length=24)),
                (
                    "church",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)s_set",
                        to="tenants.church",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_message_reactions",
                        to="accounts.churchmember",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reactions",
                        to="workforce.chatmessage",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["message", "reaction"],
                        name="workforce_c_message_7b8c0e_idx",
                    ),
                    models.Index(
                        fields=["church", "message"],
                        name="workforce_c_church__2a1f8b_idx",
                    ),
                ],
                "unique_together": {("message", "member")},
            },
        ),
    ]
