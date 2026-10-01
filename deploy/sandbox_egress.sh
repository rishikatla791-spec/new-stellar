#!/usr/bin/env bash
# Keep sandbox containers off the host and off private networks.
#
#   sudo deploy/sandbox_egress.sh            apply the rules (safe to repeat)
#   sudo deploy/sandbox_egress.sh --remove   take them out again
#   sudo deploy/sandbox_egress.sh --status   show what is installed
#
# Code the model writes runs in Stellar's sandbox containers, and those
# containers can reach the internet by design. Without these rules they can
# also reach everything else the host can: services on the host itself
# (Redis, SSH, the app), other machines on the private network, and the
# cloud metadata service that hands out the server's credentials.
#
# Stellar names every sandbox bridge stl-<something> (stl-u<user id>, and
# stl-shared for the fallback network), so the rules match the interface
# with "stl-+" and touch no other container on the machine.
#
# Two chains:
#   STELLAR-SANDBOX-OUT  hooked into DOCKER-USER: traffic a sandbox sends
#                        through the host to another machine. Private,
#                        loopback, link-local and other non-public ranges
#                        are refused; DNS is allowed wherever the resolver
#                        lives, since cloud and home resolvers are often on
#                        private addresses.
#   STELLAR-SANDBOX-HOST hooked into INPUT: traffic a sandbox sends to the
#                        host itself (the bridge gateway or any host
#                        address). All refused, except replies to
#                        connections the host opened (nginx reaching a
#                        deployed app).
#
# The rules live in the kernel and vanish on reboot, which is why
# deploy/stellar-sandbox-egress.service runs this script after Docker
# starts. IPv6 is not covered because Stellar's networks are IPv4-only.
set -euo pipefail

IPT="${IPTABLES:-iptables}"
OUT=STELLAR-SANDBOX-OUT
HOST=STELLAR-SANDBOX-HOST
IFACE='stl-+'
BLOCKED=(
  0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16
  172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15
  198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4
)

remove_rules() {
  while "$IPT" -D DOCKER-USER -i "$IFACE" -j "$OUT" 2>/dev/null; do :; done
  while "$IPT" -D INPUT -i "$IFACE" -j "$HOST" 2>/dev/null; do :; done
  for chain in "$OUT" "$HOST"; do
    "$IPT" -F "$chain" 2>/dev/null || true
    "$IPT" -X "$chain" 2>/dev/null || true
  done
}

apply_rules() {
  if ! "$IPT" -L DOCKER-USER -n >/dev/null 2>&1; then
    echo "DOCKER-USER chain not found: is Docker running with iptables enabled?" >&2
    exit 1
  fi

  "$IPT" -N "$OUT" 2>/dev/null || "$IPT" -F "$OUT"
  "$IPT" -A "$OUT" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  "$IPT" -A "$OUT" -p udp --dport 53 -j RETURN
  "$IPT" -A "$OUT" -p tcp --dport 53 -j RETURN
  for net in "${BLOCKED[@]}"; do
    "$IPT" -A "$OUT" -d "$net" -j REJECT
  done
  "$IPT" -A "$OUT" -j RETURN

  "$IPT" -N "$HOST" 2>/dev/null || "$IPT" -F "$HOST"
  "$IPT" -A "$HOST" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  "$IPT" -A "$HOST" -j REJECT

  "$IPT" -C DOCKER-USER -i "$IFACE" -j "$OUT" 2>/dev/null \
    || "$IPT" -I DOCKER-USER 1 -i "$IFACE" -j "$OUT"
  "$IPT" -C INPUT -i "$IFACE" -j "$HOST" 2>/dev/null \
    || "$IPT" -I INPUT 1 -i "$IFACE" -j "$HOST"
  echo "sandbox egress rules applied (interfaces $IFACE)"
}

case "${1:-}" in
  --remove) remove_rules; echo "sandbox egress rules removed" ;;
  --status)
    "$IPT" -S "$OUT" 2>/dev/null || echo "$OUT: not installed"
    "$IPT" -S "$HOST" 2>/dev/null || echo "$HOST: not installed"
    "$IPT" -S DOCKER-USER | grep -- "$OUT" || true
    "$IPT" -S INPUT | grep -- "$HOST" || true
    ;;
  "") apply_rules ;;
  *) echo "usage: $0 [--remove|--status]" >&2; exit 2 ;;
esac
