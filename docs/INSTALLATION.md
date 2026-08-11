# RFID Bridge Installation

This guide installs the complete RFID Bridge on a separate Raspberry Pi.

## Supported baseline

- Raspberry Pi 4 or newer
- Raspberry Pi OS Lite 64-bit
- Debian 13 / arm64
- Wired network recommended
- Internet access required during installation
- Administrator account with sudo access

## Safety defaults

A new installation starts in safe setup mode:

- `READER_BACKEND=fake`
- `SENDER_BACKEND=disabled`
- `ALLOW_PHYSICAL_READER_CONTACT=false`
- `ALLOW_ODOO_CONTACT=false`
- worker service disabled and inactive

The installer does not contact RFID hardware or Odoo.

## 1. Prepare the Pi

Set the hostname and timezone before installation.

Recommended hostname:

```text
rfid-bridge-01
```

Recommended timezone:

```text
Africa/Johannesburg
```

Confirm the Pi identity and network address:

```bash
hostname
hostname -I
timedatectl
```

## 2. Copy the repository

Copy or clone the repository onto the target Pi.

A temporary checkout location may be used, for example:

```text
/home/<administrator>/rfid_bridge
```

Change into the repository root:

```bash
cd /home/<administrator>/rfid_bridge
```

## 3. Generate secrets

Generate a Django secret:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Generate a PostgreSQL password:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Store both values securely. Do not commit them to Git.

## 4. Run the complete installer

Replace the example hostname, IP address and secrets:

```bash
sudo \
  RFID_BRIDGE_DB_PASSWORD='REPLACE_DATABASE_PASSWORD' \
  RFID_BRIDGE_DJANGO_SECRET='REPLACE_DJANGO_SECRET' \
  RFID_BRIDGE_ALLOWED_HOSTS='127.0.0.1,localhost,rfid-bridge-01,192.168.1.116' \
  RFID_BRIDGE_SERVER_NAMES='rfid-bridge-01 192.168.1.116' \
  bash deploy/install/install_rfid_bridge.sh
```

Use the actual hostname and IP address of the target Pi.

## 5. Create the administrator

Create the first web administrator account:

```bash
sudo -u rfidbridge \
  /opt/rfid_bridge/venv/bin/python \
  /opt/rfid_bridge/app/manage.py \
  createsuperuser
```

Store the username securely.

## 6. Verify the installation

Run the full read-only installation verifier:

```bash
sudo bash \
  /opt/rfid_bridge/app/deploy/install/05_verify_installation.sh
```

The expected safe state is:

```text
Web service: active and enabled
Nginx: active and enabled
Worker service: inactive and disabled
Reader contact: blocked
Odoo contact: blocked
```

## 7. Open the web interface

Open either the hostname or IP address in a browser:

```text
http://<pi-hostname>/
http://<pi-ip-address>/
```

Sign in with the Django administrator account created above.

## 8. Configure local settings

From the web interface:

1. Add the receiving reader.
2. Add the dispatch reader.
3. Leave both readers disabled until hardware details are confirmed.
4. Review operational retry and retention settings.
5. Confirm the Sessions page loads.
6. Confirm the RFID events page loads.
7. Confirm the Delivery attempts page loads.

## 9. Keep integrations disabled

Do not change these safety settings until the hardware and Odoo contracts are approved:

```text
READER_BACKEND=fake
SENDER_BACKEND=disabled
ALLOW_PHYSICAL_READER_CONTACT=false
ALLOW_ODOO_CONTACT=false
```

Do not enable or start `rfid-bridge-worker.service` during setup.

## 10. Required hardware information

Before implementing or enabling the physical reader backend, confirm:

- reader manufacturer and model
- TCP, serial or USB protocol
- reader IP address and port
- message format
- EPC field format
- heartbeat behaviour
- reconnect behaviour
- duplicate event identifier
- antenna and door mapping

## 11. Required Odoo information

Before implementing or enabling the Odoo sender, confirm:

- Odoo.sh hostname
- database name
- endpoint path
- authentication method
- request schema
- response schema
- receipt session contract
- dispatch session contract
- idempotency requirements
- duplicate-event behaviour
- timeout and retry rules

## 12. Service commands

Check the web service:

```bash
systemctl status rfid-bridge-web.service
```

Check the worker service:

```bash
systemctl status rfid-bridge-worker.service
```

During setup, the worker must remain inactive and disabled.

## 13. Configuration locations

```text
Application:          /opt/rfid_bridge/app
Python environment:   /opt/rfid_bridge/venv
Application config:   /etc/rfid_bridge/app.env
Secrets:              /etc/rfid_bridge/secrets.env
Static files:         /var/lib/rfid_bridge/staticfiles
State and backups:    /var/lib/rfid_bridge
Logs:                 /var/log/rfid_bridge
Runtime socket:       /run/rfid_bridge/web.sock
```

## 14. Installation sequence

The complete installer runs these scripts in order:

```text
01_prepare_system.sh
02_configure_postgresql.sh
03_install_application.sh
04_install_services.sh
05_verify_installation.sh
```

The complete orchestrator is:

```text
deploy/install/install_rfid_bridge.sh
```
