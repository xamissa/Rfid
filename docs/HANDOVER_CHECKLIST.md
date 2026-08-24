# RFID Bridge Handover Checklist

Use this checklist before handing the RFID Bridge package to another installer or developer.

## Proven current state

The commissioned Receiving POC has demonstrated:

- physical fixed-reader START and STOP control
- durable local EPC persistence
- asynchronous RFID event delivery to Odoo
- Odoo-owned RFID session lifecycle
- EPC-to-product registry resolution
- clean expected/matched reconciliation
- automatic application of clean RFID results to native transfer quantities
- normal operator-controlled Odoo Validate
- completed normal Odoo stock movement into the destination location
- reader returning to idle after the session

Also confirm:

- Django application checks pass
- Nginx configuration validates
- web service is active and enabled
- legacy worker service is inactive and disabled
- dashboard requires authentication
- login page loads successfully
- reader configuration pages load
- operational settings page loads
- session monitoring page loads
- RFID event monitoring page loads
- delivery attempt monitoring page loads
- Git working tree is clean

## Required safety state

The handover package must retain:

```text
READER_BACKEND=fake
SENDER_BACKEND=disabled
ALLOW_PHYSICAL_READER_CONTACT=false
ALLOW_ODOO_CONTACT=false
rfid-bridge-worker.service: inactive and disabled
```

No real reader or Odoo credentials may be included in the repository.

## Package contents

Confirm the handover package includes:

- complete Git repository
- deployment scripts
- systemd unit files
- Nginx configuration
- example environment files
- installation guide
- configuration guide
- operations guide
- hardware testing guide
- Odoo testing guide
- troubleshooting guide
- this handover checklist

Do not include:

- `/etc/rfid_bridge/secrets.env`
- database passwords
- Django secret keys
- Odoo credentials or tokens
- reader credentials
- private SSH keys
- production evidence containing secrets

## Recipient responsibilities

The receiving installer or developer must:

1. Install on the correct target Pi and network.
2. Generate new local secrets.
3. Keep all integrations disabled during initial installation.
4. Create a new administrator account.
5. Run the complete installation verifier.
6. Confirm the web interface is protected by login.
7. Configure the target reader and door identity.
8. Configure the target Odoo URL, gateway/reader codes, and dedicated API credential.
9. Follow staged hardware commissioning.
10. Follow staged Odoo commissioning.
11. Verify the final control and delivery workers before enabling persistent execution.

## Final handover evidence

Before packaging, capture:

- current Git commit
- clean Git status
- Django system check result
- migration state
- Nginx validation result
- web service active and enabled state
- worker inactive and disabled state
- protected dashboard HTTP result
- login page HTTP result
- package checksum

## Sign-off criteria

The package is ready for handover only when:

- all documentation is committed
- the repository is clean
- the installer scripts pass syntax validation
- the application passes Django checks
- the web interface is live and protected
- the worker is inactive and disabled
- no real integration credentials are present
- the package checksum is recorded
- known outstanding hardware and Odoo work is stated clearly

The repository contains the implementation that has passed the Receiving POC on the commissioned staging environment.

This does not mean an arbitrary new Pi, reader, Odoo database, or Dispatch door is automatically commissioned. Each target deployment must still be configured and verified before external integrations are enabled.
