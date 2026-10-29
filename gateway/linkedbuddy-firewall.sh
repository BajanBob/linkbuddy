#!/bin/sh
# Linked Buddy forwarding rules with a kill switch.
# TV traffic may only leave through the VPN tunnel (tun0). If the tunnel is down, the TV gets no
# internet instead of going out over your normal connection.
set -eu
CONF=/etc/linkedbuddy/config.json
TV_IF=$(python3 -c "import json;print(json.load(open('$CONF'))['tv_if'])")
MODE=$(python3 -c "import json;print(json.load(open('$CONF'))['mode'])")
VPN_IF=tun0

clear_rules() {
  iptables -D FORWARD -j LB_FWD 2>/dev/null || true
  iptables -F LB_FWD 2>/dev/null || true
  iptables -X LB_FWD 2>/dev/null || true
  iptables -t nat -D POSTROUTING -o "$VPN_IF" -j MASQUERADE 2>/dev/null || true
  iptables -t mangle -D FORWARD -o "$VPN_IF" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
}

case "${1:-up}" in
  up)
    clear_rules
    iptables -N LB_FWD
    iptables -I FORWARD 1 -j LB_FWD
    # replies coming back from the internet through the tunnel
    iptables -A LB_FWD -i "$VPN_IF" -o "$TV_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    # TV out through the tunnel only
    iptables -A LB_FWD -i "$TV_IF" -o "$VPN_IF" -j ACCEPT
    if [ "$MODE" = cable ]; then
      # anything else from the TV port is dropped (kill switch)
      iptables -A LB_FWD -i "$TV_IF" -j DROP
    else
      # LAN mode: the TV shares the home network, so only block forwarded traffic that would
      # leave by the normal internet route instead of the tunnel
      iptables -A LB_FWD -i "$TV_IF" -o "$TV_IF" -j DROP
    fi
    iptables -t nat -A POSTROUTING -o "$VPN_IF" -j MASQUERADE
    # avoid stalls on streaming sites over the tunnel
    iptables -t mangle -A FORWARD -o "$VPN_IF" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    ;;
  down)
    clear_rules
    ;;
  *) echo "usage: $0 up|down"; exit 2 ;;
esac
