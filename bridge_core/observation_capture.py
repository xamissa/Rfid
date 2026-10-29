"""Single-reader durable aggregation. No HTTP; database is authoritative."""
from collections import defaultdict
from hashlib import sha256
import json
import logging
import time
import uuid

from django.db import transaction
from django.utils import timezone

from bridge_core.models import RFIDObservation, ObservationOutbox
from bridge_core.observation_config import ObservationConfig
from bridge_core.reader_protocol import COMMAND_ACTIVE_TAG, parse_tag_data, RFIDProtocolError

logger = logging.getLogger(__name__)


def canonical_bytes(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def fingerprint(payload):
    return sha256(canonical_bytes(payload)).hexdigest()


class ObservationCapture:
    def __init__(self, *, device, config=None, monotonic=time.monotonic):
        self.device = device
        self.config = config or ObservationConfig()
        self.clock = monotonic
        self.run = uuid.uuid4()
        self.epoch = uuid.uuid4()
        self.generation = uuid.uuid4()
        self.context = dict(context_kind='no_transfer', session_key=None, operation=None,
                            related_session_key=None, command_revision=None, context_reason='startup')
        self.timers = {}
        self._last_warning = 0.0
        self.recovered = False

    def recover(self):
        if self.recovered:
            return
        with transaction.atomic():
            rows = list(RFIDObservation.objects.select_for_update().filter(device=self.device, status='open'))
            self._finalize(rows, 'restart')
        self.recovered = True
        logger.info('observation_capture_start reader=%s recovered=%s', self.device.code, len(rows))

    def snapshot(self):
        return dict(self.context, context_generation_uuid=self.generation,
                    connection_epoch_uuid=self.epoch, capture_run_uuid=self.run)

    def _payload(self, row, reason):
        names = ('epc', 'antenna', 'raw_read_count', 'context_kind', 'session_key', 'operation',
                 'context_reason', 'related_session_key', 'command_revision', 'diagnostic_metadata')
        payload = {name: getattr(row, name) for name in names}
        payload.update(event_uuid=str(row.event_uuid), first_seen_at=row.first_seen_at.isoformat(),
                       last_seen_at=row.last_seen_at.isoformat(), finalization_reason=reason)
        for name in ('context_generation_uuid', 'connection_epoch_uuid', 'capture_run_uuid'):
            payload[name] = str(getattr(row, name))
        return payload

    def _finalize(self, rows, reason):
        now = timezone.now()
        for row in rows:
            row.status = 'finalized'
            row.updated_at = now
            row.finalized_at = now
            row.finalization_reason = reason
            row.frozen_payload = self._payload(row, reason)
            row.payload_hash = fingerprint(row.frozen_payload)
        if rows:
            RFIDObservation.objects.bulk_update(rows, ['status', 'finalized_at', 'finalization_reason', 'frozen_payload', 'payload_hash', 'updated_at'])
            ObservationOutbox.objects.bulk_create([ObservationOutbox(observation=row, next_attempt_at=now) for row in rows])
        for row in rows:
            self.timers.pop(row.event_uuid, None)

    def boundary(self, reason, *, reconnect=False, **context):
        with transaction.atomic():
            rows = list(RFIDObservation.objects.select_for_update().filter(device=self.device, status='open'))
            self._finalize(rows, reason)
        # Advance in-memory attribution only after local transaction exit.
        self.timers.clear()
        self.generation = uuid.uuid4()
        if reconnect:
            self.epoch = uuid.uuid4()
        self.context.update(context)
        self.context['context_reason'] = reason
        if self.context['context_kind'] == 'unresolved':
            logger.warning('observation_unresolved reader=%s related_session=%s', self.device.code, self.context.get('related_session_key'))

    @transaction.atomic
    def ingest(self, frames, *, seen_at=None, tick=None, snapshot=None):
        now = seen_at or timezone.now()
        tick = self.clock() if tick is None else tick
        snapshot = snapshot or self.snapshot()
        grouped = defaultdict(list)
        for frame in frames:
            if frame.command != COMMAND_ACTIVE_TAG:
                continue
            try:
                if frame.status or frame.address != self.device.device_address:
                    raise RFIDProtocolError('Invalid tag status/address')
                tag = parse_tag_data(frame.payload)
                if not tag.epc:
                    raise RFIDProtocolError('Missing EPC')
            except RFIDProtocolError:
                logger.warning('observation_invalid_tag reader=%s', self.device.code)
                continue
            grouped[(tag.epc.upper(), tag.antenna)].append((tag, frame))
        rows = list(RFIDObservation.objects.select_for_update().filter(device=self.device, status='open',
            context_generation_uuid=snapshot['context_generation_uuid'],
            connection_epoch_uuid=snapshot['connection_epoch_uuid']))
        expired = []
        by_reason = defaultdict(list)
        for row in rows:
            first, last = self.timers.get(row.event_uuid, (
                tick - max(0, (now-row.first_seen_at).total_seconds()),
                tick - max(0, (now-row.last_seen_at).total_seconds())))
            reason = ('max_duration' if tick-first >= self.config.max_duration_seconds else
                      'inactivity' if tick-last >= self.config.inactivity_seconds else None)
            if reason:
                by_reason[reason].append(row)
                expired.append(row.pk)
        for reason, due in by_reason.items():
            self._finalize(due, reason)
        opened = {(row.epc, row.antenna): row for row in rows if row.pk not in expired}
        updated, created = [], []
        for (epc, antenna), reports in grouped.items():
            row = opened.get((epc, antenna))
            if row is None:
                if len(opened) >= self.config.active_key_limit:
                    victim_key = next(iter(opened))
                    victim = opened.pop(victim_key)
                    # New rows must exist before finalization/outbox creation.
                    if created:
                        RFIDObservation.objects.bulk_create(created)
                        created.clear()
                    self._finalize([victim], 'capacity')
                row = RFIDObservation(device=self.device, epc=epc, antenna=antenna,
                    antenna_key=antenna if antenna is not None else -1,
                    first_seen_at=now, last_seen_at=now, raw_read_count=0,
                    **snapshot)
                created.append(row)
                opened[(epc, antenna)] = row
                self.timers[row.event_uuid] = (tick, tick)
            else:
                updated.append(row)
            row.last_seen_at = max(now, row.last_seen_at)
            row.updated_at = now
            row.raw_read_count += len(reports)
            tag, frame = reports[-1]
            row.diagnostic_metadata = dict(protocol_type=tag.protocol_type,
                reader_count=tag.count, pc=tag.pc, tid=tag.tid,
                last_sequence=frame.sequence, reader_address=frame.address)
            first, _ = self.timers.get(row.event_uuid, (tick - max(0, (now-row.first_seen_at).total_seconds()), tick))
            self.timers[row.event_uuid] = (first, tick)
        if created:
            RFIDObservation.objects.bulk_create(created)
        if updated:
            RFIDObservation.objects.bulk_update(updated, ['last_seen_at', 'raw_read_count', 'diagnostic_metadata', 'updated_at'])

    def monitor(self):
        now = self.clock()
        if now - self._last_warning < 60:
            return
        self._last_warning = now
        metrics = observation_metrics(self.device)
        if metrics['pending_count'] >= self.config.queue_high_water:
            logger.warning('observation_queue_high_water reader=%s metrics=%s', self.device.code, metrics)


def observation_metrics(device=None):
    from django.db.models import Min
    queue = ObservationOutbox.objects.all()
    observations = RFIDObservation.objects.all()
    if device:
        queue = queue.filter(observation__device=device)
        observations = observations.filter(device=device)
    pending = queue.filter(state__in=['pending', 'leased'])
    return dict(pending_count=pending.count(), oldest_pending_at=pending.aggregate(value=Min('created_at'))['value'],
                quarantined_count=queue.filter(state='quarantined').count(),
                open_count=observations.filter(status='open').count())
