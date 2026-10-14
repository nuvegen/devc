"""Sitzung anlegen, schließen, Server per SIGTERM beenden: bleibt ein lxc-exec-Prozess oder eine bash übrig?"""
import json, os, signal, subprocess, sys, time, urllib.request
port = 8742
server = subprocess.Popen([sys.executable, "-m", "devc", "serve", "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
token = subprocess.run([sys.executable, "-m", "devc", "token", "show"], capture_output=True, text=True).stdout.strip()
def api(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Devc": "1"})
    return json.load(urllib.request.urlopen(req))
def children():
    out = subprocess.run(["pgrep", "-P", str(server.pid), "-x", "lxc"], capture_output=True, text=True).stdout.split()
    return len(out)
def bashes():
    out = subprocess.run(["lxc", "--project", "devcontainers", "exec", "it-sudo", "--", "pgrep", "-c", "-x", "bash"], capture_output=True, text=True)
    return out.stdout.strip()
time.sleep(1.5)
base = "/api/containers/it-sudo/terminals"
print("vorher: lxc-Kinder", children(), "bash im Container", bashes())
ids = [api("POST", base, {"user": "dev"})["id"] for _ in range(3)]
time.sleep(1)
print("3 Sitzungen: lxc-Kinder", children(), "bash", bashes())
start = time.time(); api("DELETE", f"{base}/{ids[0]}")
while len(api("GET", base)["sessions"]) > 2 and time.time() - start < 10: time.sleep(0.1)
print(f"eine geschlossen nach {time.time() - start:.1f}s: lxc-Kinder", children(), "bash", bashes(), "Sitzungen", len(api("GET", base)["sessions"]))
start = time.time(); server.send_signal(signal.SIGTERM); rc = server.wait(20)
time.sleep(1)
left = subprocess.run(["pgrep", "-P", "1", "-f", "exec it-sudo -t"], capture_output=True, text=True).stdout.split()
print(f"Server beendet (rc {rc}) nach {time.time() - start:.1f}s: bash", bashes())
