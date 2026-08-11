# RFID Bridge Operations

This guide covers routine checks, service state, monitoring, and safe recovery actions.

## Normal operating state during setup

Before hardware and Odoo integrations are approved, the required state is:

```text
rfid-bridge-web.service: active and enabled
rfid-bridge-worker.service: inactive and disabled
nginx.service: active and enabled
READER_BACKEND=fake
SENDER_BACKEND=disabled
ALLOW_PHYSICAL_READER_CONTACT=false
ALLOW_ODOO_CONTACT=false
```

## Daily read-only checks

Check service state:

```bash
systemctl is-active rfid-bridge-web.service
systemctl is-enabled rfid-bridge-web.service
systemctl is-active rfid-bridge-worker.service
systemctl is-enabled rfid-bridge-worker.service
systemctl is-active nginx.service
```

Check the protected web boundary:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1/accounts/login/
```

Expected HTTP results:

```text
dashboard: 302
login: 200
```

## Web monitoring pages

After signing in, use these pages for read-only operational monitoring:

```text
/sessions/
/events/
/delivery-attempts/
```

Use the Sessions page to review active and completed RFID sessions.

Use the RFID events page to review recently received or simulated tag events.

Use the Delivery attempts page to review queue delivery status and failure details.

## Service logs

Read recent web service logs:

```bash
sudo journalctl -u rfid-bridge-web.service --no-pager -n 100
```

Read recent worker logs:

```bash
sudo journalctl -u rfid-bridge-worker.service --no-pager -n 100
```

Read recent Nginx errors:

```bash
sudo tail -n 100 /var/log/nginx/rfid-bridge-error.log
```

Do not paste secret configuration values into support logs or tickets.

## Safe web service recovery

Use this only when the web interface is unavailable or a deployment requires the web process to reload.

Validate the application first:

```bash
sudo -u rfidbridge \
  /opt/rfid_bridge/venv/bin/python \
  /opt/rfid_bridge/app/manage.py check
```

Restart only the web service:

```bash
sudo systemctl restart rfid-bridge-web.service
```

Wait for the Gunicorn socket before testing Nginx:

```bash
for ATTEMPT in 1 2 3 4 5 6 7 8 9 10
do
    if sudo test -S /run/rfid_bridge/web.sock; then
        echo "WEB_SOCKET_READY_ATTEMPT=$ATTEMPT"
        break
    fi

    sleep 1
done
```

Then confirm:

```bash
systemctl is-active rfid-bridge-web.service
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1/accounts/login/
```

A temporary HTTP 502 immediately after restart can occur if Nginx is checked before Gunicorn recreates the socket.
Do not treat that as a persistent failure until the socket readiness check has completed.

Never restart or enable the worker as part of web recovery.

## Worker safety rules

During setup and handover, the worker must remain:

```text
inactive
disabled
```

Do not start or enable the worker unless all of the following are complete:

- the physical reader backend is implemented
- the reader protocol is confirmed
- the Odoo sender is implemented
- the Odoo request and response contracts are confirmed
- offline tests pass
- controlled hardware tests pass
- controlled Odoo tests pass
- explicit deployment approval is given

## Escalation conditions

Stop and investigate before making changes when any of these occur:

- the web service will not remain active
- the Gunicorn socket is missing after the readiness wait
- Nginx validation fails
- Django system checks fail
- the worker becomes active unexpectedly
- reader or Odoo contact is enabled unexpectedly
- queue records show repeated failures
- database migrations are pending unexpectedly

Collect read-only evidence before attempting recovery.

Do not expose passwords, Django secrets, tokens, or Odoo credentials in evidence files.
