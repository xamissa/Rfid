"""Offline matching-layer and repository service contract checks."""
import configparser
import json
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from bridge_core.models import RFIDObservation
from bridge_core.odoo_api_v1 import OdooRFIDApiClient
from bridge_core import test_observations as observation_fixtures
from bridge_core.test_observations import tag
from bridge_core.test_odoo_api_v1 import RecordingOpenerFactory


class ObservationWireContractTests(TestCase):
    def setUp(self):
        observation_fixtures.ObservationTests.setUp(self)

    def test_observation_contract_unchanged_and_no_transactional_flag(self):
        for index, (kind, operation) in enumerate([
            ('receipt', 'receipt'), ('delivery', 'dispatch'),
            ('no_transfer', None), ('unresolved', None),
        ]):
            self.capture.boundary('context_change', context_kind=kind,
                session_key='session' if operation else None, operation=operation)
            self.capture.ingest([tag(f'{index:08X}', antenna=2 if operation else None)])
        self.capture.boundary('shutdown')
        payloads = list(RFIDObservation.objects.order_by('id').values_list('frozen_payload', flat=True))
        factory = RecordingOpenerFactory({'result': {'ok': True, 'results': []}})
        api = OdooRFIDApiClient(base_url='https://offline.invalid', bearer_token='offline-placeholder',
            gateway_code='gateway', opener_factory=factory)
        api.observations(reader_code=self.device.code, observations=payloads)
        params = json.loads(factory.opener.requests[0]['request'].data)['params']
        self.assertEqual(set(params), {'gateway_code', 'reader_code', 'schema_version', 'observations'})
        self.assertEqual(params['gateway_code'], 'gateway')
        self.assertEqual(params['reader_code'], self.device.code)
        self.assertEqual(params['schema_version'], 1)
        self.assertEqual(params['observations'], payloads)
        required = {'event_uuid', 'epc', 'antenna', 'first_seen_at', 'last_seen_at',
            'raw_read_count', 'context_kind', 'session_key', 'operation',
            'context_generation_uuid', 'connection_epoch_uuid', 'capture_run_uuid', 'finalization_reason'}
        for item in params['observations']:
            self.assertTrue(required <= set(item))
            self.assertFalse({'product', 'product_id', 'company', 'company_id',
                'picking', 'picking_id', 'result', 'reconciliation_result',
                'observation_ledger_managed'} & set(item))
        self.assertEqual([p['context_kind'] for p in payloads], ['receipt', 'delivery', 'no_transfer', 'unresolved'])
        self.assertEqual(payloads[1]['operation'], 'dispatch')
        self.assertIsNone(payloads[2]['antenna'])


class ObservationServiceSourceTests(SimpleTestCase):
    def test_unit_matches_final_delivery_conventions(self):
        root = Path(__file__).resolve().parent.parent
        def load(name):
            text = (root / 'deploy/systemd' / name).read_text()
            parser = configparser.ConfigParser(strict=False, interpolation=None)
            parser.read_string(text.replace('\\\n', ''))
            return parser, text
        actual, text = load('rfid-observation-delivery.service')
        existing, _ = load('rfid-final-delivery.service')
        for section in existing.sections():
            for key, value in existing[section].items():
                if (section, key) not in [('Unit', 'description'), ('Service', 'execstart')]:
                    self.assertEqual(actual[section][key], value, (section, key))
        self.assertEqual(actual['Service']['User'], 'rfidbridge')
        self.assertEqual(actual['Service']['Group'], 'rfidbridge')
        self.assertEqual(actual['Service']['WorkingDirectory'], '/opt/rfid_bridge/app')
        self.assertEqual(actual['Service']['ExecStart'].split(), [
            '/opt/rfid_bridge/venv/bin/python', '/opt/rfid_bridge/app/manage.py',
            'run_rfid_observation_delivery_worker'])
        self.assertNotIn('rfid-final-worker.service', actual['Unit'].get('Requires', ''))
        self.assertNotIn('rfid-final-worker.service', actual['Unit']['After'])
        self.assertIn('Environment=PYTHONDONTWRITEBYTECODE=1', text)
        self.assertIn('Environment=PYTHONUNBUFFERED=1', text)
        # Existing units delegate both env files to Django, not systemd.
        self.assertFalse(actual.has_option('Service', 'EnvironmentFile'))
        from config.environment import APP_ENV_FILE, SECRETS_ENV_FILE
        self.assertEqual(str(APP_ENV_FILE), '/etc/rfid_bridge/app.env')
        self.assertEqual(str(SECRETS_ENV_FILE), '/etc/rfid_bridge/secrets.env')


class MatchingCaptureCompatibilityTests(TestCase):
    def setUp(self):
        observation_fixtures.PipelineIntegrationTests.setUp(self)

    command = observation_fixtures.PipelineIntegrationTests.command
    emit = observation_fixtures.PipelineIntegrationTests.emit

    def test_single_business_report_has_both_paths_and_epc_counts_once(self):
        from bridge_core.models import RawRFIDEvent
        self.assertTrue(self.command('start').success)
        self.emit([tag()])
        self.assertEqual(RFIDObservation.objects.count(), 1)
        self.assertEqual(RawRFIDEvent.objects.count(), 1)
        self.assertEqual(RFIDObservation.objects.get().context_kind, 'receipt')
        self.emit([tag()])
        self.assertEqual(RawRFIDEvent.objects.count(), 1)
        self.assertEqual(RFIDObservation.objects.get().raw_read_count, 2)

    def test_single_ambient_report_has_only_observation_path(self):
        from bridge_core.models import RawRFIDEvent
        self.emit([tag()])
        self.assertEqual(RFIDObservation.objects.count(), 1)
        self.assertEqual(RawRFIDEvent.objects.count(), 0)
        self.assertEqual(RFIDObservation.objects.get().context_kind, 'no_transfer')
