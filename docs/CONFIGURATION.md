# RFID Bridge Configuration

This guide explains the configuration files, safety controls, and web settings used by the RFID Bridge.

## Configuration files

Non-secret application settings:

```text
/etc/rfid_bridge/app.env
```

Secret values:

```text
/etc/rfid_bridge/secrets.env
```

Both files must be readable by the `rfidbridge` service account.

The secrets file must not be committed to Git or shared in support logs.

## Integration safety controls

The bridge uses two independent controls for each external integration:

1. A backend selector chooses the implementation.
2. An explicit allow flag permits real contact.

The safe setup values are:

```text
READER_BACKEND=fake
SENDER_BACKEND=disabled
ALLOW_PHYSICAL_READER_CONTACT=false
ALLOW_ODOO_CONTACT=false
```

Real reader contact is blocked unless both conditions are true:

- a real reader backend is selected
- `ALLOW_PHYSICAL_READER_CONTACT=true`

Real Odoo contact is blocked unless both conditions are true:

- a real sender backend is selected
- `ALLOW_ODOO_CONTACT=true`

Changing only an allow flag is not sufficient to activate an integration.
Changing only a backend selector is also not sufficient.

Do not enable either integration until its contract has been implemented and tested.

## Reader settings in the web interface

Reader records are configured from the Readers page.

Each reader currently includes:

- unique code
- descriptive name
- operational role
- enabled or disabled state
- notes

Reader roles distinguish receiving and dispatch doors.

Leave a reader disabled until its physical connection details and protocol are implemented.

The current reader record does not yet store network address, port, or protocol settings.
Those fields must be added only after the actual hardware contract is confirmed.

## Operational settings in the web interface

The Operational settings page controls queue behaviour, including:

- delivery batch size
- maximum delivery attempts
- retry delays
- retention periods
- operational notes

Use conservative values during initial testing.

Changes to these settings affect worker behaviour only after the worker is deliberately enabled and started.

## Application environment settings

The application configuration file includes values such as:

```text
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,<hostname>,<ip-address>
READER_BACKEND=fake
SENDER_BACKEND=disabled
ALLOW_PHYSICAL_READER_CONTACT=false
ALLOW_ODOO_CONTACT=false
```

Keep `DJANGO_DEBUG=false` on deployed systems.

`DJANGO_ALLOWED_HOSTS` must contain every hostname or IP address used to open the web interface.

After changing application environment settings, validate the configuration before restarting the web service.

## Nginx server names

The Nginx configuration must include the hostname and IP address used by browser clients.

The installer receives these through:

```text
RFID_BRIDGE_SERVER_NAMES
```

Example:

```text
RFID_BRIDGE_SERVER_NAMES=rfid-bridge-01 192.168.1.116
```

Use the actual hostname and IP address of the target Pi.

## Safe configuration change procedure

Use this sequence whenever configuration is changed:

1. Back up the current file.
2. Make only the required change.
3. Validate Django configuration.
4. Validate Nginx configuration when applicable.
5. Restart only the web service if required.
6. Confirm the worker remains inactive and disabled.

Validate Django:

```bash
sudo -u rfidbridge \
  /opt/rfid_bridge/venv/bin/python \
  /opt/rfid_bridge/app/manage.py check
```

Validate Nginx:

```bash
sudo nginx -t
```

Check service state:

```bash
systemctl is-active rfid-bridge-web.service
systemctl is-enabled rfid-bridge-web.service
systemctl is-active rfid-bridge-worker.service
systemctl is-enabled rfid-bridge-worker.service
```

The required setup state is:

```text
web: active and enabled
worker: inactive and disabled
```
