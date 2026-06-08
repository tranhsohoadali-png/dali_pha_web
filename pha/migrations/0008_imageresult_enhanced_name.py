from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pha', '0007_paintstock_price_per_kg_productionlog_cost'),
    ]

    operations = [
        migrations.AddField(
            model_name='imageresult',
            name='enhanced_name',
            field=models.TextField(blank=True, default=''),
        ),
    ]
