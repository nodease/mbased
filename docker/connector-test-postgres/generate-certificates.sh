#!/bin/sh
set -eu

server_dir="${CONNECTOR_TEST_SERVER_TLS_DIR:-/tls/server}"
admin_credential_dir="${CONNECTOR_TEST_ADMIN_CREDENTIAL_DIR:-/tls/admin-credentials}"
connector_credential_dir="${CONNECTOR_TEST_CONNECTOR_CREDENTIAL_DIR:-/tls/connector-credentials}"
public_dir="${CONNECTOR_TEST_PUBLIC_CA_DIR:-/tls/public}"

umask 077
mkdir -p \
    "$server_dir" \
    "$admin_credential_dir" \
    "$connector_credential_dir" \
    "$public_dir"
rm -f \
    "$server_dir/ca.key" \
    "$admin_credential_dir/demo-password" \
    "$connector_credential_dir/postgres-password" \
    "$admin_credential_dir"/.password.* \
    "$connector_credential_dir"/.password.*

work_dir=""
staged_ca_file=""
staged_server_certificate=""
staged_server_key=""
password_file=""
public_ca_file=""
cleanup() {
    if [ -n "$work_dir" ] && [ -d "$work_dir" ]; then
        rm -rf "$work_dir"
    fi
    rm -f \
        "${staged_ca_file:-}" \
        "${staged_server_certificate:-}" \
        "${staged_server_key:-}" \
        "${password_file:-}" \
        "${public_ca_file:-}"
}
trap cleanup EXIT HUP INT TERM

certificate_material_is_valid=false
if [ -f "$server_dir/ca.crt" ] \
    && [ -f "$server_dir/server.key" ] \
    && [ -f "$server_dir/server.crt" ]; then
    certificate_key_hash="$(
        openssl x509 -in "$server_dir/server.crt" -pubkey -noout 2>/dev/null \
            | openssl pkey -pubin -outform DER 2>/dev/null \
            | openssl dgst -sha256 2>/dev/null || true
    )"
    private_key_hash="$(
        openssl pkey -in "$server_dir/server.key" -pubout -outform DER 2>/dev/null \
            | openssl dgst -sha256 2>/dev/null || true
    )"
    if [ -n "$certificate_key_hash" ] \
        && [ "$certificate_key_hash" = "$private_key_hash" ] \
        && [ "$(stat -c '%a' "$server_dir/server.key")" = "600" ] \
        && openssl x509 -checkend 604800 -noout -in "$server_dir/ca.crt" \
            >/dev/null 2>&1 \
        && openssl x509 -checkend 604800 -noout -in "$server_dir/server.crt" \
            >/dev/null 2>&1 \
        && openssl x509 -noout -text -in "$server_dir/ca.crt" \
            | grep -q 'CA:TRUE' \
        && openssl x509 -noout -text -in "$server_dir/server.crt" \
            | grep -q 'CA:FALSE' \
        && openssl verify -CAfile "$server_dir/ca.crt" \
            -verify_hostname connector-test-postgres \
            "$server_dir/server.crt" >/dev/null 2>&1 \
        && openssl verify -CAfile "$server_dir/ca.crt" \
            -verify_hostname localhost \
            "$server_dir/server.crt" >/dev/null 2>&1; then
        certificate_material_is_valid=true
    fi
fi

if [ "$certificate_material_is_valid" != true ]; then
    work_dir="$(mktemp -d "${TMPDIR:-/tmp}/connector-test-generate.XXXXXX")"
    openssl genpkey -algorithm RSA \
        -pkeyopt rsa_keygen_bits:3072 \
        -out "$work_dir/ca.key" >/dev/null 2>&1
    openssl req -x509 -new -sha256 -days 30 \
        -key "$work_dir/ca.key" \
        -subj "/CN=Nodease Local Connector Test CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "subjectKeyIdentifier=hash" \
        -out "$work_dir/ca.crt" >/dev/null 2>&1

    openssl genpkey -algorithm RSA \
        -pkeyopt rsa_keygen_bits:3072 \
        -out "$work_dir/server.key" >/dev/null 2>&1
    openssl req -new -sha256 \
        -key "$work_dir/server.key" \
        -subj "/CN=connector-test-postgres" \
        -out "$work_dir/server.csr" >/dev/null 2>&1
    cat > "$work_dir/server.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:connector-test-postgres,DNS:localhost
EOF
    openssl x509 -req -sha256 -days 14 \
        -in "$work_dir/server.csr" \
        -CA "$work_dir/ca.crt" \
        -CAkey "$work_dir/ca.key" \
        -CAcreateserial \
        -extfile "$work_dir/server.ext" \
        -out "$work_dir/server.crt" >/dev/null 2>&1

    staged_ca_file="$(mktemp "$server_dir/.ca.XXXXXX")"
    staged_server_certificate="$(mktemp "$server_dir/.server-crt.XXXXXX")"
    staged_server_key="$(mktemp "$server_dir/.server-key.XXXXXX")"
    cp "$work_dir/ca.crt" "$staged_ca_file"
    cp "$work_dir/server.crt" "$staged_server_certificate"
    cp "$work_dir/server.key" "$staged_server_key"
    chmod 0644 "$staged_ca_file" "$staged_server_certificate"
    chmod 0600 "$staged_server_key"
    chown postgres:postgres "$staged_server_certificate" "$staged_server_key"
    mv -f "$staged_ca_file" "$server_dir/ca.crt"
    staged_ca_file=""
    mv -f "$staged_server_certificate" "$server_dir/server.crt"
    staged_server_certificate=""
    mv -f "$staged_server_key" "$server_dir/server.key"
    staged_server_key=""
    rm -rf "$work_dir"
    work_dir=""
fi

generate_password_file() {
    destination="$1"
    if [ -s "$destination" ] \
        && grep -Eq '^[0-9a-f]{64}$' "$destination"; then
        chmod 0600 "$destination"
        chown postgres:postgres "$destination"
        return
    fi
    destination_dir="$(dirname "$destination")"
    password_file="$(mktemp "$destination_dir/.password.XXXXXX")"
    openssl rand -hex 32 > "$password_file"
    chmod 0600 "$password_file"
    chown postgres:postgres "$password_file"
    mv -f "$password_file" "$destination"
    password_file=""
}

generate_password_file "$admin_credential_dir/postgres-password"
generate_password_file "$connector_credential_dir/demo-password"
if cmp -s \
    "$admin_credential_dir/postgres-password" \
    "$connector_credential_dir/demo-password"; then
    rm -f "$connector_credential_dir/demo-password"
    generate_password_file "$connector_credential_dir/demo-password"
fi
if cmp -s \
    "$admin_credential_dir/postgres-password" \
    "$connector_credential_dir/demo-password"; then
    echo "connector demo credentials could not be separated" >&2
    exit 1
fi

public_ca_file="$(mktemp "$public_dir/.ca.XXXXXX")"
cp "$server_dir/ca.crt" "$public_ca_file"
chmod 0644 "$public_ca_file"
mv -f "$public_ca_file" "$public_dir/ca.crt"
public_ca_file=""

rm -f "$server_dir/ca.key"
cleanup
trap - EXIT HUP INT TERM
