# RFID Bridge

Python and Django bridge for forwarding fixed-reader RFID events to Odoo through a controlled local queue.

## Current status

The Raspberry Pi bridge, local PostgreSQL persistence, authenticated Django web interface, fixed-reader integration, durable RFID event delivery, and Odoo RFID API integration are implemented.

The Odoo 18 Receiving POC has been proven end-to-end on staging using a physical fixed RFID reader:

- Odoo Receipt controls the RFID session
- START and STOP commands are executed against the physical reader
- EPC observations are persisted locally before delivery
- observations are delivered asynchronously to Odoo
- Odoo reconciles EPCs against the expected Receipt
- clean results are applied to native Odoo transfer quantities
- the operator performs the normal Odoo Validate action
- validated stock is posted through normal Odoo stock moves

Dispatch support exists in the architecture and codebase but still requires Door 2 commissioning and end-to-end acceptance testing.

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

Fresh installations intentionally start fail-closed with physical-reader and Odoo contact disabled.

Do not copy live credentials into Git. Configure and commission each target Pi deliberately before enabling external contact.

The legacy `rfid-bridge-worker.service` remains disabled. The final RFID control and delivery workers are separate runtime components.
