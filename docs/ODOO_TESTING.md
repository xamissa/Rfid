# RFID Bridge Odoo Testing

This guide defines the safe process for implementing and validating delivery from the RFID Bridge to Odoo.

## Safety boundary

Do not allow the bridge to contact Odoo until the endpoint, authentication, and message contracts are confirmed.

Before Odoo testing, the required state is:

```text
SENDER_BACKEND=disabled
ALLOW_ODOO_CONTACT=false
READER_BACKEND=fake
ALLOW_PHYSICAL_READER_CONTACT=false
rfid-bridge-worker.service: inactive and disabled
```

Initial Odoo testing must use simulated RFID events only.

## Required Odoo contract information

Collect and confirm all of the following:

- Odoo.sh hostname
- database name
- supported environment for testing
- endpoint path
- HTTP method
- authentication method
- credential storage requirements
- request headers
- request schema
- response schema
- success status codes
- error status codes
- idempotency key requirements
- duplicate-event behaviour
- receipt session start and stop contract
- dispatch session start and stop contract
- timeout requirements
- retry requirements
- rate limits

## Staged implementation and testing sequence

Use this order. Do not skip stages.

1. Confirm the complete Odoo contract.
2. Implement the sender behind the existing backend selector.
3. Store credentials only in the secrets configuration file.
4. Add offline request-construction tests.
5. Add offline response-classification tests.
6. Verify Odoo contact remains blocked.
7. Create simulated RFID sessions and events locally.
8. Run delivery logic with the disabled sender.
9. Confirm no network request occurs.
10. Obtain explicit approval for controlled Odoo test contact.
11. Use a non-production Odoo environment where available.
12. Enable Odoo contact for the controlled test window only.
13. Send one known simulated event.
14. Confirm the Odoo response and resulting local delivery state.
15. Test approved duplicate and retry scenarios.
16. Disable Odoo contact after the test.
17. Review evidence before enabling ongoing worker execution.

## Offline sender validation

Before any Odoo contact, validate:

- endpoint URL construction
- request headers
- authentication header construction without logging secrets
- request payload fields and types
- idempotency key generation
- timeout values
- success response classification
- retryable failure classification
- permanent failure classification
- malformed response handling

Offline sender tests must not create outbound network connections.

## Controlled Odoo contact test

Use one simulated RFID event with a known EPC and session role.

Record before the test:

- local event identifier
- session identifier
- EPC value
- expected Odoo action
- expected response
- test start time

After the request, confirm:

- exactly one outbound request was made
- the response was classified correctly
- the local delivery attempt state is correct
- the event was not duplicated
- no unrelated Odoo records were changed
- credentials were not written to logs

## Retry and duplicate testing

Test only approved failure scenarios.

The test set should include:

- one successful delivery
- one retryable timeout or temporary server failure
- one permanent validation failure
- one repeated delivery using the same idempotency key
- one malformed response

Retry delays and maximum attempts must match the approved operational settings.

Permanent failures must not retry indefinitely.

## Acceptance criteria

Odoo testing passes only when all of the following are proven:

- request payloads match the approved contract
- authentication works without exposing credentials
- successful events are marked delivered exactly once
- retryable failures are retried within configured limits
- permanent failures stop safely
- duplicate delivery is prevented or handled by the approved policy
- session role and event context are preserved
- timeouts do not block the worker indefinitely
- evidence contains no secrets
- physical reader contact remains disabled

If any criterion fails, restore the disabled sender and blocked Odoo-contact settings.
