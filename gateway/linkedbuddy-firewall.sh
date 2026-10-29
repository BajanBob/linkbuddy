#!/bin/sh
# Linked Buddy forwarding rules with a kill switch.
# VPN modes (NordVPN, Onion over VPN, imported settings): TV traffic may only leave through the
# VPN tunnel (tun0). Tor mode (optional): TV web traffic is handed to Tor and nothing else gets out.
# Either way, if the VPN or Tor is down the TV gets no internet instead of using your normal connection.
set -eu
CONF=/etc/linkedbuddy/config.json
val() { python3 -c "import json,sys;print(json.load(open('$CONF')).get(sys.argv[1],sys.argv[2]))" "$1" "$2"; }
TV_IF=$(val tv_if eth0)
MODE=$(val mode cable)
GW_IP=$(val gateway_ip 192.168.77.1)
VPN_MODE=$(val vpn_mode nord)
VPN_IF=tun0

clear_rules() {
  iptables -D FORWARD -j LB_FWD 2>/dev/null || true
  iptables -F LB_FWD 2>/dev/null || true
  iptables -X LB_FWD 2>/dev/null || true
  iptables -t nat -D PREROUTING -j LB_NAT 2>/dev/null || true
  iptables -t nat -F LB_NAT 2>/dev/null || true
  iptables -t nat -X LB_NAT 2>/dev/null || true
  iptables -t nat -D POSTROUTING -o "$VPN_IF" -j MASQUERADE 2>/dev/null || true
  iptables -t mangle -D FORWARD -o "$VPN_IF" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
}

case "${1:-up}" in
  up)
    clear_rules
    iptables -N LB_FWD
    iptables -I FORWARD 1 -j LB_FWD
    if [ "$VPN_MODE" = tor ]; then
      # Tor mode: name lookups go to Tor's DNS, new TCP connections to Tor's transparent port.
      iptables -t nat -N LB_NAT
      iptables -t nat -I PREROUTING 1 -j LB_NAT
      iptables -t nat -A LB_NAT -i "$TV_IF" -p udp --dport 53 -j REDIRECT --to-ports 5353
      iptables -t nat -A LB_NAT -i "$TV_IF" -p tcp --dport 53 -j REDIRECT --to-ports 5353
      iptables -t nat -A LB_NAT -i "$TV_IF" -d "$GW_IP" -j RETURN       # control page and DHCP stay local
      iptables -t nat -A LB_NAT -i "$TV_IF" -p tcp --syn -j REDIRECT --to-ports 9040
      # nothing from the TV is forwarded directly (kill switch; UDP/QUIC falls back to TCP)
      if [ "$MODE" = cable ]; then
        iptables -A LB_FWD -i "$TV_IF" -j DROP
      else
        iptables -A LB_FWD -i "$TV_IF" -o "$TV_IF" -j DROP
      fi
    else
      # replies coming back from the internet through the tunnel
      iptables -A LB_FWD -i "$VPN_IF" -o "$TV_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
      # TV out through the tunnel only
      iptables -A LB_FWD -i "$TV_IF" -o "$VPN_IF" -j ACCEPT
      if [ "$MODE" = cable ]; then
        iptables -A LB_FWD -i "$TV_IF" -j DROP
      else
        # LAN mode: the TV shares the home network, so block forwarded traffic that would
        # leave by the normal internet route instead of the tunnel
        iptables -A LB_FWD -i "$TV_IF" -o "$TV_IF" -j DROP
      fi
      iptables -t nat -A POSTROUTING -o "$VPN_IF" -j MASQUERADE
      # avoid stalls on streaming sites over the tunnel
      iptables -t mangle -A FORWARD -o "$VPN_IF" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    fi
    ;;
  down)
    clear_rules
    ;;
  *) echo "usage: $0 up|down"; exit 2 ;;
esac
