# Remove redundant description field (use content only)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("entries", "0004_entry_image_urls"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="entry",
            name="description",
        ),
    ]
