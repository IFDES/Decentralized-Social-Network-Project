# Add optional image_urls (text above, images below - Twitter style)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entries", "0003_entry_single_image_choice_no_deleted_in_form"),
    ]

    operations = [
        migrations.AddField(
            model_name="entry",
            name="image_urls",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of image URLs to display below the main content.",
            ),
        ),
    ]
