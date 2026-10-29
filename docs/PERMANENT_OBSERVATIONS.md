# Permanent RFID observations

The permanent observation ledger runs alongside the existing transactional path.
It does not replace `RawRFIDEvent`, session+EPC identities, session assignment,
Receipt/Delivery reconciliation, or the transactional STOP delivery barrier.

```text
reader → framing/CRC → all valid tag reports → durable open aggregate
       → finalized RFIDObservation + ObservationOutbox → leased HTTP batches
       → /api/rfid/v1/observations → Odoo rfid.observation

healthy attached window → existing final transactional ingestion → /events
```

## Capture and boundaries

`ObservationCapture` groups a receive batch by normalized EPC and exact antenna.
Antenna `None` means unknown; antenna 0 remains distinct. The persistent open-key
constraint also includes device, context generation and connection epoch. Each actual TCP connection, including verify-idle connections, advances the
epoch through the executor connection hook. Process runs get new UUIDs; startup finalizes old open aggregates as `restart`. The local
DB is authoritative, with monotonic timers only as an acceleration for deadlines.

Every aggregate receives its UUID at creation. `raw_read_count` counts decoded,
EPC-bearing tag reports, not the reader's optional count. Latest reader count,
sequence, protocol, TID and PC are diagnostic metadata only. EPC storage supports
up to 510 hex characters from the protocol's one-byte EPC length.

Reports are observed before transactional eligibility filtering, including normal
ambient draining, START admission, physical START/STOP/verify-idle responses and
all tags in a multi-frame batch on both sides of the control ACK. Pending frames
have already passed the observation sink, so draining them does not count twice.
Inactivity, maximum duration, capacity, START, STOP, ABORT, reconnect and shutdown
finalize intervals. Capacity pressure finalizes durably instead of eviction.

Context is frozen at capture:

| context_kind | session_key | operation |
|---|---|---|
| receipt | attached external key | receipt |
| delivery | attached external key | dispatch |
| no_transfer | null | null |
| unresolved | null | null |

Unresolved reports retain `related_session_key` when a failed connection poisoned
the business window. They never silently return to transactional attribution.
START admission frames retain the previous context. STOP and ABORT finalize the
old generation before switching. Odoo derives product/picking/company/business
results; the Pi sends capture facts only. No stock validation or quant action is
introduced. The existing operation fallback to configured reader role is retained.

## Durability and local failure

`RFIDObservation` is mutable while open. Finalization freezes JSON and a canonical
SHA-256 fingerprint and atomically creates its one-to-one `ObservationOutbox`.
Finalized model saves are refused; delivery verifies the fingerprint before send.
Outbox delivery fields are separate from the capture payload.

Normal active capture uses an outer local DB transaction for both observations
and existing transactional ingestion. If the business path fails, it fails closed;
the receive batch is explicitly recovered as capture evidence, without retrying
stock/session assignment. Failed receive batches retain original UTC receipt time,
context snapshot, generation and epoch. They are retried before further socket
polling, even if ABORT happened meanwhile. Local persistence failure is logged and
capture becomes degraded. This bounded RAM failure buffer is not a substitute for
durable storage: power loss before commit, full/failed storage, or RF activity while
disconnected cannot be guaranteed lossless. There is no silent eviction policy.

Only one capture worker may own a physical reader. Do not run multiple capture
processes against the same device. PostgreSQL is the deployment backend; SQLite
is used only in the self-contained offline tests.

## Delivery and recovery

`run_rfid_observation_delivery_worker` is independent of physical capture. It uses
existing final runtime gateway/API configuration and requires the Odoo contact
gate. This document does not authorize deployment or enabling that gate.

The endpoint receives the existing JSON-RPC envelope with params `gateway_code`,
`reader_code`, `schema_version=1`, and `observations`. Each item contains the
required capture fields, nullable antenna/session/operation, UUID generations,
finalization reason and context/diagnostic metadata. Local `payload_hash` is not
required on the wire. If Odoo returns a hash, it must match. Without a returned
hash, duplicate success relies on Odoo enforcing immutable UUID identity.

Responses must have `ok=true` and a `results` list with exactly one matching UUID
per acknowledged item. `status` (or existing-contract `outcome`) is accepted;
contradictory values, missing/duplicate matches and malformed items are retried.
`accepted`/`duplicate` deliver; `conflict` quarantines. `rejected` quarantines unless
explicitly retryable or a recognized temporary reason (`server_busy`,
`rate_limited`, `temporarily_unavailable`). Quarantine never deletes data.

Selection and HTTP batching are distinct: batches split by actual encoded request
bytes and item count. Underfilled queues flush after the configured interval.
Leases have owner, token and expiry. HTTP happens outside all capture/DB locks.
Completion is fenced by token. Expired leases become pending and their abandoned
attempt evidence is marked `lease_expired`. No retry-count exhaustion deletion.

429 honors seconds or HTTP-date Retry-After and pauses delivery. 401/403 pause the
worker and delay affected rows; capture continues. 413 recursively splits the
batch; an individually rejected oversize item is quarantined. Other transport,
5xx and protocol errors retry identical frozen payloads. Lost responses/restarts
reuse UUIDs. Partial responses acknowledge only explicitly correlated items.
Unknown whole-request 4xx errors are retained/retried with warnings for review.

The transactional sender remains one HTTP request per transactional event. This
change batches the permanent ledger only; it does not silently rewrite the
existing /events delivery state machine.

## Configuration

Values are optional entries in the existing deployment CONFIGURATION mapping,
using the normal application env-file mechanism; no new secret setting is needed.

| Key | Default |
|---|---:|
| OBSERVATION_INACTIVITY_SECONDS | 1.0 |
| OBSERVATION_MAX_DURATION_SECONDS | 10.0 |
| OBSERVATION_ACTIVE_KEY_LIMIT | 10000 |
| OBSERVATION_BATCH_SIZE | 100 |
| OBSERVATION_BATCH_FLUSH_SECONDS | 1.0 |
| OBSERVATION_MAX_PAYLOAD_BYTES | 262144 |
| OBSERVATION_REQUEST_TIMEOUT_SECONDS | 10 |
| OBSERVATION_RETRY_INITIAL_SECONDS | 1.0 |
| OBSERVATION_RETRY_MAX_SECONDS | 300.0 |
| OBSERVATION_QUEUE_HIGH_WATER | 100000 |

Values must be finite/positive. HTTP timeout is 1–300 seconds. Retry delay grows
exponentially with jitter and is capped; Retry-After may exceed the cap. A lease
lasts `2 * request timeout + 30` seconds and renews before each actual request.
The gateway code and reader code must remain stable identities across retries.

## Operations and limitations

Logs use key/value messages: startup/recovery, batch counts and reader/batch UUIDs
at INFO; unresolved context, recovered leases, 429/auth, quarantine, high-water and
storage degradation at WARNING; persistence/database/unexpected response failures
at ERROR. No successful per-item INFO logs, credentials, request bodies or
Authorization headers. Attempt error data is bounded; arbitrary server text is
not copied into logs. Existing application settings enable the operational logger.

`observation_metrics()` exposes pending count, oldest pending timestamp,
quarantined count and open count. The oldest timestamp supports age calculation.
High-water monitoring is rate-limited to once per minute. No automatic retention
or deletion is implemented, including delivered data. Disk/database capacity and
WAL growth must be monitored externally; the repository has no existing disk
pressure monitor. A full disk cannot support indefinite capture.

An always-present tag rolls into new observations at the max duration. For 1,000
stationary tags and a 10-second max duration, this can mean 8.64 million records/day.
Tune capacity/window/retention only after measuring real dwell and repeat rates.
Local durable boundary flushes can add DB latency to command admission; network
quiet-boundary reads remain bounded, but DB commit latency is not a hard wall-clock
promise. Production PostgreSQL concurrency and storage benchmarks remain required.

Hardware commissioning must confirm antenna numbering/population, report rates,
reader count semantics, buffering and STOP/reconnect behavior. No RSSI, reader
capture timestamp, direction detection or verified reader-count semantics are
claimed. Odoo response schema/optional metadata acceptance needs combined review;
this implementation has never contacted staging or production.

## Offline validation

`config.test_settings` is a permanent, reusable SQLite test configuration that
never reads deployment env/secrets or PostgreSQL. Use the existing Python
interpreter with `PYTHONDONTWRITEBYTECODE=1`:

```text
python manage.py test bridge_core.test_observations --settings=config.test_settings --noinput
python manage.py test bridge_core --settings=config.test_settings --noinput
python manage.py makemigrations --check --dry-run --settings=config.test_settings
python manage.py check --settings=config.test_settings
```

Synthetic tests assert counts/query bounds rather than wall-clock timings and use
mocked HTTP and in-memory physical-reader doubles exclusively.

Validation recorded for this candidate: strengthened focused suite 196 passed;
broad suite 456 tests, 433 passed, 16 failures and 7 errors. The 23 failing test
names match the c4a84f9 candidate baseline under identical offline settings (420
tests, 397 passed). No unrelated baseline fixes were made. The comparison loaded
baseline versions of modified modules with a read-only git-show import hook and
ran its original test modules; no alternate checkout or live DB was used.

Synthetic capture/delivery results:

| Unique EPCs | Capture SQL queries | Durable observations | Peak pending outbox | Observation HTTP calls |
|---:|---:|---:|---:|---:|
| 100 | 6 | 100 | 100 | 1 |
| 1000 | 29 | 1000 | 1000 | 10 |

SQL counts include SQLite transaction bookkeeping. A repeated single EPC burst
of 1000 reports creates one open observation with count 1000. No timing claims
are inferred from these offline results. Migration consistency, Django system
checks, Python AST parsing and git diff whitespace checks also pass.

The final focused command used the same interpreter/settings and these labels:

```text
python manage.py test bridge_core.test_observations bridge_core.test_final_reader_executor bridge_core.test_final_capture_service bridge_core.test_final_runtime_state bridge_core.test_final_runtime_orchestrator bridge_core.test_final_worker_cycle bridge_core.test_final_worker_command bridge_core.test_final_worker_shutdown bridge_core.test_final_tag_ingestion bridge_core.test_shared_reader_poc bridge_core.test_final_reader_supervisor bridge_core.test_final_supervisor_orchestrator bridge_core.test_reader_receive_budget bridge_core.test_reader_transport bridge_core.tests.test_final_stop_delivery_barrier bridge_core.test_final_delivery_cycle bridge_core.test_final_odoo_event_sender bridge_core.test_odoo_api_v1 --settings=config.test_settings --noinput
```

Shutdown attempts physical STOP and checks its ACK even if observation persistence
fails. Persistence errors remain visible, retained reports are retried where
possible, and the socket is closed. A storage failure never becomes a success ACK.

## Matching Odoo compatibility observations

The observation-capable `FinalOdooEventSender` sends request-level
`observation_ledger_managed: true` on `/api/rfid/v1/events` as an actual JSON
boolean. Transactional events, UUIDs, counting, ACK handling and STOP barriers are
unchanged. The flag tells Odoo that `/observations` independently owns permanent
history; it does not suppress transactional business delivery. The API client's
default remains omission for older/non-observation callers; string boolean values
are refused. `/observations` has no compatibility flag and its payload is unchanged.

The supplied observation contract matches the envelope and required fields above.
Technical context values are exactly `receipt`, `delivery`, `no_transfer`, and
`unresolved`. The existing documented delivery operation hint remains `dispatch`;
command/session operation semantics are unchanged. No Odoo controller source is
present here, so server acceptance of that hint and diagnostic extras cannot be
independently verified offline. Confirm that contract during combined review;
no staging/production contact is authorized by this document.

## Deployment prerequisites — approval required

No unit has been installed, enabled or started by this implementation. Do not
contact the reader or any Odoo environment until deployment/live testing is
explicitly approved. The approved rollout must include all of the following:

1. Apply Django migration `0016_rfidobservation_observationoutbox_and_more` to the
   intended deployment database before running updated workers.
2. Deploy the updated final capture worker and its observation pipeline. Run
   exactly one capture worker per physical reader, including shared Receipt/Dispatch
   readers; do not launch a second capture worker for the observation ledger.
3. Install the repository unit `deploy/systemd/rfid-observation-delivery.service`
   and enable/start it only as part of the approved rollout. The current generic
   installer does not install this additional unit automatically.
4. Keep `rfid-final-delivery.service` deployed and running for transactional
   `/events` delivery. The observation delivery service is additional and may run
   independently of capture to drain durable backlog; it never owns the reader.
5. Use the existing gateway/reader identity, Odoo URL, bearer credentials, TLS
   configuration and contact gates. No new credentials are required. Match the
   existing `rfid-final-delivery.service` ownership (`rfidbridge:rfidbridge`),
   application path (`/opt/rfid_bridge/app`) and interpreter
   (`/opt/rfid_bridge/venv/bin/python`). The new service command is
   `manage.py run_rfid_observation_delivery_worker`.
6. Keep the existing environment loading convention: Django reads
   `/etc/rfid_bridge/app.env` and `/etc/rfid_bridge/secrets.env`. The canonical units
   do not contain `EnvironmentFile=` or credentials. Observation options are the
   optional defaults listed in Configuration; validate capacity and delivery tuning
   before enabling contact. Existing final gates and `SENDER_BACKEND=disabled`
   remain required for the complete final-worker deployment.
7. Coordinate the flag-capable transactional sender with observation-capable capture
   and the flag-capable Odoo API. Historical queued transactional events from before
   observation capture need an explicitly approved backlog-drain/backfill plan;
   the flag must not be mistaken for a historical observation backfill.

The unit follows the final delivery service's restart, dependency and hardening
settings. It intentionally has no dependency on the capture service. This is a
canonical source addition only; no systemd install/reload/enable operation has
been performed.

Matching-layer validation: 203 focused offline tests passed, including the
previous focused command with `bridge_core.test_observation_matching` added.
Tests inspect actual serialized `/events` JSON for a request-level boolean true,
legacy omission, unchanged `/observations` fields, all context values, dual-path
business capture, ambient-only capture, EPC idempotency, barriers, batching, and
the canonical unit source. Migration consistency, Django system checks, Python
AST parsing and git diff --check passed. No schema/migration change was needed
for the compatibility flag or service source. No live contract test was run.
