from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase, TestCase

from bridge_core.management.commands.run_rfid_worker import Command

from bridge_core.reader_backends import (
    FakeReaderBackend,
    TechnicalRFIDRead,
    get_reader_backend,
)


class FakeReaderBackendTests(SimpleTestCase):
    def test_default_backend_emits_no_reads(self):
        backend = FakeReaderBackend()

        self.assertEqual(backend.read_events(device=object()), ())

    def test_backend_returns_explicit_reads_without_modifying_them(self):
        read = TechnicalRFIDRead(
            reader_event_key="fake-event-001",
            epc="E2000017221101441890ABCD",
            raw_payload='{"source":"explicit-test"}',
        )
        backend = FakeReaderBackend(reads=(read,))

        self.assertEqual(backend.read_events(device=object()), (read,))
        self.assertEqual(read.reader_event_key, "fake-event-001")
        self.assertEqual(read.epc, "E2000017221101441890ABCD")

    def test_backend_selector_returns_fake_backend(self):
        backend = get_reader_backend("fake")

        self.assertIsInstance(backend, FakeReaderBackend)
        self.assertEqual(backend.read_events(device=object()), ())

    def test_backend_selector_normalizes_safe_input(self):
        backend = get_reader_backend("  FAKE  ")

        self.assertIsInstance(backend, FakeReaderBackend)

    def test_backend_selector_fails_closed_for_unsupported_backend(self):
        with self.assertRaisesMessage(
            ValueError,
            "Unsupported reader backend: physical",
        ):
            get_reader_backend("physical")


class RFIDWorkerReaderBackendTests(SimpleTestCase):
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "OperationalConfiguration.objects.get"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "ReaderDevice.objects.filter"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "run_device_ingestion_cycle"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "get_sender_backend"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "get_reader_backend"
    )
    def test_offline_cycle_runs_per_device_ingestion_coordinator(
        self,
        mocked_get_reader_backend,
        mocked_get_sender_backend,
        mocked_run_cycle,
        mocked_device_filter,
        mocked_configuration_get,
    ):
        mocked_configuration_get.return_value = SimpleNamespace(
            worker_batch_size=50,
            max_delivery_attempts=10,
        )

        first_device = SimpleNamespace(
            code="reader-one",
            enabled=True,
        )
        second_device = SimpleNamespace(
            code="reader-two",
            enabled=True,
        )

        mocked_queryset = mocked_device_filter.return_value
        mocked_queryset.order_by.return_value = (
            first_device,
            second_device,
        )

        mocked_backend = mocked_get_reader_backend.return_value
        mocked_run_cycle.return_value = SimpleNamespace(
            device_count=2,
            received_count=3,
            created_count=2,
            duplicate_count=1,
            assignment_selected_count=2,
            assigned_count=1,
            unassigned_count=1,
            assignment_failed_count=0,
        )

        command = Command()
        command.stdout = StringIO()

        command._validate_offline_cycle(1)

        mocked_device_filter.assert_called_once_with(enabled=True)
        mocked_queryset.order_by.assert_called_once_with("code")
        mocked_get_reader_backend.assert_called_once_with("fake")
        mocked_get_sender_backend.assert_called_once_with("disabled")
        mocked_run_cycle.assert_called_once_with(
            devices=(first_device, second_device),
            reader_backend=mocked_backend,
        )

        output = command.stdout.getvalue()

        self.assertIn("ENABLED_READER_DEVICES=2", output)
        self.assertIn("TECHNICAL_READS=3", output)
        self.assertIn("CREATED_EVENTS=2", output)
        self.assertIn("DUPLICATE_EVENTS=1", output)
        self.assertIn("ASSIGNMENT_SELECTED=2", output)
        self.assertIn("ASSIGNED_EVENTS=1", output)
        self.assertIn("UNASSIGNED_EVENTS=1", output)
        self.assertIn("ASSIGNMENT_FAILED=0", output)
        self.assertIn(
            "HOLD: Odoo contact remains disabled",
            output,
        )


class TechnicalReadIngestionTests(SimpleTestCase):
    @patch("bridge_core.ingestion.RawRFIDEvent.objects.get_or_create")
    def test_disabled_reader_fails_closed(self, mocked_get_or_create):
        from bridge_core.ingestion import ingest_technical_reads

        device = SimpleNamespace(enabled=False)

        with self.assertRaisesMessage(
            ValueError,
            "Reader device must be enabled for ingestion.",
        ):
            ingest_technical_reads(
                device=device,
                technical_reads=(),
            )

        mocked_get_or_create.assert_not_called()

    @patch("bridge_core.ingestion.RawRFIDEvent.objects.get_or_create")
    def test_new_read_is_created_as_unassigned(self, mocked_get_or_create):
        from bridge_core.ingestion import ingest_technical_reads

        device = SimpleNamespace(enabled=True)
        read = TechnicalRFIDRead(
            reader_event_key="fake-event-001",
            epc="E2000017221101441890ABCD",
            raw_payload='{"source":"explicit-test"}',
        )
        created_event = SimpleNamespace(
            event_id="created-event-uuid-001",
        )
        mocked_get_or_create.return_value = (created_event, True)

        result = ingest_technical_reads(
            device=device,
            technical_reads=(read,),
        )

        mocked_get_or_create.assert_called_once_with(
            device=device,
            reader_event_key="fake-event-001",
            defaults={
                "epc": "E2000017221101441890ABCD",
                "raw_payload": '{"source":"explicit-test"}',
                "queue_state": "unassigned",
            },
        )
        self.assertEqual(result.received_count, 1)
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.duplicate_count, 0)
        self.assertEqual(
            result.created_event_ids,
            ("created-event-uuid-001",),
        )

    @patch("bridge_core.ingestion.RawRFIDEvent.objects.get_or_create")
    def test_existing_read_is_counted_as_duplicate(
        self,
        mocked_get_or_create,
    ):
        from bridge_core.ingestion import ingest_technical_reads

        device = SimpleNamespace(enabled=True)
        read = TechnicalRFIDRead(
            reader_event_key="fake-event-001",
            epc="E2000017221101441890ABCD",
            raw_payload='{"source":"explicit-test"}',
        )
        existing_event = SimpleNamespace(
            event_id="existing-event-uuid-001",
        )
        mocked_get_or_create.return_value = (existing_event, False)

        result = ingest_technical_reads(
            device=device,
            technical_reads=(read,),
        )

        self.assertEqual(result.received_count, 1)
        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.created_event_ids, ())


class PerDeviceWorkerCycleTests(SimpleTestCase):
    def test_cycle_ingests_and_assigns_each_enabled_device(self):
        from bridge_core.worker_cycle import run_device_ingestion_cycle

        first_device = SimpleNamespace(code="reader-one", enabled=True)
        second_device = SimpleNamespace(code="reader-two", enabled=True)

        backend = SimpleNamespace(
            read_events=Mock(),
        )

        first_reads = (
            TechnicalRFIDRead(
                reader_event_key="event-001",
                epc="EPC-001",
                raw_payload='{"reader":"one"}',
            ),
        )
        second_reads = ()

        backend.read_events.side_effect = (
            first_reads,
            second_reads,
        )

        ingestion_function = Mock(
            side_effect=(
                SimpleNamespace(
                    received_count=1,
                    created_count=1,
                    duplicate_count=0,
                    created_event_ids=("event-uuid-001",),
                ),
                SimpleNamespace(
                    received_count=0,
                    created_count=0,
                    duplicate_count=0,
                    created_event_ids=(),
                ),
            ),
        )
        assignment_function = Mock(
            side_effect=(
                SimpleNamespace(
                    selected_count=1,
                    assigned_count=1,
                    unassigned_count=0,
                    failed_count=0,
                ),
                SimpleNamespace(
                    selected_count=0,
                    assigned_count=0,
                    unassigned_count=0,
                    failed_count=0,
                ),
            ),
        )

        result = run_device_ingestion_cycle(
            devices=(first_device, second_device),
            reader_backend=backend,
            ingestion_function=ingestion_function,
            assignment_function=assignment_function,
        )

        self.assertEqual(
            backend.read_events.call_args_list,
            [
                call(device=first_device),
                call(device=second_device),
            ],
        )
        self.assertEqual(
            assignment_function.call_args_list,
            [
                call(event_ids=("event-uuid-001",)),
                call(event_ids=()),
            ],
        )
        self.assertEqual(result.device_count, 2)
        self.assertEqual(result.received_count, 1)
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.duplicate_count, 0)
        self.assertEqual(result.assignment_selected_count, 1)
        self.assertEqual(result.assigned_count, 1)
        self.assertEqual(result.unassigned_count, 0)
        self.assertEqual(result.assignment_failed_count, 0)

    def test_cycle_aggregates_assignment_outcomes_across_devices(self):
        from bridge_core.worker_cycle import run_device_ingestion_cycle

        first_device = SimpleNamespace(code="reader-one", enabled=True)
        second_device = SimpleNamespace(code="reader-two", enabled=True)

        backend = SimpleNamespace(
            read_events=Mock(side_effect=((), ())),
        )
        ingestion_function = Mock(
            side_effect=(
                SimpleNamespace(
                    received_count=2,
                    created_count=2,
                    duplicate_count=0,
                    created_event_ids=(
                        "event-uuid-101",
                        "event-uuid-102",
                    ),
                ),
                SimpleNamespace(
                    received_count=2,
                    created_count=1,
                    duplicate_count=1,
                    created_event_ids=("event-uuid-103",),
                ),
            ),
        )
        assignment_function = Mock(
            side_effect=(
                SimpleNamespace(
                    selected_count=2,
                    assigned_count=1,
                    unassigned_count=1,
                    failed_count=0,
                ),
                SimpleNamespace(
                    selected_count=1,
                    assigned_count=0,
                    unassigned_count=0,
                    failed_count=1,
                ),
            ),
        )

        result = run_device_ingestion_cycle(
            devices=(first_device, second_device),
            reader_backend=backend,
            ingestion_function=ingestion_function,
            assignment_function=assignment_function,
        )

        self.assertEqual(result.device_count, 2)
        self.assertEqual(result.received_count, 4)
        self.assertEqual(result.created_count, 3)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.assignment_selected_count, 3)
        self.assertEqual(result.assigned_count, 1)
        self.assertEqual(result.unassigned_count, 1)
        self.assertEqual(result.assignment_failed_count, 1)

    def test_cycle_rejects_disabled_device_before_ingestion_or_assignment(
        self,
    ):
        from bridge_core.worker_cycle import run_device_ingestion_cycle

        disabled_device = SimpleNamespace(
            code="disabled-reader",
            enabled=False,
        )
        backend = SimpleNamespace(read_events=Mock())
        ingestion_function = Mock()
        assignment_function = Mock()

        with self.assertRaisesMessage(
            ValueError,
            "Worker cycle received a disabled reader device.",
        ):
            run_device_ingestion_cycle(
                devices=(disabled_device,),
                reader_backend=backend,
                ingestion_function=ingestion_function,
                assignment_function=assignment_function,
            )

        backend.read_events.assert_not_called()
        ingestion_function.assert_not_called()
        assignment_function.assert_not_called()

class DashboardDiagnosticsTests(TestCase):
    @patch("bridge_core.views.OperationalConfiguration.objects.count")
    @patch("bridge_core.views.DeliveryAttempt.objects.count")
    @patch("bridge_core.views.RawRFIDEvent.objects.filter")
    @patch("bridge_core.views.RawRFIDEvent.objects.count")
    @patch("bridge_core.views.ReaderDevice.objects.filter")
    @patch("bridge_core.views.ReaderDevice.objects.count")
    def test_dashboard_context_contains_reader_and_queue_counts(
        self,
        mocked_reader_count,
        mocked_reader_filter,
        mocked_event_count,
        mocked_event_filter,
        mocked_attempt_count,
        mocked_configuration_count,
    ):
        from bridge_core.views import build_dashboard_context

        mocked_reader_count.return_value = 3
        mocked_reader_filter.side_effect = (
            SimpleNamespace(count=Mock(return_value=2)),
            SimpleNamespace(count=Mock(return_value=1)),
        )
        mocked_event_count.return_value = 28
        mocked_event_filter.side_effect = [
            SimpleNamespace(count=Mock(return_value=value))
            for value in (1, 2, 3, 4, 5, 6, 7)
        ]
        mocked_attempt_count.return_value = 9
        mocked_configuration_count.return_value = 1

        context = build_dashboard_context()

        self.assertEqual(context["reader_device_count"], 3)
        self.assertEqual(context["enabled_reader_device_count"], 2)
        self.assertEqual(context["disabled_reader_device_count"], 1)
        self.assertEqual(context["raw_event_count"], 28)
        self.assertEqual(
            context["queue_state_counts"],
            {
                "unassigned": 1,
                "queued": 2,
                "inflight": 3,
                "retry": 4,
                "sent": 5,
                "rejected": 6,
                "dead": 7,
            },
        )
        self.assertEqual(context["delivery_attempt_count"], 9)
        self.assertEqual(
            context["operational_configuration_count"],
            1,
        )


class FakeReadInjectionCommandTests(SimpleTestCase):
    @patch(
        "bridge_core.management.commands.inject_fake_rfid_read."
        "ingest_technical_reads"
    )
    @patch(
        "bridge_core.management.commands.inject_fake_rfid_read."
        "ReaderDevice.objects.get"
    )
    def test_dry_run_does_not_ingest(
        self,
        mocked_device_get,
        mocked_ingest,
    ):
        from io import StringIO

        from bridge_core.management.commands.inject_fake_rfid_read import (
            Command,
        )

        mocked_device_get.return_value = SimpleNamespace(
            code="receiving-door",
            role="receiving",
            enabled=True,
        )

        command = Command()
        command.stdout = StringIO()

        command.handle(
            device_code="receiving-door",
            event_key="fake-event-001",
            epc="EPC-001",
            raw_payload='{"source":"test"}',
            apply=False,
        )

        output = command.stdout.getvalue()

        mocked_ingest.assert_not_called()
        self.assertIn("MODE=dry-run", output)
        self.assertIn(
            "HOLD: Dry-run complete; no database event was created",
            output,
        )

    @patch(
        "bridge_core.management.commands.inject_fake_rfid_read."
        "ingest_technical_reads"
    )
    @patch(
        "bridge_core.management.commands.inject_fake_rfid_read."
        "ReaderDevice.objects.get"
    )
    def test_apply_ingests_one_explicit_read(
        self,
        mocked_device_get,
        mocked_ingest,
    ):
        from io import StringIO

        from bridge_core.management.commands.inject_fake_rfid_read import (
            Command,
        )

        device = SimpleNamespace(
            code="dispatch-door",
            role="dispatch",
            enabled=True,
        )
        mocked_device_get.return_value = device
        mocked_ingest.return_value = SimpleNamespace(
            received_count=1,
            created_count=1,
            duplicate_count=0,
        )

        command = Command()
        command.stdout = StringIO()

        command.handle(
            device_code="dispatch-door",
            event_key="fake-event-002",
            epc="EPC-002",
            raw_payload='{"source":"test"}',
            apply=True,
        )

        output = command.stdout.getvalue()

        mocked_ingest.assert_called_once()
        call_kwargs = mocked_ingest.call_args.kwargs

        self.assertIs(call_kwargs["device"], device)
        self.assertEqual(len(call_kwargs["technical_reads"]), 1)
        self.assertEqual(
            call_kwargs["technical_reads"][0].reader_event_key,
            "fake-event-002",
        )
        self.assertIn("MODE=apply", output)
        self.assertIn("CREATED_EVENTS=1", output)
        self.assertIn(
            "PASS: Explicit fake RFID read processed safely",
            output,
        )


class QueuePolicyTests(SimpleTestCase):
    def test_allowed_queue_transitions(self):
        from bridge_core.models import RawRFIDEvent
        from bridge_core.queue_policy import validate_queue_transition

        allowed_transitions = (
            ("unassigned", "queued"),
            ("unassigned", "rejected"),
            ("queued", "inflight"),
            ("queued", "rejected"),
            ("queued", "dead"),
            ("inflight", "sent"),
            ("inflight", "retry"),
            ("inflight", "rejected"),
            ("inflight", "dead"),
            ("retry", "inflight"),
            ("retry", "dead"),
        )

        for current_state, target_state in allowed_transitions:
            with self.subTest(
                current_state=current_state,
                target_state=target_state,
            ):
                validate_queue_transition(
                    current_state=current_state,
                    target_state=target_state,
                )

        self.assertEqual(
            set(RawRFIDEvent.QueueState.values),
            {
                "unassigned",
                "queued",
                "inflight",
                "retry",
                "sent",
                "rejected",
                "dead",
            },
        )

    def test_illegal_queue_transition_is_rejected(self):
        from bridge_core.queue_policy import validate_queue_transition

        with self.assertRaisesMessage(
            ValueError,
            "Illegal queue transition: unassigned -> sent",
        ):
            validate_queue_transition(
                current_state="unassigned",
                target_state="sent",
            )

    def test_terminal_state_cannot_transition(self):
        from bridge_core.queue_policy import validate_queue_transition

        with self.assertRaisesMessage(
            ValueError,
            "Illegal queue transition: sent -> retry",
        ):
            validate_queue_transition(
                current_state="sent",
                target_state="retry",
            )

    def test_same_state_transition_is_rejected(self):
        from bridge_core.queue_policy import validate_queue_transition

        with self.assertRaisesMessage(
            ValueError,
            "Queue transition cannot remain in state: queued",
        ):
            validate_queue_transition(
                current_state="queued",
                target_state="queued",
            )

    def test_retry_delay_uses_exponential_backoff_and_cap(self):
        from bridge_core.queue_policy import (
            calculate_retry_delay_seconds,
        )

        self.assertEqual(
            calculate_retry_delay_seconds(
                attempt_number=1,
                initial_seconds=30,
                maximum_seconds=3600,
            ),
            30,
        )
        self.assertEqual(
            calculate_retry_delay_seconds(
                attempt_number=2,
                initial_seconds=30,
                maximum_seconds=3600,
            ),
            60,
        )
        self.assertEqual(
            calculate_retry_delay_seconds(
                attempt_number=8,
                initial_seconds=30,
                maximum_seconds=3600,
            ),
            3600,
        )
        self.assertEqual(
            calculate_retry_delay_seconds(
                attempt_number=20,
                initial_seconds=30,
                maximum_seconds=3600,
            ),
            3600,
        )

    def test_retry_delay_rejects_invalid_configuration(self):
        from bridge_core.queue_policy import (
            calculate_retry_delay_seconds,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Attempt number must be at least 1.",
        ):
            calculate_retry_delay_seconds(
                attempt_number=0,
                initial_seconds=30,
                maximum_seconds=3600,
            )

        with self.assertRaisesMessage(
            ValueError,
            "Initial retry seconds must be at least 1.",
        ):
            calculate_retry_delay_seconds(
                attempt_number=1,
                initial_seconds=0,
                maximum_seconds=3600,
            )

        with self.assertRaisesMessage(
            ValueError,
            (
                "Maximum retry seconds cannot be less than "
                "initial seconds."
            ),
        ):
            calculate_retry_delay_seconds(
                attempt_number=1,
                initial_seconds=30,
                maximum_seconds=20,
            )


class QueueTransitionServiceTests(SimpleTestCase):
    @patch("bridge_core.queue_service.transaction.atomic")
    @patch(
        "bridge_core.queue_service."
        "validate_queue_transition"
    )
    @patch(
        "bridge_core.queue_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_transition_locks_validates_and_updates_event(
        self,
        mocked_select_for_update,
        mocked_validate_transition,
        mocked_atomic,
    ):
        from bridge_core.queue_service import (
            transition_event_queue_state,
        )

        event = SimpleNamespace(
            event_id="event-uuid-001",
            queue_state="queued",
            save=Mock(),
        )
        locked_queryset = SimpleNamespace(
            get=Mock(return_value=event),
        )
        mocked_select_for_update.return_value = locked_queryset
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        result = transition_event_queue_state(
            event_id="event-uuid-001",
            expected_state="queued",
            target_state="inflight",
        )

        mocked_select_for_update.assert_called_once_with()
        locked_queryset.get.assert_called_once_with(
            event_id="event-uuid-001",
        )
        mocked_validate_transition.assert_called_once_with(
            current_state="queued",
            target_state="inflight",
        )
        event.save.assert_called_once_with(
            update_fields=(
                "queue_state",
                "updated_at",
            ),
        )

        self.assertEqual(event.queue_state, "inflight")
        self.assertEqual(result.event_id, "event-uuid-001")
        self.assertEqual(result.previous_state, "queued")
        self.assertEqual(result.current_state, "inflight")

    @patch("bridge_core.queue_service.transaction.atomic")
    @patch(
        "bridge_core.queue_service."
        "validate_queue_transition"
    )
    @patch(
        "bridge_core.queue_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_stale_expected_state_is_rejected_before_update(
        self,
        mocked_select_for_update,
        mocked_validate_transition,
        mocked_atomic,
    ):
        from bridge_core.queue_service import (
            transition_event_queue_state,
        )

        event = SimpleNamespace(
            event_id="event-uuid-002",
            queue_state="retry",
            save=Mock(),
        )
        locked_queryset = SimpleNamespace(
            get=Mock(return_value=event),
        )
        mocked_select_for_update.return_value = locked_queryset
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        with self.assertRaisesMessage(
            ValueError,
            (
                "Stale queue state expectation: "
                "expected queued, found retry"
            ),
        ):
            transition_event_queue_state(
                event_id="event-uuid-002",
                expected_state="queued",
                target_state="inflight",
            )

        mocked_validate_transition.assert_not_called()
        event.save.assert_not_called()
        self.assertEqual(event.queue_state, "retry")

    @patch("bridge_core.queue_service.transaction.atomic")
    @patch(
        "bridge_core.queue_service."
        "validate_queue_transition"
    )
    @patch(
        "bridge_core.queue_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_policy_failure_prevents_database_update(
        self,
        mocked_select_for_update,
        mocked_validate_transition,
        mocked_atomic,
    ):
        from bridge_core.queue_service import (
            transition_event_queue_state,
        )

        event = SimpleNamespace(
            event_id="event-uuid-003",
            queue_state="unassigned",
            save=Mock(),
        )
        locked_queryset = SimpleNamespace(
            get=Mock(return_value=event),
        )
        mocked_select_for_update.return_value = locked_queryset
        mocked_validate_transition.side_effect = ValueError(
            "Illegal queue transition: unassigned -> sent"
        )
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        with self.assertRaisesMessage(
            ValueError,
            "Illegal queue transition: unassigned -> sent",
        ):
            transition_event_queue_state(
                event_id="event-uuid-003",
                expected_state="unassigned",
                target_state="sent",
            )

        event.save.assert_not_called()
        self.assertEqual(event.queue_state, "unassigned")


class DeliveryAttemptStartServiceTests(SimpleTestCase):
    @patch("bridge_core.delivery_service.transaction.atomic")
    @patch(
        "bridge_core.delivery_service.validate_queue_transition"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.create"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.filter"
    )
    @patch(
        "bridge_core.delivery_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_first_attempt_locks_event_and_sets_inflight(
        self,
        mocked_select_for_update,
        mocked_attempt_filter,
        mocked_attempt_create,
        mocked_validate_transition,
        mocked_atomic,
    ):
        from bridge_core.delivery_service import (
            start_delivery_attempt,
        )

        event = SimpleNamespace(
            event_id="event-uuid-101",
            queue_state="queued",
            save=Mock(),
        )
        locked_queryset = SimpleNamespace(
            get=Mock(return_value=event),
        )
        attempt_queryset = SimpleNamespace(
            order_by=Mock(),
        )
        attempt_queryset.order_by.return_value = SimpleNamespace(
            first=Mock(return_value=None),
        )
        attempt = SimpleNamespace(
            id=501,
            attempt_number=1,
        )

        mocked_select_for_update.return_value = locked_queryset
        mocked_attempt_filter.return_value = attempt_queryset
        mocked_attempt_create.return_value = attempt
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        result = start_delivery_attempt(
            event_id="event-uuid-101",
            expected_state="queued",
            max_delivery_attempts=10,
        )

        mocked_select_for_update.assert_called_once_with()
        locked_queryset.get.assert_called_once_with(
            event_id="event-uuid-101",
        )
        mocked_attempt_filter.assert_called_once_with(event=event)
        attempt_queryset.order_by.assert_called_once_with(
            "-attempt_number",
        )
        mocked_validate_transition.assert_called_once_with(
            current_state="queued",
            target_state="inflight",
        )
        event.save.assert_called_once_with(
            update_fields=(
                "queue_state",
                "updated_at",
            ),
        )
        mocked_attempt_create.assert_called_once_with(
            event=event,
            attempt_number=1,
            outcome="started",
        )

        self.assertEqual(result.attempt_id, 501)
        self.assertEqual(result.attempt_number, 1)
        self.assertEqual(result.previous_state, "queued")
        self.assertEqual(result.current_state, "inflight")

    @patch("bridge_core.delivery_service.transaction.atomic")
    @patch(
        "bridge_core.delivery_service.validate_queue_transition"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.create"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.filter"
    )
    @patch(
        "bridge_core.delivery_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_retry_allocates_next_attempt_number(
        self,
        mocked_select_for_update,
        mocked_attempt_filter,
        mocked_attempt_create,
        mocked_validate_transition,
        mocked_atomic,
    ):
        from bridge_core.delivery_service import (
            start_delivery_attempt,
        )

        event = SimpleNamespace(
            event_id="event-uuid-102",
            queue_state="retry",
            save=Mock(),
        )
        previous_attempt = SimpleNamespace(attempt_number=2)
        locked_queryset = SimpleNamespace(
            get=Mock(return_value=event),
        )
        attempt_queryset = SimpleNamespace(
            order_by=Mock(),
        )
        attempt_queryset.order_by.return_value = SimpleNamespace(
            first=Mock(return_value=previous_attempt),
        )
        attempt = SimpleNamespace(
            id=502,
            attempt_number=3,
        )

        mocked_select_for_update.return_value = locked_queryset
        mocked_attempt_filter.return_value = attempt_queryset
        mocked_attempt_create.return_value = attempt
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        result = start_delivery_attempt(
            event_id="event-uuid-102",
            expected_state="retry",
            max_delivery_attempts=10,
        )

        mocked_validate_transition.assert_called_once_with(
            current_state="retry",
            target_state="inflight",
        )
        mocked_attempt_create.assert_called_once_with(
            event=event,
            attempt_number=3,
            outcome="started",
        )
        self.assertEqual(result.attempt_number, 3)
        self.assertEqual(result.previous_state, "retry")
        self.assertEqual(result.current_state, "inflight")

    @patch("bridge_core.delivery_service.transaction.atomic")
    @patch(
        "bridge_core.delivery_service.validate_queue_transition"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.create"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.filter"
    )
    @patch(
        "bridge_core.delivery_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_maximum_attempts_prevents_state_change(
        self,
        mocked_select_for_update,
        mocked_attempt_filter,
        mocked_attempt_create,
        mocked_validate_transition,
        mocked_atomic,
    ):
        from bridge_core.delivery_service import (
            start_delivery_attempt,
        )

        event = SimpleNamespace(
            event_id="event-uuid-103",
            queue_state="retry",
            save=Mock(),
        )
        previous_attempt = SimpleNamespace(attempt_number=10)
        locked_queryset = SimpleNamespace(
            get=Mock(return_value=event),
        )
        attempt_queryset = SimpleNamespace(
            order_by=Mock(),
        )
        attempt_queryset.order_by.return_value = SimpleNamespace(
            first=Mock(return_value=previous_attempt),
        )

        mocked_select_for_update.return_value = locked_queryset
        mocked_attempt_filter.return_value = attempt_queryset
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        with self.assertRaisesMessage(
            ValueError,
            "Maximum delivery attempts reached: 10",
        ):
            start_delivery_attempt(
                event_id="event-uuid-103",
                expected_state="retry",
                max_delivery_attempts=10,
            )

        mocked_validate_transition.assert_not_called()
        event.save.assert_not_called()
        mocked_attempt_create.assert_not_called()
        self.assertEqual(event.queue_state, "retry")

    @patch("bridge_core.delivery_service.transaction.atomic")
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.create"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.filter"
    )
    @patch(
        "bridge_core.delivery_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_stale_expected_state_prevents_attempt_creation(
        self,
        mocked_select_for_update,
        mocked_attempt_filter,
        mocked_attempt_create,
        mocked_atomic,
    ):
        from bridge_core.delivery_service import (
            start_delivery_attempt,
        )

        event = SimpleNamespace(
            event_id="event-uuid-104",
            queue_state="inflight",
            save=Mock(),
        )
        locked_queryset = SimpleNamespace(
            get=Mock(return_value=event),
        )

        mocked_select_for_update.return_value = locked_queryset
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        with self.assertRaisesMessage(
            ValueError,
            (
                "Stale queue state expectation: "
                "expected queued, found inflight"
            ),
        ):
            start_delivery_attempt(
                event_id="event-uuid-104",
                expected_state="queued",
                max_delivery_attempts=10,
            )

        mocked_attempt_filter.assert_not_called()
        mocked_attempt_create.assert_not_called()
        event.save.assert_not_called()


class DeliveryAttemptCompletionServiceTests(SimpleTestCase):
    @patch("bridge_core.delivery_service.transaction.atomic")
    @patch("bridge_core.delivery_service.timezone.now")
    @patch(
        "bridge_core.delivery_service.validate_queue_transition"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.select_for_update"
    )
    @patch(
        "bridge_core.delivery_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_sent_completion_updates_attempt_and_event(
        self,
        mocked_event_select,
        mocked_attempt_select,
        mocked_validate_transition,
        mocked_now,
        mocked_atomic,
    ):
        from datetime import datetime, timezone as datetime_timezone

        from bridge_core.delivery_service import (
            complete_delivery_attempt,
        )

        completed_at = datetime(
            2026,
            7,
            17,
            8,
            0,
            tzinfo=datetime_timezone.utc,
        )
        event = SimpleNamespace(
            event_id="event-uuid-201",
            queue_state="inflight",
            save=Mock(),
        )
        attempt = SimpleNamespace(
            id=601,
            attempt_number=1,
            outcome="started",
            response_code="",
            error_kind="",
            detail="",
            completed_at=None,
            next_retry_at=None,
            save=Mock(),
        )

        mocked_event_select.return_value = SimpleNamespace(
            get=Mock(return_value=event),
        )
        mocked_attempt_select.return_value = SimpleNamespace(
            get=Mock(return_value=attempt),
        )
        mocked_now.return_value = completed_at
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        result = complete_delivery_attempt(
            event_id="event-uuid-201",
            attempt_id=601,
            outcome="sent",
            response_code="200",
            detail="Accepted",
        )

        mocked_validate_transition.assert_called_once_with(
            current_state="inflight",
            target_state="sent",
        )
        self.assertEqual(event.queue_state, "sent")
        self.assertEqual(attempt.outcome, "sent")
        self.assertEqual(attempt.response_code, "200")
        self.assertEqual(attempt.detail, "Accepted")
        self.assertEqual(attempt.completed_at, completed_at)
        self.assertIsNone(attempt.next_retry_at)
        self.assertEqual(result.current_state, "sent")
        self.assertEqual(result.outcome, "sent")

    @patch("bridge_core.delivery_service.transaction.atomic")
    @patch("bridge_core.delivery_service.timezone.now")
    @patch(
        "bridge_core.delivery_service."
        "calculate_retry_delay_seconds"
    )
    @patch(
        "bridge_core.delivery_service.validate_queue_transition"
    )
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.select_for_update"
    )
    @patch(
        "bridge_core.delivery_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_retry_completion_schedules_next_retry(
        self,
        mocked_event_select,
        mocked_attempt_select,
        mocked_validate_transition,
        mocked_retry_delay,
        mocked_now,
        mocked_atomic,
    ):
        from datetime import datetime, timedelta, timezone as datetime_timezone

        from bridge_core.delivery_service import (
            complete_delivery_attempt,
        )

        completed_at = datetime(
            2026,
            7,
            17,
            8,
            0,
            tzinfo=datetime_timezone.utc,
        )
        event = SimpleNamespace(
            event_id="event-uuid-202",
            queue_state="inflight",
            save=Mock(),
        )
        attempt = SimpleNamespace(
            id=602,
            attempt_number=2,
            outcome="started",
            response_code="",
            error_kind="",
            detail="",
            completed_at=None,
            next_retry_at=None,
            save=Mock(),
        )

        mocked_event_select.return_value = SimpleNamespace(
            get=Mock(return_value=event),
        )
        mocked_attempt_select.return_value = SimpleNamespace(
            get=Mock(return_value=attempt),
        )
        mocked_retry_delay.return_value = 60
        mocked_now.return_value = completed_at
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        result = complete_delivery_attempt(
            event_id="event-uuid-202",
            attempt_id=602,
            outcome="retry",
            error_kind="timeout",
            detail="Temporary timeout",
            retry_initial_seconds=30,
            retry_max_seconds=3600,
        )

        mocked_retry_delay.assert_called_once_with(
            attempt_number=2,
            initial_seconds=30,
            maximum_seconds=3600,
        )
        self.assertEqual(event.queue_state, "retry")
        self.assertEqual(attempt.outcome, "retry")
        self.assertEqual(
            attempt.next_retry_at,
            completed_at + timedelta(seconds=60),
        )
        self.assertEqual(result.current_state, "retry")
        self.assertEqual(result.outcome, "retry")

    @patch("bridge_core.delivery_service.transaction.atomic")
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.select_for_update"
    )
    @patch(
        "bridge_core.delivery_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_non_inflight_event_is_rejected(
        self,
        mocked_event_select,
        mocked_attempt_select,
        mocked_atomic,
    ):
        from bridge_core.delivery_service import (
            complete_delivery_attempt,
        )

        event = SimpleNamespace(
            event_id="event-uuid-203",
            queue_state="retry",
        )

        mocked_event_select.return_value = SimpleNamespace(
            get=Mock(return_value=event),
        )
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        with self.assertRaisesMessage(
            ValueError,
            (
                "Delivery attempt can only complete while event "
                "is inflight; found retry"
            ),
        ):
            complete_delivery_attempt(
                event_id="event-uuid-203",
                attempt_id=603,
                outcome="dead",
            )

        mocked_attempt_select.assert_not_called()

    @patch("bridge_core.delivery_service.transaction.atomic")
    @patch(
        "bridge_core.delivery_service."
        "DeliveryAttempt.objects.select_for_update"
    )
    @patch(
        "bridge_core.delivery_service."
        "RawRFIDEvent.objects.select_for_update"
    )
    def test_completed_attempt_cannot_complete_twice(
        self,
        mocked_event_select,
        mocked_attempt_select,
        mocked_atomic,
    ):
        from bridge_core.delivery_service import (
            complete_delivery_attempt,
        )

        event = SimpleNamespace(
            event_id="event-uuid-204",
            queue_state="inflight",
        )
        attempt = SimpleNamespace(
            id=604,
            attempt_number=1,
            outcome="retry",
        )

        mocked_event_select.return_value = SimpleNamespace(
            get=Mock(return_value=event),
        )
        mocked_attempt_select.return_value = SimpleNamespace(
            get=Mock(return_value=attempt),
        )
        mocked_atomic.return_value = Mock(
            __enter__=Mock(return_value=None),
            __exit__=Mock(return_value=False),
        )

        with self.assertRaisesMessage(
            ValueError,
            (
                "Delivery attempt is already completed with "
                "outcome: retry"
            ),
        ):
            complete_delivery_attempt(
                event_id="event-uuid-204",
                attempt_id=604,
                outcome="sent",
            )

    def test_retry_requires_retry_configuration(self):
        from bridge_core.delivery_service import (
            complete_delivery_attempt,
        )

        with self.assertRaisesMessage(
            ValueError,
            (
                "Retry completion requires "
                "retry_initial_seconds."
            ),
        ):
            complete_delivery_attempt(
                event_id="event-uuid-205",
                attempt_id=605,
                outcome="retry",
            )

    def test_unsupported_outcome_is_rejected(self):
        from bridge_core.delivery_service import (
            complete_delivery_attempt,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Unsupported delivery completion outcome: started",
        ):
            complete_delivery_attempt(
                event_id="event-uuid-206",
                attempt_id=606,
                outcome="started",
            )


class DisabledSenderBackendTests(SimpleTestCase):
    def test_disabled_backend_blocks_delivery(self):
        from bridge_core.sender_backends import DisabledSenderBackend

        backend = DisabledSenderBackend()

        with self.assertRaisesMessage(
            RuntimeError,
            "Delivery is disabled; no external contact is permitted.",
        ):
            backend.send_event(event=object())

    def test_backend_selector_returns_disabled_backend(self):
        from bridge_core.sender_backends import (
            DisabledSenderBackend,
            get_sender_backend,
        )

        backend = get_sender_backend("disabled")

        self.assertIsInstance(backend, DisabledSenderBackend)

    def test_backend_selector_normalizes_safe_input(self):
        from bridge_core.sender_backends import (
            DisabledSenderBackend,
            get_sender_backend,
        )

        backend = get_sender_backend("  DISABLED  ")

        self.assertIsInstance(backend, DisabledSenderBackend)

    def test_backend_selector_fails_closed_for_unsupported_backend(self):
        from bridge_core.sender_backends import get_sender_backend

        with self.assertRaisesMessage(
            ValueError,
            "Unsupported sender backend: odoo",
        ):
            get_sender_backend("odoo")


class DeliverySenderResultTests(SimpleTestCase):
    def test_sent_result_preserves_completion_metadata(self):
        from bridge_core.sender_backends import DeliverySenderResult

        result = DeliverySenderResult(
            outcome="sent",
            response_code="200",
            detail="Accepted",
        )

        self.assertEqual(result.outcome, "sent")
        self.assertEqual(result.response_code, "200")
        self.assertEqual(result.error_kind, "")
        self.assertEqual(result.detail, "Accepted")

    def test_retry_result_preserves_error_metadata(self):
        from bridge_core.sender_backends import DeliverySenderResult

        result = DeliverySenderResult(
            outcome="retry",
            response_code="503",
            error_kind="temporary_unavailable",
            detail="Temporary failure",
        )

        self.assertEqual(result.outcome, "retry")
        self.assertEqual(result.response_code, "503")
        self.assertEqual(
            result.error_kind,
            "temporary_unavailable",
        )
        self.assertEqual(result.detail, "Temporary failure")

    def test_all_supported_terminal_outcomes_are_accepted(self):
        from bridge_core.sender_backends import DeliverySenderResult

        for outcome in (
            "sent",
            "retry",
            "rejected",
            "dead",
        ):
            with self.subTest(outcome=outcome):
                result = DeliverySenderResult(outcome=outcome)
                self.assertEqual(result.outcome, outcome)

    def test_started_outcome_is_rejected(self):
        from bridge_core.sender_backends import DeliverySenderResult

        with self.assertRaisesMessage(
            ValueError,
            "Unsupported sender result outcome: started",
        ):
            DeliverySenderResult(outcome="started")

    def test_unknown_outcome_is_rejected(self):
        from bridge_core.sender_backends import DeliverySenderResult

        with self.assertRaisesMessage(
            ValueError,
            "Unsupported sender result outcome: unknown",
        ):
            DeliverySenderResult(outcome="unknown")


class DeliveryCandidateSelectionTests(SimpleTestCase):
    def test_batch_size_must_be_positive(self):
        from bridge_core.delivery_cycle import (
            select_delivery_candidates,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Delivery candidate batch size must be at least 1.",
        ):
            select_delivery_candidates(batch_size=0)

    @patch("bridge_core.delivery_cycle.RawRFIDEvent.objects")
    @patch("bridge_core.delivery_cycle.DeliveryAttempt.objects")
    @patch("bridge_core.delivery_cycle.Subquery")
    def test_selection_builds_ordered_limited_candidate_query(
        self,
        mocked_subquery,
        mocked_attempt_objects,
        mocked_event_objects,
    ):
        from datetime import datetime, timezone as datetime_timezone

        from bridge_core.delivery_cycle import (
            select_delivery_candidates,
        )

        now = datetime(
            2026,
            7,
            17,
            11,
            0,
            tzinfo=datetime_timezone.utc,
        )

        latest_attempts = Mock()
        mocked_attempt_objects.filter.return_value = latest_attempts
        latest_attempts.order_by.return_value = latest_attempts

        outcome_values = Mock()
        retry_values = Mock()

        def values_side_effect(field_name):
            if field_name == "outcome":
                return outcome_values
            if field_name == "next_retry_at":
                return retry_values
            raise AssertionError(
                f"Unexpected values field: {field_name}"
            )

        latest_attempts.values.side_effect = values_side_effect
        outcome_slice = object()
        retry_slice = object()

        outcome_values.__getitem__ = Mock(
            return_value=outcome_slice
        )
        retry_values.__getitem__ = Mock(
            return_value=retry_slice
        )
        mocked_subquery.side_effect = (
            "latest-outcome-subquery",
            "latest-retry-subquery",
        )

        queryset = Mock()
        annotated_queryset = Mock()
        filtered_queryset = Mock()
        related_queryset = Mock()
        ordered_queryset = Mock()

        mocked_event_objects.annotate.return_value = annotated_queryset
        annotated_queryset.filter.return_value = filtered_queryset
        filtered_queryset.select_related.return_value = related_queryset
        related_queryset.order_by.return_value = ordered_queryset
        ordered_queryset.__getitem__ = Mock(
            return_value=("candidate-one", "candidate-two")
        )

        result = select_delivery_candidates(
            batch_size=2,
            now=now,
        )

        mocked_attempt_objects.filter.assert_called_once()
        mocked_subquery.assert_has_calls(
            (
                call(outcome_slice),
                call(retry_slice),
            )
        )
        latest_attempts.order_by.assert_called_once_with(
            "-attempt_number"
        )
        mocked_event_objects.annotate.assert_called_once()
        annotated_queryset.filter.assert_called_once()
        filtered_queryset.select_related.assert_called_once_with(
            "device"
        )
        related_queryset.order_by.assert_called_once_with(
            "received_at",
            "id",
        )
        ordered_queryset.__getitem__.assert_called_once_with(
            slice(None, 2, None)
        )
        self.assertEqual(
            result,
            ("candidate-one", "candidate-two"),
        )


class SingleEventDeliveryProcessingTests(SimpleTestCase):
    def test_sent_result_starts_sends_and_completes_attempt(self):
        from bridge_core.delivery_cycle import (
            process_delivery_candidate,
        )
        from bridge_core.sender_backends import DeliverySenderResult

        event = SimpleNamespace(
            event_id="event-uuid-301",
            queue_state="queued",
        )
        sender_backend = SimpleNamespace(
            send_event=Mock(
                return_value=DeliverySenderResult(
                    outcome="sent",
                    response_code="200",
                    detail="Accepted",
                )
            )
        )
        start_function = Mock(
            return_value=SimpleNamespace(
                attempt_id=701,
                attempt_number=1,
            )
        )
        complete_function = Mock(
            return_value=SimpleNamespace(
                event_id="event-uuid-301",
                attempt_id=701,
                attempt_number=1,
                outcome="sent",
                current_state="sent",
                next_retry_at=None,
            )
        )

        result = process_delivery_candidate(
            event=event,
            sender_backend=sender_backend,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
            start_function=start_function,
            complete_function=complete_function,
        )

        start_function.assert_called_once_with(
            event_id="event-uuid-301",
            expected_state="queued",
            max_delivery_attempts=10,
        )
        sender_backend.send_event.assert_called_once_with(event=event)
        complete_function.assert_called_once_with(
            event_id="event-uuid-301",
            attempt_id=701,
            outcome="sent",
            response_code="200",
            error_kind="",
            detail="Accepted",
            retry_initial_seconds=30,
            retry_max_seconds=3600,
        )
        self.assertEqual(result.outcome, "sent")
        self.assertEqual(result.current_state, "sent")
        self.assertIsNone(result.next_retry_at)

    def test_retry_result_preserves_sender_error_metadata(self):
        from bridge_core.delivery_cycle import (
            process_delivery_candidate,
        )
        from bridge_core.sender_backends import DeliverySenderResult

        event = SimpleNamespace(
            event_id="event-uuid-302",
            queue_state="retry",
        )
        sender_backend = SimpleNamespace(
            send_event=Mock(
                return_value=DeliverySenderResult(
                    outcome="retry",
                    response_code="503",
                    error_kind="temporary_unavailable",
                    detail="Try again later",
                )
            )
        )
        start_function = Mock(
            return_value=SimpleNamespace(
                attempt_id=702,
                attempt_number=2,
            )
        )
        retry_at = object()
        complete_function = Mock(
            return_value=SimpleNamespace(
                event_id="event-uuid-302",
                attempt_id=702,
                attempt_number=2,
                outcome="retry",
                current_state="retry",
                next_retry_at=retry_at,
            )
        )

        result = process_delivery_candidate(
            event=event,
            sender_backend=sender_backend,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
            start_function=start_function,
            complete_function=complete_function,
        )

        complete_function.assert_called_once_with(
            event_id="event-uuid-302",
            attempt_id=702,
            outcome="retry",
            response_code="503",
            error_kind="temporary_unavailable",
            detail="Try again later",
            retry_initial_seconds=30,
            retry_max_seconds=3600,
        )
        self.assertEqual(result.outcome, "retry")
        self.assertIs(result.next_retry_at, retry_at)

    def test_sender_exception_is_completed_as_retry(self):
        from bridge_core.delivery_cycle import (
            process_delivery_candidate,
        )

        event = SimpleNamespace(
            event_id="event-uuid-303",
            queue_state="queued",
        )
        sender_backend = SimpleNamespace(
            send_event=Mock(
                side_effect=RuntimeError(
                    "Delivery is disabled; no external contact is permitted."
                )
            )
        )
        start_function = Mock(
            return_value=SimpleNamespace(
                attempt_id=703,
                attempt_number=1,
            )
        )
        complete_function = Mock(
            return_value=SimpleNamespace(
                event_id="event-uuid-303",
                attempt_id=703,
                attempt_number=1,
                outcome="retry",
                current_state="retry",
                next_retry_at=object(),
            )
        )

        result = process_delivery_candidate(
            event=event,
            sender_backend=sender_backend,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
            start_function=start_function,
            complete_function=complete_function,
        )

        complete_function.assert_called_once_with(
            event_id="event-uuid-303",
            attempt_id=703,
            outcome="retry",
            response_code="",
            error_kind="RuntimeError",
            detail=(
                "Delivery is disabled; "
                "no external contact is permitted."
            ),
            retry_initial_seconds=30,
            retry_max_seconds=3600,
        )
        self.assertEqual(result.outcome, "retry")
        self.assertEqual(result.current_state, "retry")


class BatchDeliveryCycleTests(SimpleTestCase):
    def test_invalid_configuration_is_rejected_before_selection(self):
        from bridge_core.delivery_cycle import run_batch_delivery_cycle

        selection_function = Mock()

        with self.assertRaisesMessage(
            ValueError,
            "Delivery batch size must be at least 1.",
        ):
            run_batch_delivery_cycle(
                sender_backend=object(),
                batch_size=0,
                max_delivery_attempts=10,
                retry_initial_seconds=30,
                retry_max_seconds=3600,
                selection_function=selection_function,
            )

        selection_function.assert_not_called()

    def test_batch_processes_candidates_and_counts_outcomes(self):
        from bridge_core.delivery_cycle import run_batch_delivery_cycle

        events = (
            SimpleNamespace(event_id="event-401"),
            SimpleNamespace(event_id="event-402"),
            SimpleNamespace(event_id="event-403"),
            SimpleNamespace(event_id="event-404"),
        )
        selection_function = Mock(return_value=events)
        processing_function = Mock(
            side_effect=(
                SimpleNamespace(outcome="sent"),
                SimpleNamespace(outcome="retry"),
                SimpleNamespace(outcome="rejected"),
                SimpleNamespace(outcome="dead"),
            )
        )
        sender_backend = object()

        result = run_batch_delivery_cycle(
            sender_backend=sender_backend,
            batch_size=4,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
            selection_function=selection_function,
            processing_function=processing_function,
        )

        selection_function.assert_called_once_with(batch_size=4)
        self.assertEqual(processing_function.call_count, 4)

        for event, processing_call in zip(
            events,
            processing_function.call_args_list,
        ):
            self.assertEqual(
                processing_call,
                call(
                    event=event,
                    sender_backend=sender_backend,
                    max_delivery_attempts=10,
                    retry_initial_seconds=30,
                    retry_max_seconds=3600,
                ),
            )

        self.assertEqual(result.selected_count, 4)
        self.assertEqual(result.processed_count, 4)
        self.assertEqual(result.sent_count, 1)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(result.rejected_count, 1)
        self.assertEqual(result.dead_count, 1)
        self.assertEqual(result.failed_count, 0)

    def test_processing_failure_is_counted_and_batch_continues(self):
        from bridge_core.delivery_cycle import run_batch_delivery_cycle

        events = (
            SimpleNamespace(event_id="event-405"),
            SimpleNamespace(event_id="event-406"),
            SimpleNamespace(event_id="event-407"),
        )
        processing_function = Mock(
            side_effect=(
                RuntimeError("stale event"),
                SimpleNamespace(outcome="sent"),
                SimpleNamespace(outcome="retry"),
            )
        )

        result = run_batch_delivery_cycle(
            sender_backend=object(),
            batch_size=3,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
            selection_function=Mock(return_value=events),
            processing_function=processing_function,
        )

        self.assertEqual(processing_function.call_count, 3)
        self.assertEqual(result.selected_count, 3)
        self.assertEqual(result.processed_count, 2)
        self.assertEqual(result.sent_count, 1)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(result.dead_count, 0)
        self.assertEqual(result.failed_count, 1)

    def test_empty_selection_returns_zero_counts(self):
        from bridge_core.delivery_cycle import run_batch_delivery_cycle

        processing_function = Mock()

        result = run_batch_delivery_cycle(
            sender_backend=object(),
            batch_size=50,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
            selection_function=Mock(return_value=()),
            processing_function=processing_function,
        )

        processing_function.assert_not_called()
        self.assertEqual(result.selected_count, 0)
        self.assertEqual(result.processed_count, 0)
        self.assertEqual(result.sent_count, 0)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(result.dead_count, 0)
        self.assertEqual(result.failed_count, 0)


class RFIDWorkerDeliveryIntegrationTests(SimpleTestCase):
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "run_batch_delivery_cycle"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "OperationalConfiguration.objects.get"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "ReaderDevice.objects.filter"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "run_device_ingestion_cycle"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "get_sender_backend"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "get_reader_backend"
    )
    def test_delivery_processing_requires_explicit_opt_in(
        self,
        mocked_get_reader_backend,
        mocked_get_sender_backend,
        mocked_ingestion_cycle,
        mocked_device_filter,
        mocked_configuration_get,
        mocked_delivery_cycle,
    ):
        mocked_configuration_get.return_value = SimpleNamespace(
            worker_batch_size=50,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
        )
        mocked_device_filter.return_value.order_by.return_value = ()
        mocked_ingestion_cycle.return_value = SimpleNamespace(
            device_count=0,
            received_count=0,
            created_count=0,
            duplicate_count=0,
            assignment_selected_count=0,
            assigned_count=0,
            unassigned_count=0,
            assignment_failed_count=0,
        )

        command = Command()
        command.stdout = StringIO()

        command._validate_offline_cycle(1)

        mocked_delivery_cycle.assert_not_called()
        self.assertIn(
            "DELIVERY_PROCESSING=disabled",
            command.stdout.getvalue(),
        )

    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "run_batch_delivery_cycle"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "OperationalConfiguration.objects.get"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "ReaderDevice.objects.filter"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "run_device_ingestion_cycle"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "get_sender_backend"
    )
    @patch(
        "bridge_core.management.commands.run_rfid_worker."
        "get_reader_backend"
    )
    def test_explicit_opt_in_runs_batch_delivery_cycle(
        self,
        mocked_get_reader_backend,
        mocked_get_sender_backend,
        mocked_ingestion_cycle,
        mocked_device_filter,
        mocked_configuration_get,
        mocked_delivery_cycle,
    ):
        configuration = SimpleNamespace(
            worker_batch_size=50,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
        )
        mocked_configuration_get.return_value = configuration
        mocked_device_filter.return_value.order_by.return_value = ()
        mocked_ingestion_cycle.return_value = SimpleNamespace(
            device_count=0,
            received_count=0,
            created_count=0,
            duplicate_count=0,
            assignment_selected_count=0,
            assigned_count=0,
            unassigned_count=0,
            assignment_failed_count=0,
        )
        mocked_delivery_cycle.return_value = SimpleNamespace(
            selected_count=3,
            processed_count=2,
            sent_count=1,
            retry_count=1,
            rejected_count=0,
            dead_count=0,
            failed_count=1,
        )
        sender_backend = mocked_get_sender_backend.return_value

        command = Command()
        command.stdout = StringIO()

        command._validate_offline_cycle(
            1,
            process_delivery=True,
        )

        mocked_delivery_cycle.assert_called_once_with(
            sender_backend=sender_backend,
            batch_size=50,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
        )

        output = command.stdout.getvalue()
        self.assertIn("DELIVERY_PROCESSING=enabled", output)
        self.assertIn("DELIVERY_SELECTED=3", output)
        self.assertIn("DELIVERY_PROCESSED=2", output)
        self.assertIn("DELIVERY_SENT=1", output)
        self.assertIn("DELIVERY_RETRY=1", output)
        self.assertIn("DELIVERY_FAILED=1", output)


class RFIDSessionModelContractTests(SimpleTestCase):
    def test_session_contract_is_endpoint_neutral(self):
        from bridge_core.models import RFIDSession

        self.assertEqual(
            RFIDSession.OperationType.values,
            ["receipt", "dispatch"],
        )
        self.assertEqual(
            RFIDSession.Status.values,
            ["active", "closed", "cancelled"],
        )

        self.assertEqual(
            RFIDSession._meta.get_field("odoo_model").default,
            "stock.picking",
        )
        self.assertEqual(
            RFIDSession._meta.get_field("status").default,
            "active",
        )

    def test_only_one_active_session_is_allowed_per_device(self):
        from bridge_core.models import RFIDSession

        constraint_names = {
            constraint.name
            for constraint in RFIDSession._meta.constraints
        }

        self.assertIn(
            "unique_active_rfid_session_per_device",
            constraint_names,
        )

    def test_session_relationship_does_not_auto_assign_events(self):
        from bridge_core.models import RawRFIDEvent

        field = RawRFIDEvent._meta.get_field("rfid_session")

        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertFalse(field.has_default())


class RawRFIDEventSessionRelationshipTests(SimpleTestCase):
    def test_session_relationship_is_nullable_and_protected(self):
        from django.db.models.deletion import PROTECT

        from bridge_core.models import RawRFIDEvent, RFIDSession

        field = RawRFIDEvent._meta.get_field("rfid_session")

        self.assertIs(field.remote_field.model, RFIDSession)
        self.assertIs(field.remote_field.on_delete, PROTECT)
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertEqual(
            field.remote_field.related_name,
            "raw_events",
        )

    def test_unassigned_event_remains_valid_without_session(self):
        from bridge_core.models import RawRFIDEvent

        field = RawRFIDEvent._meta.get_field("rfid_session")

        self.assertFalse(field.has_default())
        self.assertEqual(
            RawRFIDEvent._meta.get_field("queue_state").default,
            RawRFIDEvent.QueueState.UNASSIGNED,
        )


class ActiveSessionAssignmentServiceTests(SimpleTestCase):
    @patch(
        "bridge_core.session_assignment."
        "transaction.atomic"
    )
    @patch(
        "bridge_core.session_assignment."
        "validate_queue_transition"
    )
    @patch(
        "bridge_core.session_assignment."
        "RFIDSession.objects"
    )
    @patch(
        "bridge_core.session_assignment."
        "RawRFIDEvent.objects"
    )
    def test_receiving_event_is_assigned_and_queued_atomically(
        self,
        mocked_event_objects,
        mocked_session_objects,
        mocked_validate_transition,
        mocked_atomic,
    ):
        from bridge_core.models import RawRFIDEvent, ReaderDevice, RFIDSession
        from bridge_core.session_assignment import (
            assign_event_to_active_session,
        )

        device = SimpleNamespace(
            id=41,
            role=ReaderDevice.Role.RECEIVING,
        )
        event = SimpleNamespace(
            event_id="event-uuid-001",
            device_id=device.id,
            device=device,
            rfid_session_id=None,
            rfid_session=None,
            queue_state=RawRFIDEvent.QueueState.UNASSIGNED,
            save=Mock(),
        )
        session = SimpleNamespace(
            session_id="session-uuid-001",
            operation_type=RFIDSession.OperationType.RECEIPT,
        )

        event_lock = mocked_event_objects.select_for_update.return_value
        event_query = event_lock.select_related.return_value
        event_query.get.return_value = event

        session_lock = (
            mocked_session_objects.select_for_update.return_value
        )
        session_lock.get.return_value = session

        result = assign_event_to_active_session(
            event_id="event-uuid-001",
        )

        event_query.get.assert_called_once_with(
            event_id="event-uuid-001",
        )
        session_lock.get.assert_called_once_with(
            device_id=41,
            status=RFIDSession.Status.ACTIVE,
        )
        mocked_validate_transition.assert_called_once_with(
            current_state=RawRFIDEvent.QueueState.UNASSIGNED,
            target_state=RawRFIDEvent.QueueState.QUEUED,
        )

        self.assertIs(event.rfid_session, session)
        self.assertEqual(
            event.queue_state,
            RawRFIDEvent.QueueState.QUEUED,
        )
        event.save.assert_called_once_with(
            update_fields=(
                "rfid_session",
                "queue_state",
                "updated_at",
            ),
        )

        self.assertEqual(result.event_id, "event-uuid-001")
        self.assertEqual(result.session_id, "session-uuid-001")
        self.assertEqual(
            result.previous_state,
            RawRFIDEvent.QueueState.UNASSIGNED,
        )
        self.assertEqual(
            result.current_state,
            RawRFIDEvent.QueueState.QUEUED,
        )

    @patch(
        "bridge_core.session_assignment."
        "transaction.atomic"
    )
    @patch(
        "bridge_core.session_assignment."
        "RFIDSession.objects"
    )
    @patch(
        "bridge_core.session_assignment."
        "RawRFIDEvent.objects"
    )
    def test_non_unassigned_event_is_rejected_before_session_lookup(
        self,
        mocked_event_objects,
        mocked_session_objects,
        mocked_atomic,
    ):
        from bridge_core.models import RawRFIDEvent
        from bridge_core.session_assignment import (
            assign_event_to_active_session,
        )

        event = SimpleNamespace(
            queue_state=RawRFIDEvent.QueueState.QUEUED,
            rfid_session_id=None,
        )

        event_lock = mocked_event_objects.select_for_update.return_value
        event_query = event_lock.select_related.return_value
        event_query.get.return_value = event

        with self.assertRaisesMessage(
            ValueError,
            "Event must be unassigned before session assignment.",
        ):
            assign_event_to_active_session(event_id="event-uuid-002")

        mocked_session_objects.select_for_update.assert_not_called()

    @patch(
        "bridge_core.session_assignment."
        "transaction.atomic"
    )
    @patch(
        "bridge_core.session_assignment."
        "RFIDSession.objects"
    )
    @patch(
        "bridge_core.session_assignment."
        "RawRFIDEvent.objects"
    )
    def test_existing_session_assignment_is_rejected(
        self,
        mocked_event_objects,
        mocked_session_objects,
        mocked_atomic,
    ):
        from bridge_core.models import RawRFIDEvent
        from bridge_core.session_assignment import (
            assign_event_to_active_session,
        )

        event = SimpleNamespace(
            queue_state=RawRFIDEvent.QueueState.UNASSIGNED,
            rfid_session_id=99,
        )

        event_lock = mocked_event_objects.select_for_update.return_value
        event_query = event_lock.select_related.return_value
        event_query.get.return_value = event

        with self.assertRaisesMessage(
            ValueError,
            "Event already has an RFID session assignment.",
        ):
            assign_event_to_active_session(event_id="event-uuid-003")

        mocked_session_objects.select_for_update.assert_not_called()

    @patch(
        "bridge_core.session_assignment."
        "transaction.atomic"
    )
    @patch(
        "bridge_core.session_assignment."
        "validate_queue_transition"
    )
    @patch(
        "bridge_core.session_assignment."
        "RFIDSession.objects"
    )
    @patch(
        "bridge_core.session_assignment."
        "RawRFIDEvent.objects"
    )
    def test_incompatible_session_operation_is_rejected_without_save(
        self,
        mocked_event_objects,
        mocked_session_objects,
        mocked_validate_transition,
        mocked_atomic,
    ):
        from bridge_core.models import RawRFIDEvent, ReaderDevice, RFIDSession
        from bridge_core.session_assignment import (
            assign_event_to_active_session,
        )

        device = SimpleNamespace(
            id=42,
            role=ReaderDevice.Role.RECEIVING,
        )
        event = SimpleNamespace(
            device_id=device.id,
            device=device,
            queue_state=RawRFIDEvent.QueueState.UNASSIGNED,
            rfid_session_id=None,
            save=Mock(),
        )
        session = SimpleNamespace(
            operation_type=RFIDSession.OperationType.DISPATCH,
        )

        event_lock = mocked_event_objects.select_for_update.return_value
        event_query = event_lock.select_related.return_value
        event_query.get.return_value = event

        session_lock = (
            mocked_session_objects.select_for_update.return_value
        )
        session_lock.get.return_value = session

        with self.assertRaisesMessage(
            ValueError,
            "Active RFID session operation is incompatible "
            "with the reader device role.",
        ):
            assign_event_to_active_session(event_id="event-uuid-004")

        mocked_validate_transition.assert_not_called()
        event.save.assert_not_called()

class ActiveSessionAssignmentCycleTests(SimpleTestCase):
    def test_cycle_assigns_each_selected_event(self):
        from bridge_core.session_assignment_cycle import (
            run_active_session_assignment_cycle,
        )

        assignment_function = Mock()

        result = run_active_session_assignment_cycle(
            event_ids=(
                "event-uuid-101",
                "event-uuid-102",
            ),
            assignment_function=assignment_function,
        )

        self.assertEqual(
            assignment_function.call_args_list,
            [
                call(event_id="event-uuid-101"),
                call(event_id="event-uuid-102"),
            ],
        )
        self.assertEqual(result.selected_count, 2)
        self.assertEqual(result.assigned_count, 2)
        self.assertEqual(result.unassigned_count, 0)
        self.assertEqual(result.failed_count, 0)

    def test_missing_active_session_leaves_event_unassigned_and_continues(
        self,
    ):
        from bridge_core.models import RFIDSession
        from bridge_core.session_assignment_cycle import (
            run_active_session_assignment_cycle,
        )

        assignment_function = Mock(
            side_effect=(
                RFIDSession.DoesNotExist,
                object(),
            ),
        )

        result = run_active_session_assignment_cycle(
            event_ids=(
                "event-uuid-201",
                "event-uuid-202",
            ),
            assignment_function=assignment_function,
        )

        self.assertEqual(
            assignment_function.call_args_list,
            [
                call(event_id="event-uuid-201"),
                call(event_id="event-uuid-202"),
            ],
        )
        self.assertEqual(result.selected_count, 2)
        self.assertEqual(result.assigned_count, 1)
        self.assertEqual(result.unassigned_count, 1)
        self.assertEqual(result.failed_count, 0)

    def test_unexpected_assignment_failure_is_counted_and_continues(
        self,
    ):
        from bridge_core.session_assignment_cycle import (
            run_active_session_assignment_cycle,
        )

        assignment_function = Mock(
            side_effect=(
                ValueError("incompatible assignment"),
                object(),
            ),
        )

        result = run_active_session_assignment_cycle(
            event_ids=(
                "event-uuid-301",
                "event-uuid-302",
            ),
            assignment_function=assignment_function,
        )

        self.assertEqual(
            assignment_function.call_args_list,
            [
                call(event_id="event-uuid-301"),
                call(event_id="event-uuid-302"),
            ],
        )
        self.assertEqual(result.selected_count, 2)
        self.assertEqual(result.assigned_count, 1)
        self.assertEqual(result.unassigned_count, 0)
        self.assertEqual(result.failed_count, 1)

    def test_empty_selection_returns_zero_counts(self):
        from bridge_core.session_assignment_cycle import (
            run_active_session_assignment_cycle,
        )

        assignment_function = Mock()

        result = run_active_session_assignment_cycle(
            event_ids=(),
            assignment_function=assignment_function,
        )

        assignment_function.assert_not_called()
        self.assertEqual(result.selected_count, 0)
        self.assertEqual(result.assigned_count, 0)
        self.assertEqual(result.unassigned_count, 0)
        self.assertEqual(result.failed_count, 0)


class AdminOperationalRecordSafetyTests(SimpleTestCase):
    def test_operational_records_are_registered_read_only(self):
        from django.contrib import admin

        from bridge_core.models import (
            DeliveryAttempt,
            RawRFIDEvent,
            RFIDSession,
        )

        request = object()

        for model in (
            RFIDSession,
            RawRFIDEvent,
            DeliveryAttempt,
        ):
            with self.subTest(model=model._meta.label):
                self.assertIn(model, admin.site._registry)

                model_admin = admin.site._registry[model]

                self.assertFalse(
                    model_admin.has_add_permission(request)
                )
                self.assertFalse(
                    model_admin.has_change_permission(request)
                )
                self.assertFalse(
                    model_admin.has_delete_permission(request)
                )

                expected_readonly = {
                    field.name
                    for field in model._meta.fields
                }

                self.assertEqual(
                    set(model_admin.readonly_fields),
                    expected_readonly,
                )

    def test_reader_device_remains_configurable(self):
        from django.contrib import admin

        from bridge_core.models import ReaderDevice

        model_admin = admin.site._registry[ReaderDevice]

        self.assertNotIn("code", model_admin.readonly_fields)
        self.assertNotIn("name", model_admin.readonly_fields)
        self.assertNotIn("role", model_admin.readonly_fields)
        self.assertNotIn("enabled", model_admin.readonly_fields)
        self.assertIn("created_at", model_admin.readonly_fields)
        self.assertIn("updated_at", model_admin.readonly_fields)

    def test_operational_configuration_is_singleton_managed(self):
        from django.contrib import admin

        from bridge_core.models import OperationalConfiguration

        request = object()
        model_admin = admin.site._registry[
            OperationalConfiguration
        ]

        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertIn("name", model_admin.readonly_fields)
        self.assertIn("updated_at", model_admin.readonly_fields)

class ReaderDeviceFormTests(SimpleTestCase):
    def setUp(self):
        super().setUp()

        unique_validation_patch = patch(
            "bridge_core.models.ReaderDevice.validate_unique",
            return_value=None,
        )
        unique_validation_patch.start()
        self.addCleanup(unique_validation_patch.stop)

    @staticmethod
    def _valid_data(**overrides):
        data = {
            "code": "receiving-door-01",
            "name": "Receiving door reader",
            "role": "receiving",
            "host": "192.168.1.200",
            "port": 8090,
            "device_address": 1,
            "inventory_mode": "cached",
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 5,
            "reconnect_delay_seconds": 5,
            "enabled": True,
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_disabled_reader_may_be_saved_without_host(self):
        from bridge_core.forms import ReaderDeviceForm

        form = ReaderDeviceForm(
            data=self._valid_data(
                host="",
                enabled=False,
            )
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_enabled_reader_requires_host(self):
        from bridge_core.forms import ReaderDeviceForm

        form = ReaderDeviceForm(
            data=self._valid_data(host="")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("host", form.errors)
        self.assertIn(
            "required when enabled",
            form.errors["host"][0],
        )

    def test_valid_tcp_reader_configuration_is_accepted(self):
        from bridge_core.forms import ReaderDeviceForm

        form = ReaderDeviceForm(data=self._valid_data())

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["host"], "192.168.1.200")
        self.assertEqual(form.cleaned_data["port"], 8090)
        self.assertEqual(form.cleaned_data["device_address"], 1)
        self.assertEqual(form.cleaned_data["inventory_mode"], "cached")

    def test_tcp_port_must_be_in_valid_range(self):
        from bridge_core.forms import ReaderDeviceForm

        form = ReaderDeviceForm(
            data=self._valid_data(port=65536)
        )

        self.assertFalse(form.is_valid())
        self.assertIn("port", form.errors)
        self.assertIn(
            "between 1 and 65535",
            form.errors["port"][0],
        )

    def test_device_address_must_fit_one_byte(self):
        from bridge_core.forms import ReaderDeviceForm

        form = ReaderDeviceForm(
            data=self._valid_data(device_address=256)
        )

        self.assertFalse(form.is_valid())
        self.assertIn("device_address", form.errors)
        self.assertIn(
            "between 0 and 255",
            form.errors["device_address"][0],
        )

    def test_timing_values_are_bounded(self):
        from bridge_core.forms import ReaderDeviceForm

        form = ReaderDeviceForm(
            data=self._valid_data(
                connect_timeout_seconds=0,
                read_timeout_seconds=301,
                reconnect_delay_seconds=0,
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn("connect_timeout_seconds", form.errors)
        self.assertIn("read_timeout_seconds", form.errors)
        self.assertIn("reconnect_delay_seconds", form.errors)

class PocRuntimeControlFormTests(SimpleTestCase):
    def test_defaults_are_fail_closed(self):
        from bridge_core.forms import (
            PocRuntimeControlForm,
        )
        from bridge_core.models import (
            OperationalConfiguration,
        )

        form = PocRuntimeControlForm(
            instance=OperationalConfiguration()
        )

        self.assertEqual(
            form.initial["poc_reader_backend"],
            "fake",
        )
        self.assertFalse(
            form.initial[
                "poc_allow_physical_reader_contact"
            ]
        )
        self.assertFalse(
            form.initial["poc_allow_odoo_contact"]
        )

    def test_contact_enable_requires_exact_confirmation(self):
        from bridge_core.forms import (
            PocRuntimeControlForm,
        )
        from bridge_core.models import (
            OperationalConfiguration,
        )

        form = PocRuntimeControlForm(
            data={
                "poc_reader_backend": "cached_tcp",
                "poc_allow_physical_reader_contact": "on",
                "poc_allow_odoo_contact": "on",
                "confirmation": "wrong",
            },
            instance=OperationalConfiguration(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("confirmation", form.errors)

    def test_fake_backend_cannot_enable_physical_contact(self):
        from bridge_core.forms import (
            POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE,
            PocRuntimeControlForm,
        )
        from bridge_core.models import (
            OperationalConfiguration,
        )

        form = PocRuntimeControlForm(
            data={
                "poc_reader_backend": "fake",
                "poc_allow_physical_reader_contact": "on",
                "confirmation": (
                    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE
                ),
            },
            instance=OperationalConfiguration(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            "poc_reader_backend",
            form.errors,
        )
        self.assertIn(
            "poc_allow_physical_reader_contact",
            form.errors,
        )

    def test_cached_tcp_contact_can_be_enabled(self):
        from bridge_core.forms import (
            POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE,
            PocRuntimeControlForm,
        )
        from bridge_core.models import (
            OperationalConfiguration,
        )

        form = PocRuntimeControlForm(
            data={
                "poc_reader_backend": "cached_tcp",
                "poc_allow_physical_reader_contact": "on",
                "poc_allow_odoo_contact": "on",
                "confirmation": (
                    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE
                ),
            },
            instance=OperationalConfiguration(),
        )

        self.assertTrue(
            form.is_valid(),
            form.errors.as_json(),
        )

    def test_odoo_contact_only_can_be_enabled(self):
        from bridge_core.forms import (
            POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE,
            PocRuntimeControlForm,
        )
        from bridge_core.models import (
            OperationalConfiguration,
        )

        form = PocRuntimeControlForm(
            data={
                "poc_reader_backend": "fake",
                "poc_allow_odoo_contact": "on",
                "confirmation": (
                    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE
                ),
            },
            instance=OperationalConfiguration(),
        )

        self.assertTrue(
            form.is_valid(),
            form.errors.as_json(),
        )

    def test_disabling_all_contact_needs_no_confirmation(self):
        from bridge_core.forms import (
            PocRuntimeControlForm,
        )
        from bridge_core.models import (
            OperationalConfiguration,
        )

        configuration = OperationalConfiguration(
            poc_reader_backend="cached_tcp",
            poc_allow_physical_reader_contact=True,
            poc_allow_odoo_contact=True,
        )

        form = PocRuntimeControlForm(
            data={
                "poc_reader_backend": "fake",
                "confirmation": "",
            },
            instance=configuration,
        )

        self.assertTrue(
            form.is_valid(),
            form.errors.as_json(),
        )
