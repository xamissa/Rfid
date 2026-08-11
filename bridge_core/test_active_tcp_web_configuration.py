from django.test import TestCase, override_settings

from bridge_core.forms import (
    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE,
    PocRuntimeControlForm,
)
from bridge_core.models import OperationalConfiguration
from bridge_core.views import (
    build_system_readiness_checks,
    effective_poc_reader_backend,
    effective_reader_backend,
)


class ActiveTCPWebConfigurationTests(TestCase):
    def setUp(self):
        self.configuration = OperationalConfiguration.objects.get(
            name="default"
        )

    def test_active_tcp_is_registered_model_choice(self):
        self.assertIn(
            OperationalConfiguration.PocReaderBackend.ACTIVE_TCP,
            OperationalConfiguration.PocReaderBackend.values,
        )

        labels = dict(
            OperationalConfiguration.PocReaderBackend.choices
        )

        self.assertEqual(
            labels[
                OperationalConfiguration
                .PocReaderBackend
                .ACTIVE_TCP
            ],
            "Active TCP reader",
        )

    def test_form_accepts_active_tcp_with_reader_permission(self):
        form = PocRuntimeControlForm(
            data={
                "poc_reader_backend": "active_tcp",
                "poc_allow_physical_reader_contact": "on",
                "poc_allow_odoo_contact": "",
                "confirmation": (
                    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE
                ),
            },
            instance=self.configuration,
        )

        self.assertTrue(
            form.is_valid(),
            form.errors.as_json(),
        )

        saved = form.save()

        self.assertEqual(
            saved.poc_reader_backend,
            OperationalConfiguration
            .PocReaderBackend
            .ACTIVE_TCP,
        )
        self.assertTrue(
            saved.poc_allow_physical_reader_contact
        )
        self.assertFalse(saved.poc_allow_odoo_contact)

    def test_form_blocks_fake_backend_with_reader_permission(self):
        form = PocRuntimeControlForm(
            data={
                "poc_reader_backend": "fake",
                "poc_allow_physical_reader_contact": "on",
                "poc_allow_odoo_contact": "",
                "confirmation": (
                    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE
                ),
            },
            instance=self.configuration,
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            "poc_reader_backend",
            form.errors,
        )

    def test_readiness_accepts_active_tcp_as_physical_backend(self):
        self.configuration.poc_reader_backend = (
            OperationalConfiguration
            .PocReaderBackend
            .ACTIVE_TCP
        )

        readiness = build_system_readiness_checks(
            self.configuration,
            reader_device_count=1,
            enabled_reader_device_count=1,
        )

        check = next(
            item
            for item in readiness["checks"]
            if item["key"] == "physical_reader_backend"
        )

        self.assertTrue(check["ready"])
        self.assertEqual(
            check["label"],
            "Physical RFID reader backend selected",
        )

    @override_settings(
        READER_BACKEND="fake",
        ALLOW_PHYSICAL_READER_CONTACT=False,
    )
    def test_database_active_tcp_backend_is_effective(self):
        self.configuration.poc_reader_backend = (
            OperationalConfiguration
            .PocReaderBackend
            .ACTIVE_TCP
        )
        self.configuration.poc_allow_physical_reader_contact = True

        self.assertEqual(
            effective_reader_backend(self.configuration),
            "active_tcp",
        )
        self.assertEqual(
            effective_poc_reader_backend(self.configuration),
            "active_tcp",
        )

    @override_settings(
        READER_BACKEND="fake",
        ALLOW_PHYSICAL_READER_CONTACT=True,
    )
    def test_environment_contact_fallback_remains_cached_tcp(self):
        self.configuration.poc_reader_backend = (
            OperationalConfiguration.PocReaderBackend.FAKE
        )
        self.configuration.poc_allow_physical_reader_contact = False

        self.assertEqual(
            effective_reader_backend(self.configuration),
            "cached_tcp",
        )
        self.assertEqual(
            effective_poc_reader_backend(self.configuration),
            "cached_tcp",
        )

    @override_settings(
        READER_BACKEND="fake",
        ALLOW_PHYSICAL_READER_CONTACT=False,
    )
    def test_fail_closed_state_remains_fake(self):
        self.configuration.poc_reader_backend = (
            OperationalConfiguration.PocReaderBackend.FAKE
        )
        self.configuration.poc_allow_physical_reader_contact = False

        self.assertEqual(
            effective_reader_backend(self.configuration),
            "fake",
        )
        self.assertEqual(
            effective_poc_reader_backend(self.configuration),
            "fake",
        )
