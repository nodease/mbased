#!/bin/sh
set -eu

password_file="${CONNECTOR_DEMO_PASSWORD_FILE:-/tls/connector-credentials/demo-password}"
if [ ! -f "$password_file" ] \
    || ! grep -Eq '^[0-9a-f]{64}$' "$password_file"; then
    echo "connector demo role credential is unavailable" >&2
    exit 1
fi

password="$(tr -d '\r\n' < "$password_file")"
escaped_password="$(printf '%s' "$password" | sed "s/'/''/g")"
sql_file="$(mktemp)"
cleanup() {
    rm -f "$sql_file"
}
trap cleanup EXIT HUP INT TERM
chmod 0600 "$sql_file"

{
    printf '%s\n' 'DO $connector_demo$'
    printf '%s\n' 'BEGIN'
    printf '%s\n' "    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'connector_demo_user') THEN"
    printf '%s\n' "        ALTER ROLE connector_demo_user WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD '$escaped_password';"
    printf '%s\n' '    ELSE'
    printf '%s\n' "        CREATE ROLE connector_demo_user WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD '$escaped_password';"
    printf '%s\n' '    END IF;'
    printf '%s\n' 'END'
    printf '%s\n' '$connector_demo$;'
    printf '%s\n' 'REVOKE ALL ON DATABASE connector_demo FROM PUBLIC;'
    printf '%s\n' 'GRANT CONNECT ON DATABASE connector_demo TO connector_demo_user;'
    printf '%s\n' 'REVOKE ALL ON SCHEMA public FROM PUBLIC;'
    printf '%s\n' 'REVOKE ALL ON SCHEMA public FROM connector_demo_user;'
    printf '%s\n' "ALTER ROLE connector_demo_user IN DATABASE connector_demo SET default_transaction_read_only = 'on';"
    printf '%s\n' "ALTER ROLE connector_demo_user IN DATABASE connector_demo SET statement_timeout = '5s';"
} > "$sql_file"

psql \
    --set=ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --file "$sql_file" \
    >/dev/null
