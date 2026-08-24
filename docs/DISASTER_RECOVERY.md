# RFID Bridge Disaster Recovery

## Recovery model

The RFID Bridge has two recovery paths:

1. **Fresh rebuild** from the Git repository.
2. **Exact recovery** using Git plus a protected database/config backup.

Git contains application source, migrations, installers, service definitions
and recovery scripts. Live secrets and database backups must never be committed.

## Fresh rebuild safety

A fresh installation is deliberately fail-closed.

It installs:

- RFID Bridge web application
- PostgreSQL
- Nginx
- `rfid-bridge-web.service`
- `rfid-bridge-worker.service`
- `rfid-final-worker.service`
- `rfid-final-delivery.service`

The web service may run, but all RFID workers remain disabled until commissioning.

Fresh configuration contains placeholder final-runtime values. Physical reader
and Odoo contact must not be enabled until the target environment is verified.

## Exact backup

Run:

    cd /opt/rfid_bridge/app
    sudo bash deploy/recovery/create_exact_backup.sh

Backups are created under:

    /var/lib/rfid_bridge/backups/

Each recovery set contains:

- `rfid_bridge.dump`
- `config/app.env`
- `config/secrets.env`
- `MANIFEST.txt`
- `SHA256SUMS`

A successful backup reports:

    RFID_EXACT_DR_BACKUP=PASS

The database and protected config are one matched recovery set. Do not mix
files from different backups.

The canonical application runtime reads `app.env` and `secrets.env`.
A historical `database.env` file may exist on older commissioned Pis, but it is
not required by the current application runtime or disaster-recovery contract.

`secrets.env` contains the encryption material required for encrypted Odoo
credentials in the database.

## Backup security

Exact backups contain sensitive information including credentials, API tokens,
encryption keys and operational history.

Never:

- commit them to Git
- push them to GitHub
- place them in public tickets
- share them through an unapproved channel

A backup kept only on the same Pi does not protect against SD-card or Pi loss.
Copy approved recovery sets to secure off-device storage.

## Exact restore

A fresh replacement Pi should first be built from the approved Git revision.

Keep all RFID workers disabled.

Copy the protected recovery set to the replacement Pi.

Exact restore is destructive to the current local RFID database and requires
explicit confirmation:

    cd /opt/rfid_bridge/app

    sudo CONFIRM_EXACT_RESTORE=YES \
      bash deploy/recovery/restore_exact_backup.sh \
      /var/lib/rfid_bridge/backups/exact_HOST_TIMESTAMP

The restore validates the manifest and SHA-256 checksums before restoring.

A successful restore reports:

    RFID_EXACT_DR_RESTORE=PASS

Workers remain stopped after restore.

## Mandatory post-restore checks

Do not blindly start RFID workers.

Verify:

- correct Git revision
- correct Pi/network
- correct reader IP and port
- correct reader code
- correct gateway code
- correct Odoo URL/environment
- valid Odoo bearer credential
- TLS verification
- reader connectivity
- no unintended active or stale RFID session
- Django checks
- web interface

The legacy `rfid-bridge-worker.service` must remain disabled.

Start final workers only after commissioning checks pass.

## Odoo safety

RFID confirms quantities; Odoo remains stock authority.

Normal flow:

    RFID scan
      -> reconciliation
      -> Apply RFID-confirmed quantities
      -> operator uses normal Odoo Validate
      -> native Odoo stock movement

The bridge must never automatically validate an Odoo transfer or directly
adjust `stock.quant`.

## Recovery version

`MANIFEST.txt` records the Git commit active when an exact backup was created.

For disaster recovery, restore that application revision first where possible,
verify the recovered system, then perform any separately approved upgrade.
