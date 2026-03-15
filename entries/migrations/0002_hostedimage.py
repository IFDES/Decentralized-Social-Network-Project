# Generated manually for node image hosting

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authors", "0001_initial"),
        ("entries", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="HostedImage",
            fields=[
                (
                    "uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("file", models.ImageField(upload_to="entries/images/%Y/%m/")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hosted_images",
                        to="authors.author",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AlterField(
            model_name="entry",
            name="content_type",
            field=models.CharField(
                choices=[
                    ("text/plain", "Plain text"),
                    ("text/markdown", "CommonMark"),
                    ("image", "Image"),
                ],
                default="text/plain",
                max_length=64,
            ),
        ),
    ]
