import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('entries', '0020_hostedimage_db_fallback'),
    ]

    operations = [
        migrations.AlterField(
            model_name='entry',
            name='published',
            field=models.DateTimeField(db_index=True, default=django.utils.timezone.now),
        ),
    ]
