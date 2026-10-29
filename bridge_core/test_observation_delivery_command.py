"""Observation command failure handling without DB, HTTP or reader contact."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from bridge_core.management.commands import run_rfid_observation_delivery_worker as command


@override_settings(ALLOW_ODOO_CONTACT=True, CONFIGURATION={})
class ObservationDeliveryCommandTests(SimpleTestCase):
    def setUp(self):
        self.enterContext(patch.object(command, 'load_final_runtime_configuration',
            return_value=SimpleNamespace(reader_code='offline-reader',
                odoo_base_url='https://offline.invalid', bearer_token='offline-placeholder',
                gateway_code='offline-gateway', verify_tls=True)))
        self.api = self.enterContext(patch.object(command, 'OdooRFIDApiClient'))
        self.worker = Mock()
        self.enterContext(patch.object(command, 'ObservationDeliveryWorker', return_value=self.worker))
        self.enterContext(patch.object(command, 'observation_metrics', return_value={}))
        self.sleep = self.enterContext(patch.object(command.time, 'sleep'))
        self.log_exception = self.enterContext(patch.object(command.logger, 'exception',
            wraps=command.logger.exception))

    def test_continuous_failure_logs_reader_and_continues(self):
        self.worker.run_once.side_effect = [RuntimeError('synthetic cycle failure'), 0, KeyboardInterrupt()]
        with self.assertLogs(command.logger, level='ERROR') as logs:
            call_command('run_rfid_observation_delivery_worker')
        self.log_exception.assert_called_once_with(
            'observation_worker_cycle_failed reader=%s', 'offline-reader')
        self.assertEqual(self.worker.run_once.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertIn('reader=offline-reader', logs.output[0])
        self.assertIn('RuntimeError: synthetic cycle failure', logs.output[0])
        self.assertNotIn('offline-placeholder', '\n'.join(logs.output))
        self.assertNotIn('Authorization', '\n'.join(logs.output))
        self.api.assert_called_once()  # mocked constructor; no real client exists

    def test_once_reraises_original_exception_without_retry(self):
        failure = RuntimeError('synthetic cycle failure')
        self.worker.run_once.side_effect = failure
        with self.assertRaises(RuntimeError) as raised:
            call_command('run_rfid_observation_delivery_worker', once=True)
        self.assertIs(raised.exception, failure)
        self.worker.run_once.assert_called_once_with()
        self.log_exception.assert_not_called()
        self.sleep.assert_not_called()
