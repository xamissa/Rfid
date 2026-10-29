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
        after_reader_start=None,
        before_reader_stop=None,
        before_session_close=None,
        before_success_ack=None,
    ):
        self.api_client = api_client
        self.reader_executor = reader_executor
        self.after_reader_start = after_reader_start
        self.before_reader_stop = before_reader_stop
        self.before_session_close = before_session_close
        self.before_success_ack = before_success_ack
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
        if (hasattr(self.reader_executor, "window_error")
                and command.command != RuntimeCommand.ABORT):
            try:
                self.reader_executor.require_healthy()
            except Exception as exc:
                self.runtime = self.runtime.mark_degraded(str(exc))
                return self._reject_command(command=command, message=str(exc))
            # Health may recover during HTTP polling after the cycle marked an
            # empty runtime degraded. Apply the same recovery rule as run_once,
            # before rejecting a fresh START. Never clear an attached window.
            if (command.command == RuntimeCommand.START
                    and self.runtime.state == RuntimeState.DEGRADED
                    and self.runtime.session_key is None
                    and not self.reader_executor.is_active):
                self.mark_reader_verified_idle()
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
        if (
            command.command == RuntimeCommand.STOP
            and self.before_success_ack is not None
        ):
            self.before_success_ack(
                session_key=command.session_key,
                reader_code=command.reader_code,
            )

        if (hasattr(self.reader_executor, "window_error")
                and command.command != RuntimeCommand.ABORT):
            self.reader_executor.require_healthy()

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

        session_sync = None

        try:
            session_sync = synchronize_start_command(
                command=command,
            )

            if hasattr(self.reader_executor, 'set_window_metadata'):
                self.reader_executor.set_window_metadata(
                    operation=session_sync.session.operation_type,
                    revision=command.revision,
                )
            self.reader_executor.start(
                session_key=command.session_key,
                reader_code=command.reader_code,
            )

            self.runtime = (
                self.runtime.mark_reader_started()
            )

            if self.after_reader_start is not None:
                self.after_reader_start(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                )

        except Exception as exc:
            cleanup_error = None
            stop_error = None

            if self.reader_executor.is_active:
                try:
                    if self.before_reader_stop is not None:
                        self.before_reader_stop(
                            session_key=command.session_key,
                            reader_code=command.reader_code,
                        )

                    self.reader_executor.stop(
                        session_key=command.session_key,
                        reader_code=command.reader_code,
                    )

                    if self.before_session_close is not None:
                        self.before_session_close(
                            session_key=command.session_key,
                            reader_code=command.reader_code,
                        )

                except Exception as stop_exc:
                    stop_error = stop_exc

            if (
                session_sync is not None
                and session_sync.created
            ):
                try:
                    cancel_local_session(
                        session_key=command.session_key,
                        reader_code=command.reader_code,
                    )
                except Exception as cleanup_exc:
                    cleanup_error = cleanup_exc

            error_message = str(exc)

            if stop_error is not None:
                error_message = (
                    f"{error_message}; emergency reader STOP "
                    f"also failed: {stop_error}"
                )

            if cleanup_error is not None:
                error_message = (
                    f"{error_message}; newly-created local session "
                    "cleanup also failed: "
                    f"{cleanup_error}"
                )

            self.runtime = (
                self.runtime.mark_degraded(
                    error_message
                )
            )

            try:
                self.api_client.ack(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                    command=command.command.value,
                    revision=command.revision,
                    success=False,
                    message=error_message,
                )
            except Exception:
                pass

            return CommandExecutionResult(
                runtime=self.runtime,
                command=command,
                success=False,
                message=error_message,
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
            if self.before_reader_stop is not None:
                self.before_reader_stop(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                )

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
            if self.before_reader_stop is not None:
                self.before_reader_stop(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                )

            if hasattr(self.reader_executor, "abort_window"):
                self.reader_executor.abort_window(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                )
            elif self.reader_executor.is_active:
                self.reader_executor.stop(
                    session_key=command.session_key,
                    reader_code=command.reader_code,
                )
            else:
                # Recovery ABORT may arrive after START never reached this
                # process. Missing persistent state is not proof that the
                # hardware is idle, so require a fresh physical STOP/ACK.
                self.reader_executor.verify_idle()

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
