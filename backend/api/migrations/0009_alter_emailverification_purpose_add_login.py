from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0008_alter_emailverification_purpose"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emailverification",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("register", "register"),
                    ("login", "login"),
                    ("reset_password", "reset_password"),
                ],
                default="register",
                max_length=16,
            ),
        ),
    ]
