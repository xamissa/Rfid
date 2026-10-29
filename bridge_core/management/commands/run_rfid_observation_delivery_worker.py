import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bridge_core.final_runtime_config import load_final_runtime_configuration
from bridge_core.observation_config import ObservationConfig
from bridge_core.observation_delivery import ObservationDeliveryWorker
from bridge_core.observation_capture import observation_metrics
from bridge_core.odoo_api_v1 import OdooRFIDApiClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Deliver durable RFID observations independently of capture.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--reader-code')

    def handle(self, *args, **options):
        if not settings.ALLOW_ODOO_CONTACT:
            raise CommandError('Observation delivery requires ALLOW_ODOO_CONTACT=True.')
        final = load_final_runtime_configuration(settings.CONFIGURATION)
        config = ObservationConfig.load(settings.CONFIGURATION)
        reader = options['reader_code'] or final.reader_code
        api = OdooRFIDApiClient(base_url=final.odoo_base_url, bearer_token=final.bearer_token,
            gateway_code=final.gateway_code, timeout_seconds=config.request_timeout_seconds,
            verify_tls=final.verify_tls)
        worker = ObservationDeliveryWorker(api_client=api, reader_code=reader, config=config)
        logger.info('observation_delivery_start reader=%s metrics=%s', reader, observation_metrics())
        try:
            while True:
                try:
                    selected = worker.run_once()
                except Exception:
                    if options['once']:
                        raise
                    logger.exception('observation_worker_cycle_failed reader=%s', reader)
                    selected = 0  # retained leases recover after DB/service recovery
                if options['once']:
                    return
                if not selected:
                    time.sleep(min(1.0, config.batch_flush_seconds))
        except KeyboardInterrupt:
            return
