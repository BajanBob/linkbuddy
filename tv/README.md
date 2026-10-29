# Linked Buddy TV app

A status screen for the TV: which country the TV appears to be in, and whether the Linked Buddy Gateway's NordVPN is on.
TV makers do not allow apps to change network settings or run a VPN, so this app reports status; the Gateway or SmartDNS does the change.

## Samsung (Tizen)
1. TV: Apps → press 1 2 3 4 5 on the remote → Developer mode **On**, enter your computer's IP → restart the TV.
2. Computer: install Tizen Studio with the TV extensions; create a Samsung certificate in Certificate Manager.
3. Device Manager → add the TV's IP → connect.
4. File → Import → Tizen → Tizen Project → choose `samsung-tizen` → right-click → **Run As → Tizen Web Application**.
   (CLI: `tizen package -t wgt -s <cert-profile> -- samsung-tizen` then `tizen install -n LinkedBuddyTV.wgt -t <tv>`.)

## LG (webOS)
1. TV: install **Developer Mode** from the LG Content Store, sign in with an LG developer account, Dev Mode Status **On**.
2. Computer: `npm i -g @webos-tools/cli`, then `ares-setup-device` to add the TV (use the passphrase shown on the TV).
3. `cd lg-webos && ares-package . && ares-install --device tv *.ipk && ares-launch --device tv com.bajanbob.linkedbuddy.tv`
   Developer Mode sessions expire after 50 hours unless extended in the Developer Mode app.
