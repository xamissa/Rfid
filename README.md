# RFID Bridge

Python and Django bridge for forwarding fixed-reader RFID events to Odoo through a controlled local queue.

## Current status

The application foundation, local database, authenticated web interface, monitoring pages, deployment scripts, and offline worker paths are implemented.

Physical reader and Odoo integrations remain disabled until their technical contracts are confirmed and controlled tests pass.

## Safe default state

```text
READER_BACKEND=fake
SENDER_BACKEND=disabled
ALLOW_PHYSICAL_READER_CONTACT=false
ALLOW_ODOO_CONTACT=false
rfid-bridge-worker.service: inactive and disabled
```

## Start here

- Installation: `docs/INSTALLATION.md`
- Configuration: `docs/CONFIGURATION.md`
- Operations: `docs/OPERATIONS.md`
- Hardware testing: `docs/HARDWARE_TESTING.md`
- Odoo testing: `docs/ODOO_TESTING.md`
- Troubleshooting: `docs/TROUBLESHOOTING.md`
- Handover checklist: `docs/HANDOVER_CHECKLIST.md`

## Important

Do not enable the worker or either external integration until explicit approval is given.
