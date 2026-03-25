# Single "Image" content type; form excludes DELETED visibility (handled in form only)

from django.db import migrations, models


def content_type_image_png_jpeg_to_image(apps, schema_editor):
    """Migrate existing image/png and image/jpeg content_type to image."""
    Entry = apps.get_model("entries", "Entry")
    Entry.objects.filter(content_type__in=["image/png", "image/jpeg"]).update(
        content_type="image"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("entries", "0002_hostedimage"),
    ]

    operations = [
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
        migrations.RunPython(content_type_image_png_jpeg_to_image, migrations.RunPython.noop),
    ]
