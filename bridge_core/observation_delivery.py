"""True HTTP batches with durable, fenced, recoverable outbox leases."""
from datetime import timedelta
from email.utils import parsedate_to_datetime
import json
import logging
import math
import random
import uuid

from django.db import transaction
from django.utils import timezone

from bridge_core.models import ObservationOutbox, ObservationDeliveryAttempt
from bridge_core.observation_capture import fingerprint
from bridge_core.observation_config import ObservationConfig
from bridge_core.odoo_api_v1 import OdooRFIDApiTransportError

logger = logging.getLogger(__name__)


def encoded_size(gateway, reader, items):
    return len(json.dumps(dict(jsonrpc='2.0', method='call', params=dict(
        gateway_code=gateway, reader_code=reader, schema_version=1, observations=items),
        id=None), separators=(',', ':')).encode())


class ObservationDeliveryWorker:
    def __init__(self, *, api_client, reader_code, config=None, owner=None, jitter=random.uniform):
        self.api = api_client
        self.reader_code = reader_code
        self.config = config or ObservationConfig()
        self.owner = owner or str(uuid.uuid4())
        self.jitter = jitter
        self.paused_until = None

    @property
    def lease_seconds(self):
        return 2 * self.config.request_timeout_seconds + 30

    @transaction.atomic
    def claim(self, now):
        expired = ObservationOutbox.objects.filter(observation__device__code=self.reader_code,
            state='leased', lease_expires_at__lte=now)
        # Serialize claim/recovery on outbox rows. No network under this lock.
        recovered = list(expired.select_for_update())
        for row in recovered:
            ObservationDeliveryAttempt.objects.filter(outbox=row, lease_token=row.lease_token,
                outcome='started').update(outcome='lease_expired', completed_at=now)
            row.state = 'pending'
            row.lease_token = None
            row.lease_owner = ''
            row.lease_expires_at = None
        if recovered:
            ObservationOutbox.objects.bulk_update(recovered, ['state', 'lease_token', 'lease_owner', 'lease_expires_at'])
            logger.warning('observation_expired_lease reader=%s count=%s', self.reader_code, len(recovered))
            logger.info('observation_queue_recovery reader=%s count=%s', self.reader_code, len(recovered))
        query = ObservationOutbox.objects.filter(observation__device__code=self.reader_code,
            state='pending', next_attempt_at__lte=now).order_by('created_at', 'pk')
        rows = list(query.select_for_update().select_related('observation')[:self.config.batch_size])
        # Underfilled batches wait only up to flush interval; retry due times still apply.
        if rows and len(rows) < self.config.batch_size and (
                now - rows[0].created_at).total_seconds() < self.config.batch_flush_seconds:
            return []
        token, batch = uuid.uuid4(), uuid.uuid4()
        for row in rows:
            row.state = 'leased'
            row.lease_token = token
            row.lease_owner = self.owner
            row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            row.attempt_count += 1
        if rows:
            ObservationOutbox.objects.bulk_update(rows, ['state', 'lease_token', 'lease_owner', 'lease_expires_at', 'attempt_count'])
            ObservationDeliveryAttempt.objects.bulk_create([ObservationDeliveryAttempt(
                outbox=row, batch_uuid=batch, lease_token=token, attempt_number=row.attempt_count) for row in rows])
        return rows

    def _delay(self, row):
        base = min(self.config.retry_max_seconds,
                   self.config.retry_initial_seconds * 2 ** min(row.attempt_count - 1, 30))
        return min(self.config.retry_max_seconds, max(self.config.retry_initial_seconds, base * self.jitter(0.8, 1.2)))

    @transaction.atomic
    def finish(self, row, outcome, error='', retry_after=None):
        current = ObservationOutbox.objects.select_for_update().get(pk=row.pk)
        if current.state != 'leased' or current.lease_token != row.lease_token:
            return  # old worker cannot acknowledge a newly leased item
        now = timezone.now()
        current.state = outcome
        current.last_error = error[:256]
        current.lease_token = None
        current.lease_owner = ''
        current.lease_expires_at = None
        if outcome == 'delivered':
            current.acknowledged_at = now
        elif outcome == 'pending':
            current.next_attempt_at = now + timedelta(seconds=max(self._delay(row), retry_after or 0))
        current.save()
        ObservationDeliveryAttempt.objects.filter(outbox=row, lease_token=row.lease_token,
            attempt_number=row.attempt_count, outcome='started').update(
                outcome=outcome, error_kind=error[:128], completed_at=now)

    def _retry_after(self, value):
        try:
            seconds = float(value)
            return max(0, seconds) if math.isfinite(seconds) else None
        except (ValueError, TypeError):
            try:
                return max(0, (parsedate_to_datetime(value) - timezone.now()).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return None

    def _send(self, rows):
        if not rows:
            return
        if self.paused_until and timezone.now() < self.paused_until:
            delay = (self.paused_until - timezone.now()).total_seconds()
            for row in rows:
                self.finish(row, 'pending', 'delivery_paused', delay)
            return
        # Renew and fence leases before each actual HTTP request, including splits.
        rows = [row for row in rows if ObservationOutbox.objects.filter(
            pk=row.pk, state='leased', lease_token=row.lease_token).update(
                lease_expires_at=timezone.now() + timedelta(seconds=self.lease_seconds))]
        if not rows:
            return
        batch_uuid = uuid.uuid4()
        ObservationDeliveryAttempt.objects.filter(
            outbox__in=rows, lease_token=rows[0].lease_token,
            outcome='started').update(batch_uuid=batch_uuid)
        try:
            result = self.api.observations(reader_code=self.reader_code,
                observations=[row.observation.frozen_payload for row in rows])
        except OdooRFIDApiTransportError as exc:
            status = exc.status_code
            if status == 413 and len(rows) > 1:
                logger.warning('observation_payload_split batch=%s reader=%s count=%s', batch_uuid, self.reader_code, len(rows))
                middle = len(rows)//2
                self._send(rows[:middle])
                self._send(rows[middle:])
                return
            delay = self._retry_after(exc.retry_after) if status == 429 else None
            if status in (401, 403):
                delay = self.config.retry_max_seconds
                self.paused_until = timezone.now() + timedelta(seconds=delay)
                logger.warning('observation_auth_failure reader=%s status=%s', self.reader_code, status)
            if status == 429:
                self.paused_until = timezone.now() + timedelta(seconds=delay or self.config.retry_initial_seconds)
                logger.warning('observation_rate_limited reader=%s retry_after=%s', self.reader_code, delay)
            if status == 413:
                logger.warning('observation_rejected_oversize batch=%s event_uuid=%s', batch_uuid, rows[0].observation.event_uuid)
            elif status and 400 <= status < 500 and status not in (401, 403, 429):
                logger.warning('observation_request_rejected batch=%s status=%s', batch_uuid, status)
            for row in rows:
                self.finish(row, 'quarantined' if status == 413 else 'pending',
                            f'HTTP_{status}' if status else 'transport_error', delay)
            logger.info('observation_batch_summary batch=%s reader=%s count=%s delivered=0 retry_or_quarantine=%s',
                        batch_uuid, self.reader_code, len(rows), len(rows))
            return
        except Exception:
            logger.error('observation_batch_failure batch=%s reader=%s', batch_uuid, self.reader_code)
            for row in rows:
                self.finish(row, 'pending', 'transport_or_protocol_error')
            return
        items = result.get('results') if isinstance(result, dict) and result.get('ok') is True else None
        if not isinstance(items, list):
            logger.error('observation_unexpected_response batch=%s reader=%s', batch_uuid, self.reader_code)
            items = []
        matches = {}
        for item in items:
            if isinstance(item, dict):
                matches.setdefault(str(item.get('event_uuid')), []).append(item)
        delivered = quarantined = 0
        for row in rows:
            found = matches.get(str(row.observation.event_uuid), [])
            if len(found) != 1:
                logger.error('observation_response_correlation batch=%s event_uuid=%s', batch_uuid, row.observation.event_uuid)
                self.finish(row, 'pending', 'missing_or_ambiguous_result')
                continue
            item = found[0]
            status = item.get('status', item.get('outcome'))
            if 'status' in item and 'outcome' in item and item['status'] != item['outcome']:
                status = None
            if item.get('payload_hash', row.observation.payload_hash) != row.observation.payload_hash:
                status = 'conflict'
            if status in ('accepted', 'duplicate'):
                self.finish(row, 'delivered')
                delivered += 1
            elif status in ('rejected', 'conflict'):
                # Validation rejection is permanent unless server explicitly says retryable.
                reason = item.get('reason_code', item.get('error_code', ''))
                retryable = status == 'rejected' and (item.get('retryable') is True or
                    reason in ('temporarily_unavailable', 'rate_limited', 'server_busy'))
                # Retain only a bounded code, never arbitrary server error text.
                self.finish(row, 'pending' if retryable else 'quarantined',
                            f'{status}:{reason}' if reason else str(status))
                quarantined += not retryable
                logger.warning('observation_item_%s batch=%s event_uuid=%s', status, batch_uuid, row.observation.event_uuid)
            else:
                logger.error('observation_unexpected_item batch=%s event_uuid=%s', batch_uuid, row.observation.event_uuid)
                self.finish(row, 'pending', 'unexpected_item_status')
        logger.info('observation_batch_summary batch=%s reader=%s count=%s delivered=%s quarantined=%s',
                    batch_uuid, self.reader_code, len(rows), delivered, quarantined)

    def run_once(self):
        now = timezone.now()
        if self.paused_until and now < self.paused_until:
            return 0
        try:
            rows = self.claim(now)
            batch = []
            for row in rows:
                observation = row.observation
                if (observation.status != 'finalized' or
                        fingerprint(observation.frozen_payload) != observation.payload_hash):
                    self.finish(row, 'quarantined', 'local_payload_integrity')
                    logger.warning('observation_uuid_conflict event_uuid=%s', observation.event_uuid)
                    continue
                if encoded_size(self.api.gateway_code, self.reader_code, [observation.frozen_payload]) > self.config.max_payload_bytes:
                    self.finish(row, 'quarantined', 'payload_too_large')
                    logger.warning('observation_rejected_size event_uuid=%s', observation.event_uuid)
                    continue
                if batch and encoded_size(self.api.gateway_code, self.reader_code,
                    [item.observation.frozen_payload for item in batch] + [observation.frozen_payload]) > self.config.max_payload_bytes:
                    self._send(batch)
                    batch = []
                batch.append(row)
            self._send(batch)
            return len(rows)
        except Exception:
            logger.exception('observation_delivery_database_failure reader=%s', self.reader_code)
            raise
