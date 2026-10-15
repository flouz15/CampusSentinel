import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Report',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('location', models.CharField(blank=True, max_length=255)),
                ('time', models.DateTimeField()),
                ('note', models.TextField(blank=True)),
                ('status', models.CharField(default='PENDING_REVIEW', max_length=32)),
                ('image', models.ImageField(blank=True, null=True, upload_to='reports/')),
                ('category', models.CharField(default='未分类', max_length=64)),
                ('priority', models.CharField(default='P2', max_length=8)),
                ('reporter_name', models.CharField(default='匿名', max_length=64)),
                ('reporter_role', models.CharField(default='学生', max_length=32)),
                ('reporter_contact', models.CharField(blank=True, max_length=64)),
                ('department', models.CharField(blank=True, max_length=64)),
                ('latitude', models.FloatField(blank=True, null=True)),
                ('longitude', models.FloatField(blank=True, null=True)),
                ('confidence', models.IntegerField(blank=True, null=True)),
                ('evidence_urls', models.JSONField(blank=True, default=list)),
                ('handled_by', models.CharField(blank=True, max_length=64)),
                ('handled_at', models.DateTimeField(blank=True, null=True)),
                ('violation', models.BooleanField(blank=True, null=True)),
                ('reward_points', models.IntegerField(blank=True, null=True)),
                ('reward_message', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['-time']},
        ),
    ]
