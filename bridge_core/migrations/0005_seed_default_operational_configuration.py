from django.db import migrations


def create_default_configuration(apps, schema_editor):
    OperationalConfiguration = apps.get_model(
        "bridge_core",
        "OperationalConfiguration",
    )

    OperationalConfiguration.objects.get_or_create(
        name="default",
        defaults={
            "worker_batch_size": 50,
            "max_delivery_attempts": 10,
            "retry_initial_seconds": 30,
            "retry_max_seconds": 3600,
            "event_retention_days": 90,
            "notes": "Initial offline operational defaults.",
        },
    )


def remove_default_configuration(apps, schema_editor):
    OperationalConfiguration = apps.get_model(
        "bridge_core",
        "OperationalConfiguration",
    )

    OperationalConfiguration.objects.filter(name="default").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("bridge_core", "0004_operationalconfiguration"),
    ]

    operations = [
        migrations.RunPython(
            create_default_configuration,
            remove_default_configuration,
        ),
    ]
