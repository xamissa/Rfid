from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the initial RFID Bridge administrator account if missing."

    username = "admin"
    password = "admin"
    email = "admin@rfid.local"

    def handle(self, *args, **options):
        user_model = get_user_model()

        user, created = user_model.objects.get_or_create(
            username=self.username,
            defaults={
                "email": self.email,
                "is_staff": True,
                "is_superuser": True,
            },
        )

        if created:
            user.set_password(self.password)
            user.save()

            self.stdout.write(
                self.style.SUCCESS(
                    "PASS: Initial admin account created."
                )
            )
            return

        changed = False

        if not user.is_staff:
            user.is_staff = True
            changed = True

        if not user.is_superuser:
            user.is_superuser = True
            changed = True

        if changed:
            user.save()

        self.stdout.write(
            self.style.SUCCESS(
                "PASS: Initial admin account already exists."
            )
        )
