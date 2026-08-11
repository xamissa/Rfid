from django.test import SimpleTestCase

from bridge_core.physical_reader_backend import (
    CachedTCPPhysicalReaderBackend,
)
from bridge_core.reader_backends import (
    FakeReaderBackend,
    get_reader_backend,
)


class ExplicitReaderBackendSelectorTests(SimpleTestCase):
    def test_fake_backend_remains_available_without_contact_permission(self):
        backend = get_reader_backend(
            "fake",
            allow_physical_contact=False,
        )

        self.assertIsInstance(
            backend,
            FakeReaderBackend,
        )

    def test_cached_tcp_backend_is_blocked_without_permission(self):
        with self.assertRaisesMessage(
            ValueError,
            "Physical reader contact is not allowed",
        ):
            get_reader_backend(
                "cached_tcp",
                allow_physical_contact=False,
            )

    def test_cached_tcp_backend_is_returned_with_explicit_permission(self):
        backend = get_reader_backend(
            "cached_tcp",
            allow_physical_contact=True,
        )

        self.assertIsInstance(
            backend,
            CachedTCPPhysicalReaderBackend,
        )

    def test_cached_tcp_name_is_normalized(self):
        backend = get_reader_backend(
            "  CACHED_TCP  ",
            allow_physical_contact=True,
        )

        self.assertIsInstance(
            backend,
            CachedTCPPhysicalReaderBackend,
        )

    def test_generic_physical_name_remains_unsupported(self):
        with self.assertRaisesMessage(
            ValueError,
            "Unsupported reader backend: physical",
        ):
            get_reader_backend(
                "physical",
                allow_physical_contact=True,
            )


class ActiveTCPReaderBackendSelectorTests(SimpleTestCase):
    def test_active_tcp_backend_is_blocked_without_permission(self):
        with self.assertRaisesMessage(
            ValueError,
            "Physical reader contact is not allowed",
        ):
            get_reader_backend(
                "active_tcp",
                allow_physical_contact=False,
            )

    def test_active_tcp_backend_is_returned_with_permission(self):
        from bridge_core.physical_reader_backend import (
            ActiveTCPPhysicalReaderBackend,
        )

        backend = get_reader_backend(
            "active_tcp",
            allow_physical_contact=True,
            scan_seconds=10.0,
        )

        self.assertIsInstance(
            backend,
            ActiveTCPPhysicalReaderBackend,
        )
        self.assertEqual(
            backend._scan_seconds,
            10.0,
        )

    def test_active_tcp_name_is_normalized(self):
        from bridge_core.physical_reader_backend import (
            ActiveTCPPhysicalReaderBackend,
        )

        backend = get_reader_backend(
            "  ACTIVE_TCP  ",
            allow_physical_contact=True,
        )

        self.assertIsInstance(
            backend,
            ActiveTCPPhysicalReaderBackend,
        )
