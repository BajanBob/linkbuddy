#!/usr/bin/env bash
# Linked Buddy Gateway installer
# Turns a Raspberry Pi (or any Debian/Ubuntu Linux PC) into a NordVPN box for your TV.
#
#   sudo bash install.sh
#
# Two ways to connect the TV:
#   cable : TV plugs into the Pi's Ethernet port. The Pi joins your home Wi-Fi and gives the TV
#           its address automatically. Nothing to type on the TV. (recommended)
#   lan   : Pi and TV both stay on your home network. On the TV you type the Pi's address as
#           the Gateway and DNS in the manual IP settings.
set -euo pipefail

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mXX %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run with sudo:  sudo bash install.sh"
command -v apt-get >/dev/null || die "This installer needs Raspberry Pi OS, Debian or Ubuntu (apt)."

HERE="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/linkedbuddy
ETC=/etc/linkedbuddy
mkdir -p "$APP" "$ETC"
chmod 700 "$ETC"

say "Installing OpenVPN, dnsmasq and Python"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq openvpn dnsmasq iptables python3 curl iproute2 >/dev/null
# dnsmasq is configured below; keep it from fighting with other DNS on first start
systemctl stop dnsmasq 2>/dev/null || true

# ---------------------------------------------------------------- choose the setup
UPLINK="$(ip route show default 0.0.0.0/0 | awk '/default/ {print $5; exit}')"
[ -n "$UPLINK" ] || die "No internet connection found. Connect the Pi to your home network first."
HOME_IP=$(ip -4 -o addr show dev "$UPLINK" | awk '{print $4}' | cut -d/ -f1 | head -n1)
echo
echo "Your Pi reaches the internet through: $UPLINK"
echo
echo "How will the TV connect?"
echo "  1) Cable into the Pi (recommended: plug the TV's network cable into the Pi)"
echo "  2) Through my home network (I will type the Pi's address into the TV)"
read -rp "Choose 1 or 2 [1]: " CH
MODE=cable; [ "${CH:-1}" = "2" ] && MODE=lan

TV_IF=""
if [ "$MODE" = cable ]; then
  CANDIDATES=$(ls /sys/class/net | grep -E '^(eth|en)' | grep -v "^${UPLINK}$" || true)
  if [ -z "$CANDIDATES" ]; then
    warn "Cable mode needs a free Ethernet port for the TV, but $UPLINK is the only one and it is your internet."
    warn "Either connect the Pi to your home Wi-Fi (then run this again), add a USB Ethernet adapter, or choose option 2."
    exit 1
  fi
  DEFAULT_IF=$(echo "$CANDIDATES" | head -n1)
  read -rp "Ethernet port for the TV [$DEFAULT_IF]: " TV_IF
  TV_IF=${TV_IF:-$DEFAULT_IF}
  [ -e "/sys/class/net/$TV_IF" ] || die "There is no network port called $TV_IF"
  TV_NET=192.168.77
  GW_IP=$TV_NET.1
else
  TV_IF=$UPLINK
  GW_IP=$(ip -4 -o addr show dev "$UPLINK" | awk '{print $4}' | cut -d/ -f1 | head -n1)
  [ -n "$GW_IP" ] || die "Could not read this Pi's address on $UPLINK"
  warn "Tip: give this Pi a fixed address in your router (DHCP reservation for $GW_IP), so the TV settings never change."
fi

# ---------------------------------------------------------------- NordVPN login
echo
echo "NordVPN service credentials (NOT your email login):"
echo "  my.nordaccount.com > NordVPN > Manual setup > Service credentials"
read -rp "  Service username: " NUSER
read -rsp "  Service password: " NPASS; echo
[ -n "$NUSER" ] && [ -n "$NPASS" ] || die "Both service username and password are needed."
umask 077
printf '%s\n%s\n' "$NUSER" "$NPASS" > "$ETC/nord.auth"

echo
read -rsp "Choose a password for the Linked Buddy control page: " DPASS; echo
[ ${#DPASS} -ge 6 ] || die "Use at least 6 characters."
SALT=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
HASH=$(printf '%s%s' "$SALT" "$DPASS" | sha256sum | cut -d' ' -f1)

read -rp "Country to appear in (for example: United States, United Kingdom, Canada) [United States]: " COUNTRY
COUNTRY=${COUNTRY:-United States}

echo
echo "Optional extras: Tor and NordVPN Onion over VPN (open .onion sites from the TV; slow, and most"
echo "streaming services block Tor). You can switch them on or off later on the control page."
read -rp "Add Tor and Onion over VPN? [y/N]: " XT
TOR_ONION=false; case "${XT:-n}" in [yY]*) TOR_ONION=true;; esac

cat > "$ETC/config.json" <<EOF
{
  "mode": "$MODE",
  "uplink": "$UPLINK",
  "tv_if": "$TV_IF",
  "gateway_ip": "$GW_IP",
  "home_ip": "$HOME_IP",
  "country": "$COUNTRY",
  "port": 8080,
  "salt": "$SALT",
  "hash": "$HASH",
  "vpn_mode": "nord",
  "tor_onion": $TOR_ONION
}
EOF
chmod 600 "$ETC/config.json"

# ---------------------------------------------------------------- files
say "Installing Linked Buddy"
install -m 755 "$HERE/linkedbuddy-gateway.py" "$APP/linkedbuddy-gateway.py"
install -m 755 "$HERE/linkedbuddy-firewall.sh" "$APP/linkedbuddy-firewall.sh"

# ---------------------------------------------------------------- network
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-linkedbuddy.conf
sysctl -q -p /etc/sysctl.d/99-linkedbuddy.conf

NORD_DNS1=103.86.96.100
NORD_DNS2=103.86.99.100
if [ "$MODE" = cable ]; then
  say "Setting up the TV port $TV_IF ($GW_IP)"
  if command -v nmcli >/dev/null && systemctl is-active --quiet NetworkManager; then
    nmcli con delete linkedbuddy-tv >/dev/null 2>&1 || true
    nmcli con add type ethernet ifname "$TV_IF" con-name linkedbuddy-tv \
      ipv4.method manual ipv4.addresses "$GW_IP/24" ipv4.never-default yes ipv6.method disabled connection.autoconnect yes >/dev/null
    nmcli con up linkedbuddy-tv >/dev/null 2>&1 || warn "The TV port will come up when a cable is plugged in."
  else
    cat > /etc/systemd/system/linkedbuddy-tvport.service <<EOF
[Unit]
Description=Linked Buddy TV port address
Before=dnsmasq.service
After=network-pre.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'ip link set $TV_IF up; ip addr replace $GW_IP/24 dev $TV_IF'
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now linkedbuddy-tvport.service
  fi
  cat > /etc/dnsmasq.d/linkedbuddy.conf <<EOF
# Linked Buddy: hand the TV an address, and answer its DNS through NordVPN's DNS servers
interface=$TV_IF
bind-interfaces
dhcp-range=$TV_NET.50,$TV_NET.150,255.255.255.0,12h
dhcp-option=option:router,$GW_IP
dhcp-option=option:dns-server,$GW_IP
no-resolv
server=$NORD_DNS1
server=$NORD_DNS2
EOF
else
  cat > /etc/dnsmasq.d/linkedbuddy.conf <<EOF
# Linked Buddy: answer the TV's DNS through NordVPN's DNS servers
listen-address=$GW_IP
bind-interfaces
no-dhcp-interface=$UPLINK
no-resolv
server=$NORD_DNS1
server=$NORD_DNS2
EOF
fi

# ---------------------------------------------------------------- services
cat > /etc/systemd/system/linkedbuddy-firewall.service <<EOF
[Unit]
Description=Linked Buddy forwarding and kill switch
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$APP/linkedbuddy-firewall.sh up
ExecStop=$APP/linkedbuddy-firewall.sh down
[Install]
WantedBy=multi-user.target
EOF

if [ "$TOR_ONION" = true ]; then
  say "Installing Tor (optional extra)"
  apt-get install -y -qq tor >/dev/null
  systemctl disable --now tor >/dev/null 2>&1 || true   # Linked Buddy runs its own Tor service
  install -d -o debian-tor -g debian-tor -m 700 /var/lib/linkedbuddy-tor
fi
cat > /etc/systemd/system/linkedbuddy-tor.service <<EOF
[Unit]
Description=Linked Buddy Tor for the TV (optional)
After=network-online.target linkedbuddy-firewall.service
Wants=network-online.target
[Service]
ExecStart=/usr/bin/tor -f $ETC/torrc --RunAsDaemon 0
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/linkedbuddy-gateway.service <<EOF
[Unit]
Description=Linked Buddy control page
After=network-online.target linkedbuddy-firewall.service
Wants=network-online.target
[Service]
ExecStart=/usr/bin/python3 $APP/linkedbuddy-gateway.py
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now linkedbuddy-firewall.service
systemctl enable dnsmasq >/dev/null 2>&1
systemctl restart dnsmasq
systemctl enable --now linkedbuddy-gateway.service

say "Connecting to NordVPN ($COUNTRY)"
if /usr/bin/python3 "$APP/linkedbuddy-gateway.py" --connect "$COUNTRY"; then
  systemctl enable openvpn-client@linkedbuddy >/dev/null 2>&1
else
  warn "Could not connect yet. Open the control page and pick a country."
fi

echo
say "Done"
echo "Control page:  http://$HOME_IP:8080   (from your phone or computer on your home Wi-Fi)"
echo "               Tip: reserve $HOME_IP for this Pi in your router so the address never changes."
if [ "$MODE" = cable ]; then
  echo "TV:            plug the TV's network cable into the Pi port $TV_IF."
  echo "               On the TV choose Wired network, automatic settings. Nothing to type."
else
  echo "TV:            Settings > Network > IP settings > Enter manually"
  echo "               Gateway and DNS:  $GW_IP   (keep the TV's own IP address and subnet)"
fi
echo "Kill switch:   if NordVPN drops, the TV loses internet instead of leaking your real location."
