from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0031_model_pricing_source')]

    operations = [
        migrations.AddField(model_name='modelpricingrule', name='estimate_list_price', field=models.BooleanField(default=False)),
        migrations.AlterField(
            model_name='modelpricingrule', name='kind',
            field=models.CharField(max_length=16, choices=[('manual', 'Manual'), ('alias', 'Alias'),
                                                         ('reference', 'Reference'), ('subscription', 'Subscription')]),
        ),
    ]
