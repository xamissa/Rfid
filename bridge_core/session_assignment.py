from dataclasses import dataclass

from django.db import transaction

from bridge_core.models import RawRFIDEvent, ReaderDevice, RFIDSession
from bridge_core.queue_policy import validate_queue_transition


_OPERATION_BY_DEVICE_ROLE = {
    ReaderDevice.Role.RECEIVING: RFIDSession.OperationType.RECEIPT,
    ReaderDevice.Role.DISPATCH: RFIDSession.OperationType.DISPATCH,
}


@dataclass(frozen=True)
class SessionAssignmentResult:
    event_id: object
    session_id: object
    previous_state: str
    current_state: str


def assign_event_to_active_session(*, event_id):
    with transaction.atomic():
        event = (
            RawRFIDEvent.objects
            .select_for_update()
            .select_related("device")
            .get(event_id=event_id)
        )

        if event.queue_state != RawRFIDEvent.QueueState.UNASSIGNED:
            raise ValueError(
                "Event must be unassigned before session assignment."
            )

        if event.rfid_session_id is not None:
            raise ValueError(
                "Event already has an RFID session assignment."
            )

        session = (
            RFIDSession.objects
            .select_for_update()
            .get(
                device_id=event.device_id,
                status=RFIDSession.Status.ACTIVE,
            )
        )

        expected_operation = _OPERATION_BY_DEVICE_ROLE.get(
            event.device.role
        )

        if expected_operation is None:
            raise ValueError(
                "Reader device has an unsupported assignment role."
            )

        if session.operation_type != expected_operation:
            raise ValueError(
                "Active RFID session operation is incompatible "
                "with the reader device role."
            )

        validate_queue_transition(
            current_state=event.queue_state,
            target_state=RawRFIDEvent.QueueState.QUEUED,
        )

        previous_state = event.queue_state
        event.rfid_session = session
        event.queue_state = RawRFIDEvent.QueueState.QUEUED
        event.save(
            update_fields=(
                "rfid_session",
                "queue_state",
                "updated_at",
            ),
        )

        return SessionAssignmentResult(
            event_id=event.event_id,
            session_id=session.session_id,
            previous_state=previous_state,
            current_state=event.queue_state,
        )
