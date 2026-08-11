# RFID Bridge Hardware Testing

This guide defines the safe process for connecting and validating a physical RFID reader.

## Safety boundary

Do not connect the bridge to a physical reader until the reader protocol and network details are confirmed.

Before testing, the required application state is:

```text
READER_BACKEND=fake
ALLOW_PHYSICAL_READER_CONTACT=false
SENDER_BACKEND=disabled
ALLOW_ODOO_CONTACT=false
rfid-bridge-worker.service: inactive and disabled
```

Hardware testing must not contact Odoo.

## Required reader information

Collect and confirm all of the following:

- manufacturer
- model number
- firmware version
- connection type
- reader IP address
- reader TCP or UDP port
- protocol documentation
- tag event message format
- EPC encoding and length
- antenna identifiers
- heartbeat format and interval
- reconnect behaviour
- duplicate-read behaviour
- authentication requirements
- receiving and dispatch door mapping

## Staged testing sequence

Use this order. Do not skip stages.

1. Confirm the reader documentation and connection details.
2. Confirm the Pi can reach the reader network without opening an application connection.
3. Implement the reader backend behind the existing backend selector.
4. Add offline parser tests using captured sample messages.
5. Run the backend with physical contact still blocked.
6. Review logs and confirm no connection attempt occurred.
7. Obtain explicit approval for a controlled reader connection test.
8. Enable physical reader contact for the test window only.
9. Capture a small number of known test tags.
10. Confirm parsed events are correct and duplicates are handled.
11. Disable physical reader contact after the test.
12. Review evidence before enabling any ongoing worker execution.

## Network-only checks

Before application contact is allowed, confirm the Pi network configuration:

```bash
hostname -I
ip route
ip address show
```

Confirm the reader address belongs to the expected hardware network.

Do not use intrusive scanning or write commands against the reader.

Basic reachability checks must be approved by the hardware owner.

## Offline parser validation

Before contacting the physical reader, test the parser with captured sample messages.

The test set must include:

- one valid tag message
- multiple valid tag messages
- malformed input
- incomplete input
- heartbeat input
- unknown message type
- duplicate event input
- reconnect boundary input

Parser failures must not crash the worker or create incomplete RFID events.

## Controlled tag test

Use a small, labelled set of test tags whose EPC values are known in advance.

For each tag, record:

- expected EPC
- reader antenna
- door role
- test start time
- number of physical passes
- expected event count

Do not use live stock items during the first hardware test.

## Acceptance criteria

Hardware testing passes only when all of the following are proven:

- the reader connection is stable
- valid EPC values are parsed correctly
- receiving and dispatch roles are mapped correctly
- heartbeat messages do not create tag events
- malformed messages are rejected safely
- duplicate reads follow the approved duplicate policy
- disconnect and reconnect behaviour is controlled
- no Odoo contact occurs
- no unexpected worker persistence occurs
- evidence contains no secrets

If any criterion fails, restore the fail-closed reader settings and stop testing.
