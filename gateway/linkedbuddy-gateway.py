#!/usr/bin/env python3
"""Linked Buddy Gateway control page.

Runs on the Raspberry Pi / Linux PC that gives your TV NordVPN.
  python3 linkedbuddy-gateway.py              start the control page (port 8080)
  python3 linkedbuddy-gateway.py --connect "United Kingdom"
  python3 linkedbuddy-gateway.py --disconnect
Only the Python standard library is used.
"""
import base64, hashlib, hmac, json, os, re, subprocess, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ETC = "/etc/linkedbuddy"
CONF = os.path.join(ETC, "config.json")
AUTH = os.path.join(ETC, "nord.auth")
OVPN = "/etc/openvpn/client/linkedbuddy.conf"
UNIT = "openvpn-client@linkedbuddy"
NORD = "https://api.nordvpn.com/v1"
UA = {"User-Agent": "LinkedBuddy/1.0"}
LOCK = threading.Lock()
_cache = {"countries": None, "ip": None, "ip_t": 0}


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


def best_server(country_id):
    q = urllib.parse.urlencode({
        "filters[country_id]": country_id,
        "filters[servers_technologies][identifier]": "openvpn_udp",
        "limit": 1,
    })
    s = get_json(NORD + "/servers/recommendations?" + q)
    if not s:
        raise RuntimeError("No NordVPN server available right now")
    return s[0]["hostname"]


def write_ovpn(hostname):
    if not re.fullmatch(r"[a-z0-9.-]+\.nordvpn\.com", hostname):
        raise ValueError("unexpected server name")
    url = "https://downloads.nordcdn.com/configs/files/ovpn_udp/servers/%s.udp.ovpn" % hostname
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode()
    if "<ca>" not in text:
        raise RuntimeError("NordVPN config download looked wrong")
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("auth-user-pass"):
            line = "auth-user-pass " + AUTH
        lines.append(line)
    lines += ["", "# Linked Buddy", "persist-tun"]
    os.makedirs(os.path.dirname(OVPN), exist_ok=True)
    tmp = OVPN + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, OVPN)


def tunnel_up():
    return os.path.exists("/sys/class/net/tun0")


def connect(country_name):
    with LOCK:
        c = find_country(country_name)
        host = best_server(c["id"])
        write_ovpn(host)
        conf = load_conf()
        conf["country"], conf["server"] = c["name"], host
        save_conf(conf)
        run("systemctl", "enable", UNIT)
        run("systemctl", "restart", UNIT)
        _cache["ip_t"] = 0
        for _ in range(30):
            time.sleep(1)
            if tunnel_up():
                run("systemctl", "restart", "linkedbuddy-firewall")  # re-apply rules for the new tun0
                return {"ok": True, "country": c["name"], "server": host}
        log = run("journalctl", "-u", UNIT, "-n", "8", "--no-pager").stdout
        hint = "Check your NordVPN service username/password." if "AUTH_FAILED" in log else "See: journalctl -u " + UNIT
        return {"ok": False, "error": "NordVPN did not connect. " + hint}


def disconnect():
    with LOCK:
        run("systemctl", "stop", UNIT)
        run("systemctl", "disable", UNIT)
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
    up = tunnel_up()
    return {
        "vpn": up,
        "country": conf.get("country"),
        "server": conf.get("server"),
        "mode": conf["mode"],
        "gateway_ip": conf["gateway_ip"],
        "home_ip": conf.get("home_ip"),
        "tv_port": conf["tv_if"],
        "public": public_ip() if up else None,
        "kill_switch": run("iptables", "-S", "LB_FWD").returncode == 0,
    }


def set_nord_login(user, pw):
    if not user or not pw:
        raise ValueError("Both service username and password are needed")
    with open(AUTH, "w") as f:
        f.write(user.strip() + "\n" + pw.strip() + "\n")
    os.chmod(AUTH, 0o600)


# ---------------------------------------------------------------- web page
PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Linked Buddy Gateway</title>
<style>
:root{--bg:#0d1420;--card:#16202f;--ink:#e9eef6;--mute:#93a3b8;--ok:#2fd27a;--bad:#ff5d5d;--acc:#4da3ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif}
main{max-width:640px;margin:auto;padding:16px}h1{font-size:22px;margin:8px 0 16px}
.card{background:var(--card);border-radius:14px;padding:16px;margin-bottom:14px}
.big{font-size:20px;font-weight:700}.ok{color:var(--ok)}.bad{color:var(--bad)}.mute{color:var(--mute);font-size:14px}
select,input,button{font:inherit;padding:10px 12px;border-radius:10px;border:1px solid #2b3a50;background:#0f1826;color:var(--ink);width:100%;margin-top:8px}
button{background:var(--acc);border:0;color:#04121f;font-weight:700;cursor:pointer}button.sec{background:#2b3a50;color:var(--ink)}
code{background:#0f1826;padding:2px 6px;border-radius:6px}ol{padding-left:20px}li{margin:4px 0}
</style></head><body><main>
<h1>Linked Buddy Gateway</h1>
<div class="card"><div id="state" class="big">Checking…</div><div id="where" class="mute"></div></div>
<div class="card"><b>Watch from</b><select id="country"></select>
<button id="go">Connect</button><button id="stop" class="sec">Turn VPN off</button><div id="msg" class="mute"></div></div>
<div class="card"><b>TV settings</b><div id="tv"></div></div>
<div class="card"><b>NordVPN login</b><div class="mute">Service credentials from my.nordaccount.com → NordVPN → Manual setup</div>
<input id="nu" placeholder="Service username" autocomplete="off"><input id="np" type="password" placeholder="Service password">
<button id="save" class="sec">Save login</button></div>
</main><script>
const $=id=>document.getElementById(id);
async function api(p,b){const r=await fetch(p,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{});return r.json()}
function tvHelp(s){
 if(s.mode==='cable')return `<ol><li>Plug the TV's network cable into the Pi port <code>${s.tv_port}</code>.</li>
 <li>Samsung: Settings → General → Network → Open Network Settings → <b>Wired</b>.</li>
 <li>LG: Settings → All Settings → Network → <b>Wired Connection (Ethernet)</b>.</li><li>Leave everything automatic. Nothing to type.</li></ol>`;
 return `<ol><li>Samsung: Settings → General → Network → Network Status → <b>IP Settings</b>.<br>LG: Network → your connection → <b>Edit</b> (untick Set automatically).</li>
 <li>Keep the TV's IP address and subnet mask as they are.</li><li>Gateway: <code>${s.gateway_ip}</code></li>
 <li>DNS: <code>${s.gateway_ip}</code></li><li>Save, then restart the TV app you want to watch.</li></ol>`}
async function refresh(){try{const s=await api('/api/status');
 $('state').innerHTML=s.vpn?'<span class="ok">● TV is protected by NordVPN</span>':'<span class="bad">● VPN is off: TV has no internet (kill switch)</span>';
 $('where').textContent=s.vpn&&s.public?`Showing as ${s.public.city||''} ${s.public.country||''} · ${s.public.ip||''} · server ${s.server||''}`:(s.vpn?'Connected to '+s.country:'');
 $('tv').innerHTML=tvHelp(s); if(!$('country').dataset.set&&s.country){$('country').value=s.country;$('country').dataset.set=1}
}catch(e){$('state').textContent='Cannot reach the gateway'}}
(async()=>{try{const c=await api('/api/countries');$('country').innerHTML=c.map(x=>`<option>${x.name}</option>`).join('')}catch(e){$('msg').textContent='Could not load NordVPN countries.'}
 refresh();setInterval(refresh,15000)})();
$('go').onclick=async()=>{$('msg').textContent='Connecting… (up to 30 seconds)';const r=await api('/api/connect',{country:$('country').value});
 $('msg').textContent=r.ok?'Connected to '+r.country+'. Restart the app on your TV.':r.error;refresh()};
$('stop').onclick=async()=>{await api('/api/disconnect',{});$('msg').textContent='VPN off. The TV has no internet until you connect again.';refresh()};
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
            return self.send(200, {"linkedbuddy": True, "vpn": tunnel_up(), "country": s.get("country")}, cors=True)
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
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/connect":
                return self.send(200, connect(str(body.get("country", ""))))
            if self.path == "/api/disconnect":
                return self.send(200, disconnect())
            if self.path == "/api/login":
                set_nord_login(str(body.get("user", "")), str(body.get("password", "")))
                return self.send(200, {"ok": True})
        except Exception as e:
            return self.send(200, {"ok": False, "error": str(e)})
        self.send(404, {"error": "not found"})


def main():
    args = sys.argv[1:]
    if args[:1] == ["--connect"]:
        r = connect(" ".join(args[1:]) or load_conf().get("country", "United States"))
        print(json.dumps(r))
        sys.exit(0 if r.get("ok") else 1)
    if args[:1] == ["--disconnect"]:
        print(json.dumps(disconnect()))
        return
    port = int(load_conf().get("port", 8080))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
