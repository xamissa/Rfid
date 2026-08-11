from dataclasses import dataclass
from enum import Enum


FINAL_GATEWAY_CODE = "RFID-GW-01"
FINAL_RECEIVING_READER_CODE = "receiving-door-01"


class RuntimeState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    READING = "reading"
    STOPPING = "stopping"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class RuntimeCommand(str, Enum):
    START = "start"
    STOP = "stop"
    ABORT = "abort"


class RuntimeStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class OdooRFIDCommand:
    session_key: str
    reader_code: str
    command: RuntimeCommand
    revision: int
    picking: str = ""

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict):
            raise RuntimeStateError(
                "RFID command payload must be a dictionary."
            )

        session_key = str(
            payload.get("session_key") or ""
        ).strip()
        reader_code = str(
            payload.get("reader_code") or ""
        ).strip()
        raw_command = str(
            payload.get("command") or ""
        ).strip()
        picking = str(
            payload.get("picking") or ""
        ).strip()

        if not session_key:
            raise RuntimeStateError(
                "RFID command is missing session_key."
            )

        if not reader_code:
            raise RuntimeStateError(
                "RFID command is missing reader_code."
            )

        try:
            command = RuntimeCommand(raw_command)
        except ValueError as exc:
            raise RuntimeStateError(
                f"Unsupported RFID command: {raw_command!r}"
            ) from exc

        try:
            revision = int(payload.get("revision"))
        except (TypeError, ValueError) as exc:
            raise RuntimeStateError(
                "RFID command revision must be an integer."
            ) from exc

        if revision < 1:
            raise RuntimeStateError(
                "RFID command revision must be positive."
            )

        return cls(
            session_key=session_key,
            reader_code=reader_code,
            command=command,
            revision=revision,
            picking=picking,
        )


@dataclass(frozen=True)
class LocalReaderRuntime:
    reader_code: str
    state: RuntimeState = RuntimeState.IDLE
    session_key: str | None = None
    last_command_revision: int = 0
    error: str | None = None
    completed_command_revision: int = 0
    completed_command: RuntimeCommand | None = None
    completed_session_key: str | None = None

    def heartbeat_payload(self):
        return {
            "reader_code": self.reader_code,
            "state": self.state.value,
            "session_key": self.session_key,
            "error": self.error,
        }

    def validate_command(self, command: OdooRFIDCommand):
        if command.reader_code != self.reader_code:
            raise RuntimeStateError(
                "Command reader identity does not match this runtime."
            )

        if command.revision < self.last_command_revision:
            raise RuntimeStateError(
                "Command revision is older than the last processed revision."
            )

        if (
            command.revision == self.last_command_revision
            and self.last_command_revision != 0
        ):
            if (
                command.revision
                == self.completed_command_revision
                and command.command
                == self.completed_command
                and command.session_key
                == self.completed_session_key
            ):
                return "duplicate"

            raise RuntimeStateError(
                "Command revision matches the last processed revision "
                "but is not the exact completed command."
            )

        return "new"

    def plan_command(self, command: OdooRFIDCommand):
        disposition = self.validate_command(command)

        if disposition == "duplicate":
            return RuntimeTransition(
                before=self,
                after=self,
                command=command,
                duplicate=True,
            )

        if command.command == RuntimeCommand.START:
            return self._plan_start(command)

        if command.command == RuntimeCommand.STOP:
            return self._plan_stop(command)

        if command.command == RuntimeCommand.ABORT:
            return self._plan_abort(command)

        raise RuntimeStateError(
            "Unsupported RFID runtime command."
        )

    def _plan_start(self, command):
        if self.state != RuntimeState.IDLE:
            raise RuntimeStateError(
                f"START is invalid while reader state is {self.state.value}."
            )

        if self.session_key:
            raise RuntimeStateError(
                "START is invalid while a local session key is present."
            )

        after = LocalReaderRuntime(
            reader_code=self.reader_code,
            state=RuntimeState.STARTING,
            session_key=command.session_key,
            last_command_revision=command.revision,
            error=None,
        )

        return RuntimeTransition(
            before=self,
            after=after,
            command=command,
        )

    def _plan_stop(self, command):
        if self.state != RuntimeState.READING:
            raise RuntimeStateError(
                f"STOP is invalid while reader state is {self.state.value}."
            )

        if self.session_key != command.session_key:
            raise RuntimeStateError(
                "STOP session_key does not match active local session."
            )

        after = LocalReaderRuntime(
            reader_code=self.reader_code,
            state=RuntimeState.STOPPING,
            session_key=self.session_key,
            last_command_revision=command.revision,
            error=None,
        )

        return RuntimeTransition(
            before=self,
            after=after,
            command=command,
        )

    def _plan_abort(self, command):
        if (
            self.session_key
            and self.session_key != command.session_key
        ):
            raise RuntimeStateError(
                "ABORT session_key does not match local session."
            )

        after = LocalReaderRuntime(
            reader_code=self.reader_code,
            state=RuntimeState.STOPPING,
            session_key=command.session_key,
            last_command_revision=command.revision,
            error=None,
        )

        return RuntimeTransition(
            before=self,
            after=after,
            command=command,
        )

    def mark_reader_started(self):
        if self.state != RuntimeState.STARTING:
            raise RuntimeStateError(
                "Reader can only become READING from STARTING."
            )

        if not self.session_key:
            raise RuntimeStateError(
                "Cannot enter READING without a session key."
            )

        return LocalReaderRuntime(
            reader_code=self.reader_code,
            state=RuntimeState.READING,
            session_key=self.session_key,
            last_command_revision=self.last_command_revision,
            error=None,
        )

    def mark_reader_stopped(self):
        if self.state != RuntimeState.STOPPING:
            raise RuntimeStateError(
                "Reader can only become IDLE from STOPPING."
            )

        return LocalReaderRuntime(
            reader_code=self.reader_code,
            state=RuntimeState.IDLE,
            session_key=None,
            last_command_revision=self.last_command_revision,
            error=None,
        )

    def mark_command_completed(
        self,
        command: OdooRFIDCommand,
    ):
        if command.reader_code != self.reader_code:
            raise RuntimeStateError(
                "Completed command reader does not match runtime."
            )

        if command.revision != self.last_command_revision:
            raise RuntimeStateError(
                "Completed command revision does not match "
                "the runtime revision."
            )

        return LocalReaderRuntime(
            reader_code=self.reader_code,
            state=self.state,
            session_key=self.session_key,
            last_command_revision=self.last_command_revision,
            error=self.error,
            completed_command_revision=command.revision,
            completed_command=command.command,
            completed_session_key=command.session_key,
        )

    def mark_degraded(self, error):
        message = str(error or "").strip()

        if not message:
            raise RuntimeStateError(
                "Degraded runtime requires an error message."
            )

        return LocalReaderRuntime(
            reader_code=self.reader_code,
            state=RuntimeState.DEGRADED,
            session_key=self.session_key,
            last_command_revision=self.last_command_revision,
            error=message,
        )


@dataclass(frozen=True)
class RuntimeTransition:
    before: LocalReaderRuntime
    after: LocalReaderRuntime
    command: OdooRFIDCommand
    duplicate: bool = False
