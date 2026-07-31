#!/bin/sh
set -eu
set -f

raw_ports="${CONNECTOR_EGRESS_ALLOWED_PORTS:-22,5432}"
compact_ports="$(printf '%s' "$raw_ports" | tr -d '[:space:]')"
if ! printf '%s' "$compact_ports" | grep -Eq '^[0-9]+(,[0-9]+)*$'; then
    echo "connector egress port policy is invalid" >&2
    exit 1
fi

ports=""
count=0
for port in $(printf '%s' "$compact_ports" | tr ',' ' '); do
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        echo "connector egress port policy is invalid" >&2
        exit 1
    fi
    case " $ports " in
        *" $port "*)
            echo "connector egress port policy is invalid" >&2
            exit 1
            ;;
    esac
    ports="$ports $port"
    count=$((count + 1))
done
if [ "$count" -gt 16 ]; then
    echo "connector egress port policy is invalid" >&2
    exit 1
fi

umask 077
connector_policy="/run/squid/connector-ports.conf"
runtime_config="/run/squid/squid.conf"
{
    printf 'acl CONNECTOR_ports port%s\n' "$ports"
    printf 'acl CONNECT_ports port%s\n' "$ports"
    printf 'acl Safe_ports port%s\n' "$ports"
} > "${connector_policy}.tmp"
mv "${connector_policy}.tmp" "$connector_policy"
sed 's#/etc/squid/connector-ports.conf#/run/squid/connector-ports.conf#' \
    /etc/squid/squid.conf > "${runtime_config}.tmp"
mv "${runtime_config}.tmp" "$runtime_config"

exec squid "$@"
