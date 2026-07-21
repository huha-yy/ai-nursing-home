#!/bin/sh
set -eu

DENY_CONF="/etc/dnsmasq.d/dato-llm-deny.conf"
: > "$DENY_CONF"

gen_deny() {
    # Base denylist from mounted file.
    if [ -f /etc/dato/llm_denylist.txt ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            line="$(echo "$line" | sed 's/[[:space:]]*#.*//' | tr -d '[:space:]')"
            [ -z "$line" ] && continue
            # Strip wildcard prefix for dnsmasq address syntax.
            line_no_star="$(echo "$line" | sed 's/^\*\.//')"
            echo "address=/${line}/0.0.0.0" >> "$DENY_CONF"
            if [ "$line_no_star" != "$line" ]; then
                echo "address=/${line_no_star}/0.0.0.0" >> "$DENY_CONF"
            fi
        done < /etc/dato/llm_denylist.txt
    fi

    # Extra deny entries from env.
    if [ -n "${DL_EGRESS_DNS_EXTRA_DENY:-}" ]; then
        echo "$DL_EGRESS_DNS_EXTRA_DENY" | tr ',' '\n' | while IFS= read -r entry; do
            entry="$(echo "$entry" | tr -d '[:space:]')"
            [ -z "$entry" ] && continue
            echo "address=/${entry}/0.0.0.0" >> "$DENY_CONF"
        done
    fi
}

# If disabled, skip denylist generation.
if [ "${DL_EGRESS_DNS_DISABLE:-0}" = "1" ]; then
    echo "WARNING: dl-egress-dns disabled (DL_EGRESS_DNS_DISABLE=1)" >&2
else
    gen_deny
fi

exec dnsmasq --no-daemon \
    --conf-dir=/etc/dnsmasq.d \
    --log-queries \
    --log-facility=- \
    --server="${DL_EGRESS_DNS_UPSTREAM:-1.1.1.1}" \
    --server="${DL_EGRESS_DNS_UPSTREAM2:-8.8.8.8}"
