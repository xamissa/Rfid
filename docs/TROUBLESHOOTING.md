# RFID Bridge Troubleshooting

This guide provides safe, evidence-first troubleshooting steps for the RFID Bridge.

## Troubleshooting rules

Always use this order:

1. Inspect the current state.
2. Capture read-only evidence.
3. Identify the exact failure.
4. Change only what is proven necessary.
5. Validate the change.
6. Confirm the worker safety state.

Do not restart services repeatedly without evidence.

Do not enable the worker as part of troubleshooting.

Do not expose passwords, tokens, Django secrets, or Odoo credentials in logs.

## Basic health checks

Run these read-only checks first:

```bash
systemctl is-active rfid-bridge-web.service
systemctl is-enabled rfid-bridge-web.service
systemctl is-active rfid-bridge-worker.service
systemctl is-enabled rfid-bridge-worker.service
systemctl is-active nginx.service
sudo nginx -t
sudo -u rfidbridge /opt/rfid_bridge/venv/bin/python /opt/rfid_bridge/app/manage.py check
```

The safe setup state is:

```text
web: active and enabled
worker: inactive and disabled
nginx: active
Django check: no issues
Nginx check: successful
```

## HTTP 502 after web restart

A temporary 502 can occur when Nginx checks the Gunicorn socket before Gunicorn has recreated it.

First inspect the current state:

```bash
systemctl status rfid-bridge-web.service --no-pager --lines=25
sudo journalctl -u rfid-bridge-web.service --no-pager -n 50
sudo tail -n 30 /var/log/nginx/rfid-bridge-error.log
```

Then wait for the socket:

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

Retest the web boundary:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1/accounts/login/
```

Expected results are `302` for the protected dashboard and `200` for the login page.

If the socket is still missing after 10 seconds, treat the condition as a real failure and inspect the Gunicorn journal before changing anything.

## Login page unavailable

Confirm the login route through Nginx:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1/accounts/login/
```

Expected result:

```text
200
```

If the login page fails, inspect the web journal and confirm Django checks pass before restarting anything.

## Configuration errors

When Django reports invalid settings:

1. Inspect `/etc/rfid_bridge/app.env` without printing secret values.
2. Confirm the expected variable names exist.
3. Confirm boolean values use supported forms.
4. Run `manage.py check` as the `rfidbridge` service account.
5. Restart only the web service after validation succeeds.

Never copy `/etc/rfid_bridge/secrets.env` into logs or support messages.

## Worker active unexpectedly

During setup, an active worker is a safety failure.

Capture its state and recent journal first:

```bash
systemctl status rfid-bridge-worker.service --no-pager --lines=25
sudo journalctl -u rfid-bridge-worker.service --no-pager -n 50
```

Do not assume external contact occurred. Verify the backend and allow settings before drawing conclusions.

The required setup state remains:

```text
rfid-bridge-worker.service: inactive and disabled
```

## Database or migration errors

Check migration state without creating a test database:

```bash
sudo -u rfidbridge \
  /opt/rfid_bridge/venv/bin/python \
  /opt/rfid_bridge/app/manage.py showmigrations
```

Check for model changes that are missing migrations:

```bash
sudo -u rfidbridge \
  /opt/rfid_bridge/venv/bin/python \
  /opt/rfid_bridge/app/manage.py makemigrations --check --dry-run
```

Do not grant `CREATEDB`, superuser, or role-management privileges to the application database role.

Do not run Django tests that require automatic test-database creation with the production application role.

## Permission errors

Inspect ownership and permissions before changing them:

```bash
sudo stat -c "PATH=%n OWNER=%U GROUP=%G MODE=%a TYPE=%F" \
  /opt/rfid_bridge/app \
  /opt/rfid_bridge/venv \
  /etc/rfid_bridge \
  /var/lib/rfid_bridge \
  /var/log/rfid_bridge \
  /run/rfid_bridge
```

Do not recursively change ownership or permissions unless the exact affected path is proven.

Avoid running `compileall` as the administrator account because protected cache ownership can cause permission failures.

Use Django checks and read-only Python syntax validation instead.
