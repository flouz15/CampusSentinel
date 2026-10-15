# Generated manually — remove OpenClaw integration

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0010_governanceauditrecord"),
    ]

    operations = [
        migrations.DeleteModel(name="OpenClawInboundLog"),
        migrations.DeleteModel(name="OpenClawOutbox"),
    ]
