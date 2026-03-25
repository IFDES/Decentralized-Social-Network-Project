import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Author",
            fields=[
                ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("fqid", models.URLField(blank=True, max_length=500, null=True, unique=True)),
                ("host", models.URLField(blank=True, max_length=500)),
                ("web", models.URLField(blank=True, max_length=500)),
                ("is_local", models.BooleanField(default=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("display_name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True)),
                ("github", models.URLField(blank=True)),
                ("profile_image", models.URLField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
