# Linked Buddy

Cast your phone to Samsung, LG and other smart TVs, and give the TV NordVPN.

| Part | Folder | What it does |
|---|---|---|
| Linked Buddy app | `www`, `electron` | Windows, Mac, Linux, Raspberry Pi, Android, iPhone. Step-by-step casting for every phone/TV pair, NordVPN SmartDNS setup for Samsung and LG, and a Gateway checker. |
| Linked Buddy Gateway | `gateway` | Raspberry Pi / Linux PC that runs NordVPN for the TV, with a kill switch and a control page. The TV plugs in by cable and needs no settings. |
| Linked Buddy TV | `tv` | Samsung (Tizen) and LG (webOS) app that shows the TV's apparent country and Gateway status. |

## Three ways to put the TV on NordVPN
1. **Gateway** (best): whole TV through NordVPN, works with the TV's own Netflix/Prime/etc apps. See `gateway/README.md`.
2. **SmartDNS**: activate in Nord Account, set TV DNS to 103.86.96.103 / 103.86.99.103. Unblocks catalogs, no encryption.
3. **Mirror the phone** with NordVPN on. Quickest; protected apps may show a black screen when mirrored.

Phone hotspots do not pass the phone's VPN to a TV on most phones, so they are not used.
Streaming services' terms may forbid region-changing, and some block VPN servers.

## Build installers
Push this folder to a GitHub repo and run **Actions → Build Linked Buddy → Run workflow**. Enter a tag such as `v1.0.0` to publish a release with all installers plus the Gateway and TV zips.

Local: `npm install`, then `npm start` (desktop), `npm run dist:win|dist:mac|dist:linux|dist:pi`, `npm run android:add && npm run android:apk`.

Not affiliated with NordVPN, Samsung or LG.
