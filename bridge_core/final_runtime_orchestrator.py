from dataclasses import dataclass

from bridge_core.final_runtime_state import (
    FINAL_RECEIVING_READER_CODE,
    LocalReaderRuntime,
    OdooRFIDCommand,
    RuntimeCommand,
    RuntimeState,
    RuntimeStateError,
)
from bridge_core.final_session_service import (
    FinalSessionError,
    cancel_local_session,
    close_local_session,
    synchronize_start_command,
)


class FinalRuntimeOrchestratorError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandExecutionResult:
    runtime: LocalReaderRuntime
    command: OdooRFIDCommand
    success: bool
    message: str


class FinalRuntimeOrchestrator:
    def __init__(
        self,
        *,
        api_client,
        reader_executor,
        reader_code=FINAL_RECEIVING_READER_CODE,
        before_session_close=None,
    ):
        self.api_client = api_client
        self.reader_executor = reader_executor
        self.before_session_close = before_session_close
        self.runtime = LocalReaderRuntime(
            reader_code=reader_code,
            state=RuntimeState.OFFLINE,
            session_key=None,
            last_command_revision=0,
            error="Reader state not yet verified after process startup.",
        )

    def mark_reader_verified_idle(self):
        if self.runtime.session_key is not None:
            raise FinalRuntimeOrchestratorError(
                "Cannot mark reader idle while a local runtime session exists."
            )

        self.runtime = LocalReaderRuntime(
            reader_code=self.runtime.reader_code,
            state=RuntimeState.IDLE,
            session_key=None,
            last_command_revision=self.runtime.last_command_revision,
            error=None,
        )

        return self.runtime

    def heartbeat(self):
        return self.api_client.heartbeat(
            readers=[
                self.runtime.heartbeat_payload()
            ],
        )

    def poll_commands(self):
        payloads = self.api_client.commands()
        results = []

        for payload in payloads:
            command = OdooRFIDCommand.from_payload(payload)

            if command.reader_code != self.runtime.reader_code:
                continue

            results.append(
                self.process_command(command)
            )

        return tuple(results)

    def process_command(self, command):
        try:
            transition = self.runtime.plan_command(command)
        except RuntimeStateError as exc:
            return self._reject_command(
                command=command,
                message=str(exc),
            )

        if transition.duplicate:
            return self._resend_completed_ack(
                command=command,
            )

        if command.command == RuntimeCommand.START:
            return self._execute_start(
                command=command,
                planned_runtime=transition.after,
            )

        if command.command == RuntimeCommand.STOP:
            return self._execute_stop(
                command=command,
                planned_runtime=transition.after,
            )

        if command.command == RuntimeCommand.ABORT:
            return self._execute_abort(
                command=command,
                planned_runtime=transition.after,
            )

        return self._reject_command(
            command=command,
            message="Unsupported command.",
        )

    def _success_ack_message(self, command):
        messages = {
            RuntimeCommand.START: (
                "Reader started successfully."
            ),
            RuntimeCommand.STOP: (
                "Reader stopped successfully."
            ),
            RuntimeCommand.ABORT: (
                "Reader abort completed."
            ),
        }

        return messages[command.command]

    def _send_completed_success_ack(
        self,
        *,
        command,
    ):
        ack_result = self.api_client.ack(
            session_key=command.session_key,
            reader_code=command.reader_code,
            command=command.command.value,
            revision=command.revision,
            success=True,
            message=self._success_ack_message(
                command
            ),
        )

        if not ack_result.get("ok"):
            raise FinalRuntimeOrchestratorError(
                "Odoo did not accept completed command ACK."
            )

        return ack_result

    def _resend_completed_ack(
        self,
        *,
        command,
    ):
        try:
            self._send_completed_success_ack(
                command=command,
            )
        except Exception as exc:
            return CommandExecutionResult(
                runtime=self.runtime,
                command=command,
                success=False,
                message=(
                    "Completed command ACK is still pending: "
                    f"{exc}"
                ),
            )

        return CommandExecutionResult(
            runtime=self.runtime,
            command=command,
            success=True,
            message=(
                "Duplicate completed command ACK resent "
                "successfully."
            ),
        )

    def _execute_start(
        self,
        *,
        command,
        planned_runtime,
    ):
        self.runtime = planned_runtime

        try:
            synchronize_start_command(
                command=command,
            )

            self.reader_executor.start(
                session_key=command.session_key,
                reader_code=command.reader_code,
            )

            self.runtime = (
                self.runtime.mark_reader_started()
            )

        except Exception as exc:
            self.runtime = (
                self.runtime.mark_degraded(
                    str(exc)
                )
            )

            try:
                self.api_client.ack(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                    command=command.command.value,
                    revision=command.revision,
                    success=False,
                    message=str(exc),
                )
            except Exception:
                pass

            return CommandExecutionResult(
                runtime=self.runtime,
                command=command,
                success=False,
                message=str(exc),
            )

        self.runtime = (
            self.runtime.mark_command_completed(
                command
            )
        )

        try:
            self._send_completed_success_ack(
                command=command,
            )
        except Exception as exc:
            return CommandExecutionResult(
                runtime=self.runtime,
                command=command,
                success=False,
                message=(
                    "Reader started successfully but "
                    "Odoo ACK is pending: "
                    f"{exc}"
                ),
            )

        return CommandExecutionResult(
            runtime=self.runtime,
            command=command,
            success=True,
            message=(
                "Reader started and Odoo ACK accepted."
            ),
        )

    def _execute_stop(
        self,
        *,
        command,
        planned_runtime,
    ):
        self.runtime = planned_runtime

        try:
            self.reader_executor.stop(
                session_key=command.session_key,
                reader_code=command.reader_code,
            )

            self.runtime = (
                self.runtime.mark_reader_stopped()
            )

            if self.before_session_close is not None:
                self.before_session_close(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                )

            close_local_session(
                session_key=command.session_key,
                reader_code=command.reader_code,
            )

        except Exception as exc:
            self.runtime = (
                self.runtime.mark_degraded(
                    str(exc)
                )
            )

            try:
                self.api_client.ack(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                    command=command.command.value,
                    revision=command.revision,
                    success=False,
                    message=str(exc),
                )
            except Exception:
                pass

            return CommandExecutionResult(
                runtime=self.runtime,
                command=command,
                success=False,
                message=str(exc),
            )

        self.runtime = (
            self.runtime.mark_command_completed(
                command
            )
        )

        try:
            self._send_completed_success_ack(
                command=command,
            )
        except Exception as exc:
            return CommandExecutionResult(
                runtime=self.runtime,
                command=command,
                success=False,
                message=(
                    "Reader stopped successfully but "
                    "Odoo ACK is pending: "
                    f"{exc}"
                ),
            )

        return CommandExecutionResult(
            runtime=self.runtime,
            command=command,
            success=True,
            message=(
                "Reader stopped and Odoo ACK accepted."
            ),
        )

    def _execute_abort(
        self,
        *,
        command,
        planned_runtime,
    ):
        self.runtime = planned_runtime

        try:
            self.reader_executor.stop(
                session_key=command.session_key,
                reader_code=command.reader_code,
            )

            self.runtime = (
                self.runtime.mark_reader_stopped()
            )

            if self.before_session_close is not None:
                self.before_session_close(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                )

            try:
                cancel_local_session(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                )
            except FinalSessionError:
                pass

        except Exception as exc:
            self.runtime = (
                self.runtime.mark_degraded(
                    str(exc)
                )
            )

            return CommandExecutionResult(
                runtime=self.runtime,
                command=command,
                success=False,
                message=str(exc),
            )

        self.runtime = (
            self.runtime.mark_command_completed(
                command
            )
        )

        try:
            self._send_completed_success_ack(
                command=command,
            )
        except Exception as exc:
            return CommandExecutionResult(
                runtime=self.runtime,
                command=command,
                success=False,
                message=(
                    "Reader abort completed but "
                    "Odoo ACK is pending: "
                    f"{exc}"
                ),
            )

        return CommandExecutionResult(
            runtime=self.runtime,
            command=command,
            success=True,
            message="Abort completed safely.",
        )

    def _reject_command(
        self,
        *,
        command,
        message,
    ):
        try:
            self.api_client.ack(
                session_key=command.session_key,
                reader_code=command.reader_code,
                command=command.command.value,
                revision=command.revision,
                success=False,
                message=message,
            )
        except Exception:
            pass

        return CommandExecutionResult(
            runtime=self.runtime,
            command=command,
            success=False,
            message=message,
        )
