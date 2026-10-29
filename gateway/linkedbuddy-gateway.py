#!/usr/bin/env python3
"""Linked Buddy Gateway control page.

Runs on the Raspberry Pi / Linux PC that gives your TV NordVPN, Onion over VPN, your own
imported OpenVPN settings, or Tor.
  python3 linkedbuddy-gateway.py                       start the control page (port 8080)
  python3 linkedbuddy-gateway.py --connect "United Kingdom"
  python3 linkedbuddy-gateway.py --onion               NordVPN Onion over VPN
  python3 linkedbuddy-gateway.py --import file.ovpn    use your own OpenVPN / NordVPN file
  python3 linkedbuddy-gateway.py --tor [us]            send the TV through Tor (optional exit country)
  python3 linkedbuddy-gateway.py --disconnect
  python3 linkedbuddy-gateway.py --extras on|off       show or hide the optional Tor and Onion modes
Only the Python standard library is used.
"""
import base64, hashlib, hmac, json, os, re, subprocess, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ETC = "/etc/linkedbuddy"
CONF = os.path.join(ETC, "config.json")
AUTH = os.path.join(ETC, "nord.auth")
CUSTOM_AUTH = os.path.join(ETC, "custom.auth")
TORRC = os.path.join(ETC, "torrc")
OVPN = "/etc/openvpn/client/linkedbuddy.conf"
UNIT = "openvpn-client@linkedbuddy"
TOR_UNIT = "linkedbuddy-tor"
FIREWALL = "linkedbuddy-firewall"
NORD = "https://api.nordvpn.com/v1"
UA = {"User-Agent": "LinkedBuddy/1.1"}
LOCK = threading.Lock()
_cache = {"countries": None, "ip": None, "ip_t": 0}

MODE_NAMES = {"nord": "NordVPN", "onion": "NordVPN Onion over VPN", "ovpn": "Imported VPN settings", "tor": "Tor"}


def load_conf():
    with open(CONF) as f:
        return json.load(f)


def save_conf(c):
    tmp = CONF + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONF)


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def write_private(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------- NordVPN
def countries():
    if not _cache["countries"]:
        data = get_json(NORD + "/servers/countries")
        _cache["countries"] = sorted(({"id": c["id"], "name": c["name"], "code": c["code"]} for c in data),
                                     key=lambda c: c["name"])
    return _cache["countries"]


def find_country(name):
    n = name.strip().lower()
    for c in countries():
        if n in (c["name"].lower(), c["code"].lower()):
            return c
    for c in countries():
        if c["name"].lower().startswith(n):
            return c
    raise ValueError("NordVPN has no servers in '%s'" % name)


def best_server(country_id=None, group=None):
    f = {"filters[servers_technologies][identifier]": "openvpn_udp", "limit": 1}
    if country_id:
        f["filters[country_id]"] = country_id
    if group:
        f["filters[servers_groups][identifier]"] = group
    s = get_json(NORD + "/servers/recommendations?" + urllib.parse.urlencode(f))
    if not s:
        raise RuntimeError("No NordVPN server of that kind is available right now")
    return s[0]["hostname"], (s[0].get("locations") or [{}])[0].get("country", {}).get("name")


def nord_ovpn(hostname):
    if not re.fullmatch(r"[a-z0-9.-]+\.nordvpn\.com", hostname):
        raise ValueError("unexpected server name")
    url = "https://downloads.nordcdn.com/configs/files/ovpn_udp/servers/%s.udp.ovpn" % hostname
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode()
    if "<ca>" not in text:
        raise RuntimeError("NordVPN config download looked wrong")
    return text


# ---------------------------------------------------------------- OpenVPN settings files
# Directives that would let a settings file run programs or touch files on the Pi as root.
BLOCKED = {"up", "down", "route-up", "route-pre-down", "ipchange", "learn-address", "client-connect",
           "client-disconnect", "auth-user-pass-verify", "tls-verify", "plugin", "script-security",
           "management", "management-client-user", "management-client-group", "log", "log-append",
           "status", "writepid", "cd", "chroot", "daemon", "config", "tmp-dir", "dev-node", "iproute",
           "setenv", "setenv-safe", "user", "group", "askpass", "secret", "pkcs12", "crl-verify",
           "http-proxy-user-pass", "dev-type", "lladdr", "ifconfig-noexec", "route-noexec", "server",
           "mode", "tls-server"}
FILE_OPTS = {"ca", "cert", "key", "tls-auth", "tls-crypt", "tls-crypt-v2", "extra-certs"}


def clean_ovpn(text, auth_file):
    """Check an OpenVPN / NordVPN settings file and adapt it for the gateway."""
    if len(text) > 60000:
        raise ValueError("That file is too big to be a VPN settings file")
    out, has_remote, has_ca, has_redirect, inline = [], False, False, False, None
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if inline:
            out.append(raw)
            if line.lower() == "</%s>" % inline:
                inline = None
            continue
        m = re.match(r"^<([a-z0-9-]+)>$", line.lower())
        if m:
            inline = m.group(1)
            if inline == "ca":
                has_ca = True
            if inline not in FILE_OPTS | {"connection", "auth-user-pass", "http-proxy-user-pass"}:
                raise ValueError("Unsupported block <%s> in the file" % inline)
            if inline == "connection":
                raise ValueError("<connection> blocks are not supported; use plain 'remote' lines")
            if inline == "auth-user-pass":
                raise ValueError("Remove the inline <auth-user-pass> block and type the login on the page instead")
            out.append(raw)
            continue
        if not line or line[0] in "#;":
            out.append(raw)
            continue
        words = line.split()
        opt = words[0].lower().lstrip("-")
        if opt in BLOCKED:
            raise ValueError("For safety the gateway does not accept the '%s' setting. Remove that line and try again." % opt)
        if opt in FILE_OPTS and len(words) > 1 and not (opt == "tls-auth" and words[1].isdigit()):
            raise ValueError("'%s' points to a file on another computer. Use a file with the keys pasted inside it." % opt)
        if opt == "dev":
            if not words[1:] or not words[1].startswith("tun"):
                raise ValueError("Only 'dev tun' VPN files work with the TV gateway")
            out.append("dev tun0")
            continue
        if opt == "auth-user-pass":
            out.append("auth-user-pass " + auth_file)
            continue
        if opt == "remote":
            has_remote = True
        if opt == "redirect-gateway":
            has_redirect = True
        out.append(raw)
    if inline:
        raise ValueError("The file ends inside a <%s> block" % inline)
    if not has_remote:
        raise ValueError("No 'remote' server line found. Is this an .ovpn file?")
    if not has_ca:
        raise ValueError("No <ca> certificate block found. Download the .ovpn file again.")
    out += ["", "# Linked Buddy", "persist-tun"]
    if not has_redirect:
        out.append("redirect-gateway def1")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- start / stop
def vpn_up():
    return os.path.exists("/sys/class/net/tun0")


def tor_up():
    return run("systemctl", "is-active", "--quiet", TOR_UNIT).returncode == 0


def active():
    return tor_up() if load_conf().get("vpn_mode") == "tor" else vpn_up()


def extras_on():
    """Tor and Onion over VPN are optional; they are switched on during install (or with --extras on)."""
    return bool(load_conf().get("tor_onion", False))


def _need_extras():
    if not extras_on():
        raise ValueError("Tor and Onion over VPN are turned off. Turn them on with: "
                         "sudo python3 /opt/linkedbuddy/linkedbuddy-gateway.py --extras on")


def set_extras(on):
    conf = load_conf()
    if on and not (os.path.exists("/usr/bin/tor") or os.path.exists("/usr/sbin/tor")):
        r = run("apt-get", "install", "-y", "-qq", "tor")
        if r.returncode != 0:
            return {"ok": False, "error": "Could not install Tor: " + (r.stderr or r.stdout)[-300:]}
        run("systemctl", "disable", "--now", "tor")  # Linked Buddy runs its own Tor service
    if not on and conf.get("vpn_mode") in ("tor", "onion"):
        _stop_everything()
        conf["vpn_mode"] = "nord"
    conf["tor_onion"] = bool(on)
    save_conf(conf)
    return {"ok": True, "tor_onion": bool(on)}


def _set_mode(mode, **extra):
    conf = load_conf()
    conf["vpn_mode"] = mode
    for k in ("country", "server", "exit"):
        conf.pop(k, None) if k not in extra else None
    conf.update(extra)
    save_conf(conf)
    return conf


def _stop_everything():
    run("systemctl", "stop", UNIT)
    run("systemctl", "disable", UNIT)
    run("systemctl", "stop", TOR_UNIT)
    run("systemctl", "disable", TOR_UNIT)


def _start_vpn(result):
    run("systemctl", "stop", TOR_UNIT)
    run("systemctl", "disable", TOR_UNIT)
    run("systemctl", "restart", FIREWALL)
    run("systemctl", "enable", UNIT)
    run("systemctl", "restart", UNIT)
    _cache["ip_t"] = 0
    for _ in range(30):
        time.sleep(1)
        if vpn_up():
            run("systemctl", "restart", FIREWALL)  # re-apply rules for the new tun0
            result["ok"] = True
            return result
    log = run("journalctl", "-u", UNIT, "-n", "12", "--no-pager").stdout
    hint = ("Check the VPN username/password." if "AUTH_FAILED" in log
            else "See: journalctl -u " + UNIT)
    return {"ok": False, "error": "The VPN did not connect. " + hint}


def connect(country_name):
    with LOCK:
        c = find_country(country_name)
        host, _ = best_server(c["id"])
        write_private(OVPN, clean_ovpn(nord_ovpn(host), AUTH))
        _set_mode("nord", country=c["name"], server=host)
        return _start_vpn({"mode": "nord", "country": c["name"], "server": host})


def connect_onion():
    _need_extras()
    with LOCK:
        host, where = best_server(group="legacy_onion_over_vpn")
        write_private(OVPN, clean_ovpn(nord_ovpn(host), AUTH))
        _set_mode("onion", country=where or "Onion over VPN", server=host)
        return _start_vpn({"mode": "onion", "country": where, "server": host})


def import_ovpn(text, name="", user="", password=""):
    with LOCK:
        auth = AUTH
        if user or password:
            if not (user and password):
                raise ValueError("Type both the username and the password, or leave both empty to use the NordVPN login")
            write_private(CUSTOM_AUTH, user.strip() + "\n" + password.strip() + "\n")
            auth = CUSTOM_AUTH
        cfg = clean_ovpn(text, auth)
        server = (re.search(r"^\s*remote\s+(\S+)", cfg, re.M) or [None, "?"])[1]
        write_private(OVPN, cfg)
        label = (name or server)[:60]
        _set_mode("ovpn", country=label, server=server)
        return _start_vpn({"mode": "ovpn", "country": label, "server": server})


def write_torrc(exit_cc):
    conf = load_conf()
    gw = conf["gateway_ip"]
    lines = [
        "# Linked Buddy Tor settings (written by the control page)",
        "User debian-tor",
        "DataDirectory /var/lib/linkedbuddy-tor",
        "SocksPort 0",
        "TransPort %s:9040" % gw,
        "DNSPort %s:5353" % gw,
        "VirtualAddrNetworkIPv4 10.192.0.0/10",
        "AutomapHostsOnResolve 1",
        "AutomapHostsSuffixes .onion,.exit",
        "ClientUseIPv6 0",
        "Log notice syslog",
    ]
    if exit_cc:
        lines += ["ExitNodes {%s}" % exit_cc, "StrictNodes 1"]
    write_private(TORRC, "\n".join(lines) + "\n")
    os.makedirs("/var/lib/linkedbuddy-tor", mode=0o700, exist_ok=True)
    try:
        import shutil
        shutil.chown("/var/lib/linkedbuddy-tor", "debian-tor", "debian-tor")
    except (LookupError, OSError):
        pass


def connect_tor(exit_cc=""):
    exit_cc = (exit_cc or "").strip().lower()
    if exit_cc and not re.fullmatch(r"[a-z]{2}", exit_cc):
        raise ValueError("Use a two-letter country code such as us, gb or ca, or leave it empty")
    _need_extras()
    with LOCK:
        if not os.path.exists("/usr/bin/tor") and not os.path.exists("/usr/sbin/tor"):
            return {"ok": False, "error": "Tor is not installed. Run: sudo apt install tor, then try again."}
        write_torrc(exit_cc)
        _set_mode("tor", country=("Tor exit: " + exit_cc.upper()) if exit_cc else "Tor (any country)",
                  server="Tor network", exit=exit_cc)
        run("systemctl", "stop", UNIT)
        run("systemctl", "disable", UNIT)
        run("systemctl", "restart", FIREWALL)
        run("systemctl", "enable", TOR_UNIT)
        since = time.strftime("%Y-%m-%d %H:%M:%S")
        run("systemctl", "restart", TOR_UNIT)
        _cache["ip_t"] = 0
        for _ in range(90):
            time.sleep(1)
            log = run("journalctl", "-u", TOR_UNIT, "--since", since, "--no-pager").stdout
            if "Bootstrapped 100%" in log:
                return {"ok": True, "mode": "tor", "country": "Tor", "server": "Tor network"}
            if not tor_up():
                break
        return {"ok": False, "error": "Tor did not finish connecting. Some networks block Tor; see: journalctl -u " + TOR_UNIT}


def disconnect():
    with LOCK:
        _stop_everything()
        _cache["ip_t"] = 0
        return {"ok": True}


def public_ip():
    if time.time() - _cache["ip_t"] > 30:
        try:
            d = get_json("https://ipwho.is/", timeout=8)
            _cache["ip"] = {"ip": d.get("ip"), "country": d.get("country"), "city": d.get("city"),
                            "isp": (d.get("connection") or {}).get("isp")}
        except Exception:
            _cache["ip"] = None
        _cache["ip_t"] = time.time()
    return _cache["ip"]


def status():
    conf = load_conf()
    mode = conf.get("vpn_mode", "nord")
    up = active()
    return {
        "vpn": up,
        "vpn_mode": mode,
        "mode_name": MODE_NAMES.get(mode, mode),
        "country": conf.get("country"),
        "server": conf.get("server"),
        "exit": conf.get("exit", ""),
        "mode": conf["mode"],
        "gateway_ip": conf["gateway_ip"],
        "home_ip": conf.get("home_ip"),
        "tv_port": conf["tv_if"],
        # The Pi's own traffic uses the VPN but not Tor, so this check only means something for VPN modes.
        "public": public_ip() if up and mode != "tor" else None,
        "kill_switch": run("iptables", "-S", "LB_FWD").returncode == 0,
        "tor_onion": bool(conf.get("tor_onion", False)),
        "tor_installed": os.path.exists("/usr/bin/tor") or os.path.exists("/usr/sbin/tor"),
    }


def set_nord_login(user, pw):
    if not user or not pw:
        raise ValueError("Both service username and password are needed")
    write_private(AUTH, user.strip() + "\n" + pw.strip() + "\n")


# ---------------------------------------------------------------- web page
PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Linked Buddy Gateway</title>
<style>
:root{--bg:#0d1420;--card:#16202f;--ink:#e9eef6;--mute:#93a3b8;--ok:#2fd27a;--bad:#ff5d5d;--acc:#4da3ff;--line:#2b3a50}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif}
main{max-width:640px;margin:auto;padding:16px}h1{font-size:22px;margin:8px 0 16px}
.card{background:var(--card);border-radius:14px;padding:16px;margin-bottom:14px}
.big{font-size:20px;font-weight:700}.ok{color:var(--ok)}.bad{color:var(--bad)}.mute{color:var(--mute);font-size:14px}
select,input,button,textarea{font:inherit;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:#0f1826;color:var(--ink);width:100%;margin-top:8px}
button{background:var(--acc);border:0;color:#04121f;font-weight:700;cursor:pointer}button.sec{background:var(--line);color:var(--ink)}
.tabs{display:flex;gap:6px;flex-wrap:wrap}.tabs button{width:auto;flex:1;min-width:120px;background:#0f1826;color:var(--ink);border:1px solid var(--line)}
.tabs button[aria-pressed=true]{background:var(--acc);color:#04121f;border-color:var(--acc)}
.pane[hidden]{display:none}code{background:#0f1826;padding:2px 6px;border-radius:6px}ol{padding-left:20px}li{margin:4px 0}
.warn{border-left:3px solid #f5b83d;padding-left:10px;margin-top:10px}
</style></head><body><main>
<h1>Linked Buddy Gateway</h1>
<div class="card"><div id="state" class="big">Checking…</div><div id="where" class="mute"></div></div>
<div class="card">
 <b>How the TV goes out</b>
 <div class="tabs" id="tabs">
  <button data-p="nord" aria-pressed="true">NordVPN</button><button data-p="ovpn" aria-pressed="false">Import settings</button>
  <button data-p="onion" aria-pressed="false" class="extra" hidden>Onion over VPN</button><button data-p="tor" aria-pressed="false" class="extra" hidden>Tor</button>
 </div>
 <div class="pane" id="p-nord"><div class="mute" style="margin-top:10px">NordVPN's best server in the country you pick. Best for streaming.</div>
  <select id="country"></select><button id="go">Connect</button></div>
 <div class="pane" id="p-onion" hidden><div class="mute" style="margin-top:10px">NordVPN first, then the Tor network. Lets the TV's browser open .onion sites. Slower; NordVPN picks the country.</div>
  <button id="goOnion">Connect Onion over VPN</button></div>
 <div class="pane" id="p-ovpn" hidden><div class="mute" style="margin-top:10px">Use a NordVPN .ovpn file you downloaded (nordvpn.com/ovpn) or a file from any OpenVPN provider. Files that try to run programs are refused.</div>
  <input type="file" id="file" accept=".ovpn,.conf,text/plain">
  <input id="label" placeholder="Name (optional), e.g. Nord UK #2345">
  <input id="cu" placeholder="VPN username (leave empty to use the NordVPN login below)" autocomplete="off">
  <input id="cp" type="password" placeholder="VPN password">
  <button id="goImport">Import and connect</button></div>
 <div class="pane" id="p-tor" hidden><div class="mute" style="margin-top:10px">Every TV connection goes through Tor. The TV can open .onion sites. Video is slow and most streaming services block Tor.</div>
  <input id="exit" maxlength="2" placeholder="Exit country code (optional): us, gb, ca, de…">
  <button id="goTor">Connect through Tor</button>
  <div class="mute warn">Tor hides where the TV connects from, but the TV still sends its own account and device data to Samsung, LG and app makers.</div></div>
 <div class="mute" id="extrasOff" hidden style="margin-top:10px">Optional extras: Tor and Onion over VPN are off. <a href="#" id="extrasOn" style="color:var(--acc)">Turn them on</a></div>
 <div class="mute" id="extrasOnNote" hidden style="margin-top:10px"><a href="#" id="extrasOffBtn" style="color:var(--acc)">Hide Tor and Onion over VPN</a></div>
 <button id="stop" class="sec">Turn off (TV has no internet)</button><div id="msg" class="mute"></div>
</div>
<div class="card"><b>TV settings</b><div id="tv"></div></div>
<div class="card"><b>NordVPN login</b><div class="mute">Service credentials from my.nordaccount.com → NordVPN → Manual setup</div>
<input id="nu" placeholder="Service username" autocomplete="off"><input id="np" type="password" placeholder="Service password">
<button id="save" class="sec">Save login</button></div>
</main><script>
const $=id=>document.getElementById(id);
async function api(p,b){const r=await fetch(p,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{});return r.json()}
function pane(p){document.querySelectorAll('#tabs button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.p===p));
 document.querySelectorAll('.pane').forEach(x=>x.hidden=x.id!=='p-'+p)}
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>pane(b.dataset.p));
function tvHelp(s){
 if(s.mode==='cable')return `<ol><li>Plug the TV's network cable into the Pi port <code>${s.tv_port}</code>.</li>
 <li>Samsung: Settings → General → Network → Open Network Settings → <b>Wired</b>.</li>
 <li>LG: Settings → All Settings → Network → <b>Wired Connection (Ethernet)</b>.</li><li>Leave everything automatic. Nothing to type.</li></ol>`;
 return `<ol><li>Samsung: Settings → General → Network → Network Status → <b>IP Settings</b>.<br>LG: Network → your connection → <b>Edit</b> (untick Set automatically).</li>
 <li>Keep the TV's IP address and subnet mask as they are.</li><li>Gateway: <code>${s.gateway_ip}</code></li>
 <li>DNS: <code>${s.gateway_ip}</code></li><li>Save, then restart the TV app you want to watch.</li></ol>`}
let first=true;
async function refresh(){try{const s=await api('/api/status');
 $('state').innerHTML=s.vpn?`<span class="ok">● TV is going out through ${s.mode_name}</span>`:'<span class="bad">● Off: the TV has no internet (kill switch)</span>';
 $('where').textContent=s.vpn?(s.public?`Showing as ${s.public.city||''} ${s.public.country||''} · ${s.public.ip||''} · ${s.server||''}`:(s.country||'')+(s.vpn_mode==='tor'?' · check the country with the Linked Buddy TV app':'')):'';
 $('tv').innerHTML=tvHelp(s);
 document.querySelectorAll('.extra').forEach(b=>b.hidden=!s.tor_onion);$('extrasOff').hidden=s.tor_onion;$('extrasOnNote').hidden=!s.tor_onion;
 if(!s.tor_onion&&['tor','onion'].includes(document.querySelector('#tabs [aria-pressed=true]').dataset.p))pane('nord');
 if(first){first=false;pane(s.tor_onion||!['tor','onion'].includes(s.vpn_mode)?(s.vpn_mode||'nord'):'nord');if(s.vpn_mode==='nord'&&s.country)$('country').value=s.country;if(s.exit)$('exit').value=s.exit}
}catch(e){$('state').textContent='Cannot reach the gateway'}}
(async()=>{try{const c=await api('/api/countries');$('country').innerHTML=c.map(x=>`<option>${x.name}</option>`).join('')}catch(e){$('msg').textContent='Could not load NordVPN countries.'}
 refresh();setInterval(refresh,15000)})();
async function act(label,p,b){$('msg').textContent=label;const r=await api(p,b);
 $('msg').textContent=r.ok?'Connected'+(r.country?' ('+r.country+')':'')+'. Restart the app on your TV.':r.error;refresh()}
$('go').onclick=()=>act('Connecting… (up to 30 seconds)','/api/connect',{country:$('country').value});
$('goOnion').onclick=()=>act('Connecting to Onion over VPN… (up to 30 seconds)','/api/onion',{});
$('goTor').onclick=()=>act('Connecting to Tor… (up to 90 seconds)','/api/tor',{exit:$('exit').value});
$('goImport').onclick=async()=>{const f=$('file').files[0];if(!f){$('msg').textContent='Choose an .ovpn file first.';return}
 const text=await f.text();await act('Importing and connecting…','/api/import',{ovpn:text,name:$('label').value||f.name.replace(/\.(ovpn|conf)$/i,''),user:$('cu').value,password:$('cp').value});$('cp').value=''};
async function extras(on){$('msg').textContent=on?'Turning on Tor and Onion over VPN (installs Tor, up to a minute)…':'Turning off…';
 const r=await api('/api/extras',{on});$('msg').textContent=r.ok?(on?'Tor and Onion over VPN are on.':'Tor and Onion over VPN are off.'):r.error;refresh()}
$('extrasOn').onclick=e=>{e.preventDefault();extras(true)};$('extrasOffBtn').onclick=e=>{e.preventDefault();extras(false)};
$('stop').onclick=async()=>{await api('/api/disconnect',{});$('msg').textContent='Off. The TV has no internet until you connect again.';refresh()};
$('save').onclick=async()=>{const r=await api('/api/login',{user:$('nu').value,password:$('np').value});
 $('msg').textContent=r.ok?'Saved. Press Connect.':r.error;$('np').value=''};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "LinkedBuddy"

    def log_message(self, *a):
        pass

    def authed(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            _, pw = base64.b64decode(h[6:]).decode().split(":", 1)
        except Exception:
            return False
        c = load_conf()
        got = hashlib.sha256((c["salt"] + pw).encode()).hexdigest()
        return hmac.compare_digest(got, c["hash"])

    def send(self, code, body, ctype="application/json", cors=False):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def gate(self):
        if self.authed():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Linked Buddy (user: any name)"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/ping":  # open, read-only check used by the Linked Buddy TV and phone apps
            s = load_conf()
            mode = s.get("vpn_mode", "nord")
            return self.send(200, {"linkedbuddy": True, "vpn": active(), "vpn_mode": mode,
                                   "mode_name": MODE_NAMES.get(mode, mode), "country": s.get("country")}, cors=True)
        if not self.gate():
            return
        try:
            if path == "/":
                return self.send(200, PAGE, "text/html")
            if path == "/api/status":
                return self.send(200, status())
            if path == "/api/countries":
                return self.send(200, countries())
        except Exception as e:
            return self.send(500, {"ok": False, "error": str(e)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.gate():
            return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return self.send(415, {"ok": False, "error": "JSON only"})
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 200000)
            body = json.loads(self.rfile.read(n) or b"{}")
            p = self.path
            if p == "/api/connect":
                return self.send(200, connect(str(body.get("country", ""))))
            if p == "/api/onion":
                return self.send(200, connect_onion())
            if p == "/api/import":
                return self.send(200, import_ovpn(str(body.get("ovpn", "")), str(body.get("name", "")),
                                                  str(body.get("user", "")), str(body.get("password", ""))))
            if p == "/api/tor":
                return self.send(200, connect_tor(str(body.get("exit", ""))))
            if p == "/api/extras":
                with LOCK:
                    return self.send(200, set_extras(bool(body.get("on"))))
            if p == "/api/disconnect":
                return self.send(200, disconnect())
            if p == "/api/login":
                set_nord_login(str(body.get("user", "")), str(body.get("password", "")))
                return self.send(200, {"ok": True})
        except Exception as e:
            return self.send(200, {"ok": False, "error": str(e)})
        self.send(404, {"error": "not found"})


def main():
    args = sys.argv[1:]
    cmd = args[:1]
    if cmd == ["--connect"]:
        r = connect(" ".join(args[1:]) or load_conf().get("country", "United States"))
    elif cmd == ["--onion"]:
        r = connect_onion()
    elif cmd == ["--import"] and len(args) > 1:
        with open(args[1]) as f:
            r = import_ovpn(f.read(), os.path.basename(args[1]).rsplit(".", 1)[0])
    elif cmd == ["--tor"]:
        r = connect_tor(args[1] if len(args) > 1 else "")
    elif cmd == ["--extras"] and len(args) > 1 and args[1] in ("on", "off"):
        r = set_extras(args[1] == "on")
    elif cmd == ["--disconnect"]:
        r = disconnect()
    else:
        port = int(load_conf().get("port", 8080))
        ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
        return
    print(json.dumps(r))
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
