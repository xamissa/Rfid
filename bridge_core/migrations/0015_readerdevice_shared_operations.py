from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bridge_core", "0014_add_active_tcp_poc_backend"),
    ]

    operations = [
        migrations.AddField(
            model_name="readerdevice",
            name="shared_operations",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Allow this physical reader to serve both Receiving "
                    "and Dispatch sessions. Keep disabled for dedicated "
                    "readers."
                ),
            ),
        ),
    ]
