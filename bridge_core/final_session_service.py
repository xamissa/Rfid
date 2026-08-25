from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from bridge_core.final_runtime_state import (
    OdooRFIDCommand,
    RuntimeCommand,
)
from bridge_core.models import ReaderDevice, RFIDSession


class FinalSessionError(RuntimeError):
    pass


_OPERATION_BY_ROLE = {
    ReaderDevice.Role.RECEIVING: RFIDSession.OperationType.RECEIPT,
    ReaderDevice.Role.DISPATCH: RFIDSession.OperationType.DISPATCH,
}


@dataclass(frozen=True)
class SessionSyncResult:
    session: RFIDSession
    created: bool
    reused: bool


def _operation_for_reader(reader, *, requested_operation=None):
    role_operation = _OPERATION_BY_ROLE.get(reader.role)

    if role_operation is None:
        raise FinalSessionError(
            f"Unsupported RFID reader role: {reader.role!r}"
        )

    requested_operation = str(
        requested_operation or ""
    ).strip()

    if not requested_operation:
        return role_operation

    valid_operations = {
        RFIDSession.OperationType.RECEIPT,
        RFIDSession.OperationType.DISPATCH,
    }

    if requested_operation not in valid_operations:
        raise FinalSessionError(
            f"Unsupported RFID session operation: "
            f"{requested_operation!r}"
        )

    if reader.shared_operations:
        return requested_operation

    if requested_operation != role_operation:
        raise FinalSessionError(
            "Requested RFID session operation does not match "
            "the dedicated reader role."
        )

    return role_operation


@transaction.atomic
def synchronize_start_command(
    *,
    command: OdooRFIDCommand,
):
    if command.command != RuntimeCommand.START:
        raise FinalSessionError(
            "Only a START command may create/synchronize a local session."
        )

    try:
        reader = (
            ReaderDevice.objects
            .select_for_update()
            .get(
                code=command.reader_code,
                enabled=True,
            )
        )
    except ReaderDevice.DoesNotExist as exc:
        raise FinalSessionError(
            f"Enabled RFID reader not found: {command.reader_code}"
        ) from exc

    operation = _operation_for_reader(
        reader,
        requested_operation=command.operation,
    )

    existing = (
        RFIDSession.objects
        .select_for_update()
        .filter(
            external_session_key=command.session_key,
        )
        .first()
    )

    if existing:
        if existing.device_id != reader.id:
            raise FinalSessionError(
                "Existing local session belongs to a different reader."
            )

        if existing.operation_type != operation:
            raise FinalSessionError(
                "Existing local session operation does not match "
                "the requested operation."
            )

        if existing.status != RFIDSession.Status.ACTIVE:
            raise FinalSessionError(
                "Odoo attempted to restart a locally closed/cancelled session."
            )

        return SessionSyncResult(
            session=existing,
            created=False,
            reused=True,
        )

    conflicting = (
        RFIDSession.objects
        .select_for_update()
        .filter(
            device=reader,
            status=RFIDSession.Status.ACTIVE,
        )
        .first()
    )

    if conflicting:
        raise FinalSessionError(
            "Reader already has a different active local RFID session."
        )

    session = RFIDSession.objects.create(
        external_session_key=command.session_key,
        device=reader,
        operation_type=operation,
        odoo_model="stock.picking",
        # The final v1 command contract supplies the picking reference,
        # not the numeric Odoo database ID. Final runtime identity is the
        # immutable external_session_key; zero means "not supplied".
        odoo_record_id=0,
        odoo_reference=command.picking,
        status=RFIDSession.Status.ACTIVE,
    )

    return SessionSyncResult(
        session=session,
        created=True,
        reused=False,
    )


@transaction.atomic
def close_local_session(
    *,
    session_key,
    reader_code,
):
    session_key = str(session_key or "").strip()
    reader_code = str(reader_code or "").strip()

    if not session_key or not reader_code:
        raise FinalSessionError(
            "session_key and reader_code are required."
        )

    try:
        session = (
            RFIDSession.objects
            .select_for_update()
            .select_related("device")
            .get(
                external_session_key=session_key,
            )
        )
    except RFIDSession.DoesNotExist as exc:
        raise FinalSessionError(
            "Local RFID session does not exist."
        ) from exc

    if session.device.code != reader_code:
        raise FinalSessionError(
            "Local session reader identity mismatch."
        )

    if session.status == RFIDSession.Status.CLOSED:
        return session

    if session.status != RFIDSession.Status.ACTIVE:
        raise FinalSessionError(
            f"Cannot close session in status {session.status!r}."
        )

    session.status = RFIDSession.Status.CLOSED
    session.closed_at = timezone.now()
    session.save(
        update_fields=(
            "status",
            "closed_at",
            "updated_at",
        )
    )

    return session


@transaction.atomic
def cancel_local_session(
    *,
    session_key,
    reader_code,
):
    session_key = str(session_key or "").strip()
    reader_code = str(reader_code or "").strip()

    try:
        session = (
            RFIDSession.objects
            .select_for_update()
            .select_related("device")
            .get(
                external_session_key=session_key,
            )
        )
    except RFIDSession.DoesNotExist as exc:
        raise FinalSessionError(
            "Local RFID session does not exist."
        ) from exc

    if session.device.code != reader_code:
        raise FinalSessionError(
            "Local session reader identity mismatch."
        )

    if session.status == RFIDSession.Status.CANCELLED:
        return session

    if session.status != RFIDSession.Status.ACTIVE:
        raise FinalSessionError(
            f"Cannot cancel session in status {session.status!r}."
        )

    session.status = RFIDSession.Status.CANCELLED
    session.closed_at = timezone.now()
    session.save(
        update_fields=(
            "status",
            "closed_at",
            "updated_at",
        )
    )

    return session
