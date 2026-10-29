Phase 1 isolated implementation review
======================================

Hardware state (offline/active/reconnecting) is owned by ContinuousReaderSupervisor.
LocalReaderRuntime remains the business transaction state. The physical executor
uses a private inventory key; business START/STOP attach/detach a window under the
same lock used to read and persist observations. ABORT clears an interrupted
window. Existing local session synchronization, EPC idempotency, cancellation,
reconciliation and STOP delivery barrier remain in the orchestrator/services.

The supervisor starts inventory independently of commands. Ambient frames are
discarded, including startup/recovery frames. Attach has a 0.5-second monotonic deadline including lock acquisition, an
8-batch cap, and 50ms receive budgets. It requires an empty receive and empty
protocol decoder. A partial frame rejects admission immediately. No quiet
boundary means a rejected START. Frames accepted before detach finish persistence
before STOP returns. Frames afterward are ambient. No ambient database history
or Odoo delivery is created.

Hardware failures enter reconnecting; retries wait 0.5 through 10 seconds with
capped exponential backoff. Recovery verifies STOP ACK, starts inventory and
resumes draining. Interrupted business windows remain poisoned until ABORT;
recovery never silently resumes them. Odoo transport retries remain independent.
Shutdown joins capture, sends STOP, requires its ACK and closes the connection.
If disconnected it attempts fresh STOP verification; failures propagate.

Offline validation (no deployment settings loaded)
------------------------------------------------
Interpreter: /opt/rfid_bridge/venv/bin/python
All commands used PYTHONDONTWRITEBYTECODE=1.

Focused command:
manage.py test bridge_core.test_final_reader_supervisor bridge_core.test_final_supervisor_orchestrator bridge_core.test_reader_receive_budget bridge_core.test_reader_transport bridge_core.test_final_reader_executor bridge_core.test_final_capture_service bridge_core.test_final_runtime_state bridge_core.test_final_runtime_orchestrator bridge_core.test_final_worker_cycle bridge_core.test_final_worker_command bridge_core.test_final_worker_shutdown bridge_core.test_final_tag_ingestion bridge_core.test_shared_reader_poc bridge_core.tests.test_final_stop_delivery_barrier --settings=isolated_test_settings --noinput

142 tests passed.

Broad command:
manage.py test bridge_core --settings=isolated_test_settings --noinput

420 tests: 397 passed, 16 failures, 7 errors.

Baseline comparison command: baseline_test_runner.py
Loads changed pre-existing modules and command tests directly from baseline
commit e448d3120e127bc4456e5f1424d57dffe744c7bc via git show. Other source
files are unchanged; the baseline loader also restores reader_transport.py.
396 baseline tests: 373 passed, 16 failures, 7 errors. The 24 new tests
have no baseline implementation and are excluded from baseline discovery.
Failing test names match exactly.
These are baseline/offline-settings failures, not new regressions. Full traces
are in baseline-test-results.txt and regression-test-results.txt.

git diff --check passed. No commit, push, service operations, reader contact,
Odoo contact, deployment configuration loading or PostgreSQL access performed.

Review limitations
------------------
A busy reader may prevent START (bounded quiet-boundary admission). This is a
conservative phase-1 policy that needs operator acceptance. The protocol supplies
no observation timestamp fence: arbitrarily delayed reader/network observations
cannot be proven to have occurred after attachment. The fence excludes locally
buffered complete and partial frames, but absolute RF-time attribution needs a
hardware-supported fence/timestamp or a later admission policy.

Failed active windows require ABORT; automatic business continuation is deliberately
unsupported. Persistence errors also poison the window conservatively. Existing
legacy executor/capture classes retain their APIs for isolated tests; production
worker uses the supervisor. Supervisor/orchestrator integration tests now exercise ACK failure/replay,
hardware failure before success ACK, poisoned-window recovery via ABORT,
persisted event idempotency, shared Receipt/Dispatch windows, and the real delivery
barrier (queued/retry/inflight/rejected/dead/sent). Physical inventory is never
restarted or stopped by completed command retries.

Admission implementation
------------------------
The public TCPReaderSession.has_partial_frame property owns decoder inspection;
the executor no longer reaches through private transport fields. The optional
receive timeout_seconds argument bounds the entire receive, not each socket read
independently. It recalculates remaining monotonic time before each recv and
restores the configured read timeout in finally, including on errors. An ordinary
quiet read returns empty; partial protocol state never establishes admission.
Hard admission socket errors enter reconnecting and return ReaderSupervisorError.

Supervisor health checks wait at most 50ms for the lock, so Odoo command admission
and ACK checks cannot hang behind a reconnect using the device's potentially
300-second read timeout. Reader-side START overhead is bounded by the 500ms
admission budget plus up to two 50ms health checks (apart from OS scheduling).
Business database work and Odoo HTTP have their own latency and timeout behavior;
this does not impose a deadline on those. The Odoo API client defaults to a 10s
request timeout; no server-side command expiry value is defined in this repository.
Confirm that deployment value during manual review without contacting Odoo here.

Candidate file set (retain for eventual review/commit)
----------------------------------------------------
bridge_core/final_reader_executor.py
bridge_core/final_reader_supervisor.py
bridge_core/final_runtime_orchestrator.py
bridge_core/final_worker_cycle.py
bridge_core/management/commands/run_final_rfid_worker.py
bridge_core/reader_transport.py
bridge_core/test_final_reader_supervisor.py
bridge_core/test_final_supervisor_orchestrator.py
bridge_core/test_final_worker_command.py
bridge_core/test_reader_receive_budget.py
docs/ALWAYS_ON_PHASE1_REVIEW.md

Temporary review artifacts (exclude from eventual commit)
--------------------------------------------------------
focused-test-results.txt
regression-test-results.txt
baseline-test-results.txt
baseline_test_runner.py
isolated_test_settings.py

Evidence is retained untracked in the worktree. The isolated settings/harness are
review-only; no reason to include them in the candidate commit. git diff --stat
shows only tracked modifications, so it omits the untracked supervisor, three
permanent test modules and this document. Nothing has been staged or committed.

Manual candidate review is appropriate; deployment acceptance is not established.
