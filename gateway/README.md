# Linked Buddy Gateway

Turns a Raspberry Pi 3/4/5 (Raspberry Pi OS) or any Debian/Ubuntu PC into a NordVPN box for your TV.
Samsung and LG TVs cannot run a VPN app, so the TV plugs into this box instead.

## Install
1. Join the Pi to your home **Wi-Fi** so its Ethernet port is free for the TV.
2. Get your NordVPN **service credentials**: my.nordaccount.com → NordVPN → Manual setup.
3. Copy this folder to the Pi, open Terminal in it and run:
   ```
   sudo bash install.sh
   ```
4. Choose **1 (Cable into the Pi)**, enter the service login, a control-page password and a country.
5. Plug the TV's network cable into the Pi. On the TV choose **Wired**, automatic settings.

## Use
- Control page: `http://<Pi home address>:8080` (printed at the end of install). Any user name, your chosen password.
  Switch country, turn the VPN on/off, update the NordVPN login, and see the TV settings.
- Command line: `sudo python3 /opt/linkedbuddy/linkedbuddy-gateway.py --connect "United Kingdom"` or `--disconnect`.

## Home-network mode (no cable)
Pick option 2. On the TV set IP settings manually, keep its IP and subnet, and set **Gateway** and **DNS** to the Pi's address.

## How it works
- OpenVPN connects to NordVPN's recommended server for the chosen country (`openvpn-client@linkedbuddy`).
- dnsmasq gives the TV its address (cable mode) and answers DNS through NordVPN's DNS (103.86.96.100 / 103.86.99.100).
- Kill switch: TV traffic is only forwarded into the VPN tunnel. If NordVPN drops, the TV has no internet rather than leaking your real location.

## Uninstall
```
sudo systemctl disable --now linkedbuddy-gateway linkedbuddy-firewall openvpn-client@linkedbuddy
sudo rm -rf /opt/linkedbuddy /etc/linkedbuddy /etc/dnsmasq.d/linkedbuddy.conf /etc/systemd/system/linkedbuddy-*.service /etc/sysctl.d/99-linkedbuddy.conf
sudo nmcli con delete linkedbuddy-tv 2>/dev/null; sudo systemctl restart dnsmasq
```
