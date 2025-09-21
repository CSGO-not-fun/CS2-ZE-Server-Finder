# query_servers.py
# Accurate-only ping measurement (fast UI):
# - ICMP: run SAMPLE_COUNT single-packet pings concurrently -> ping = min, jitter = P95 - P50
# - Fallback A2S: run SAMPLE_COUNT concurrent A2S_INFO probes with same aggregation
import time
import csv
import sys
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import concurrent.futures

try:
    import a2s  # python-a2s==1.4.0
except Exception:
    print("[ERROR] python-a2s not installed. Run the setup first.", flush=True)
    sys.exit(2)

# ---- tunables ----
A2S_TIMEOUT = 1.0       # was 1.5; tighten a bit since we do concurrent probes
MAX_WORKERS = 100
SAMPLE_COUNT = 5
ICMP_TIMEOUT_MS = 800   # you can lower to 600 if you want even snappier failures


# -------------------- parsing server_list --------------------
def parse_line(line: str):
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    name, addr = "", s
    if "|" in s:
        addr, name = s.split("|", 1)
    elif "," in s:
        addr, name = s.split(",", 1)
    addr, name = addr.strip(), name.strip().strip('"').strip("'")
    if ":" not in addr:
        addr += ":27015"
    host, port_s = addr.split(":", 1)
    try:
        port = int(port_s)
    except ValueError:
        print(f"[WARN] Bad port in line: {line!r}", flush=True)
        return None
    return host.strip(), port, name


def load_server_list(path="server_list.txt"):
    servers = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                p = parse_line(raw)
                if p:
                    servers.append(p)
    except FileNotFoundError:
        print(f"[ERROR] {path} not found.", flush=True)
        sys.exit(3)
    return servers


# -------------------- small helpers --------------------
def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


# -------------------- concurrent ICMP (single-packet) --------------------
def _icmp_one(host: str, timeout_ms: int) -> int | None:
    """Send exactly one echo; return RTT(ms) or None."""
    try:
        if sys.platform.startswith("win"):
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
        else:
            tout_sec = max(1, int(round(timeout_ms / 1000)))
            cmd = ["ping", "-c", "1", "-W", str(tout_sec), host]

        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=max(2, timeout_ms / 1000 + 1.5),
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        m = re.search(r"(time|时间)\s*[=<]\s*(\d+)\s*ms", out, flags=re.IGNORECASE)
        if m:
            return int(m.group(2))
        for t in re.findall(r"(\d+)\s*ms", out):
            try:
                return int(t)
            except:
                pass
        return None
    except Exception:
        return None


def icmp_samples(host: str, count: int, timeout_ms: int) -> list[int]:
    """Run `count` single-packet pings concurrently (no 1s gaps on Windows)."""
    vals: list[int] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as ex:
        futs = [ex.submit(_icmp_one, host, timeout_ms) for _ in range(count)]
        for fut in concurrent.futures.as_completed(futs):
            v = fut.result()
            if isinstance(v, int):
                vals.append(v)
    return vals


# -------------------- concurrent A2S INFO --------------------
def _a2s_one(host: str, port: int, timeout: float) -> int | None:
    try:
        t0 = time.perf_counter()
        _ = a2s.info((host, port), timeout=timeout)
        t1 = time.perf_counter()
        return int((t1 - t0) * 1000)
    except Exception:
        return None


def a2s_samples(host: str, port: int, count: int) -> list[int]:
    vals: list[int] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as ex:
        futs = [ex.submit(_a2s_one, host, port, A2S_TIMEOUT) for _ in range(count)]
        for fut in concurrent.futures.as_completed(futs):
            v = fut.result()
            if isinstance(v, int):
                vals.append(v)
    return vals


# -------------------- aggregation --------------------
def aggregate_ping(samples: list[int]) -> tuple[int | None, int | None]:
    """Return (ping_ms, jitter_ms) where ping = min(samples), jitter = P95 - P50."""
    if not samples:
        return None, None
    s = sorted(samples)
    p50 = _percentile(s, 50)
    p95 = _percentile(s, 95)
    best = s[0]
    jitter = int(round((p95 - p50))) if (p95 is not None and p50 is not None) else None
    return int(best), jitter


# -------------------- per-server query --------------------
def query_one(host: str, port: int, name: str) -> dict:
    r = {
        "ip": f"{host}:{port}",
        "name": name,
        "online": False,
        "player_count": None,
        "max_players": None,
        "map": None,
        "ping_ms": None,
        "jitter_ms": None,
        "ping_method": None,
        "error": None,
    }

    # server info (does not block long; A2S_TIMEOUT used)
    try:
        info = a2s.info((host, port), timeout=A2S_TIMEOUT)
        r["online"] = True
        r["player_count"] = info.player_count
        r["max_players"] = info.max_players
        r["map"] = info.map_name
    except Exception as e:
        r["error"] = str(e)

    # accurate-only path (fast via concurrency)
    icmp = icmp_samples(host, SAMPLE_COUNT, ICMP_TIMEOUT_MS)
    if icmp:
        ping, jitter = aggregate_ping(icmp)
        r["ping_ms"], r["jitter_ms"], r["ping_method"] = ping, jitter, "ICMP"
    else:
        a2s_vals = a2s_samples(host, port, SAMPLE_COUNT)
        ping, jitter = aggregate_ping(a2s_vals)
        r["ping_ms"], r["jitter_ms"], r["ping_method"] = ping, jitter, ("A2S" if a2s_vals else None)

    return r


# -------------------- main --------------------
def main():
    entries = load_server_list("server_list.txt")
    if not entries:
        print("[INFO] No servers in server_list.txt.", flush=True)
        print("[RECORDS] 0", flush=True)
        sys.exit(0)

    print(f"[INFO] Accurate mode. Querying {len(entries)} servers...", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(entries))) as ex:
        futs = {ex.submit(query_one, h, p, nm): (h, p, nm) for (h, p, nm) in entries}
        for fut in as_completed(futs):
            d = fut.result()
            results.append(d)
            label = f' "{d["name"]}"' if d["name"] else ""
            if d["online"]:
                ping_part = f"{d['ping_ms']}ms" if d["ping_ms"] is not None else "n/a"
                jitter_part = f", jitter={d['jitter_ms']}ms" if d["jitter_ms"] is not None else ""
                method = d["ping_method"] or "n/a"
                print(
                    f"{d['ip']}{label}  ONLINE  players={d['player_count']}/{d['max_players']}  "
                    f"map={d['map']}  ping={ping_part}{jitter_part} ({method})",
                    flush=True,
                )
            else:
                print(f"{d['ip']}{label}  OFFLINE/NO-RESPONSE  err={d['error']}", flush=True)

    outcsv = "servers_output.csv"
    with open(outcsv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ip", "name", "online", "player_count", "max_players", "map",
                    "ping_ms", "jitter_ms", "ping_method", "error"])
        for r in results:
            w.writerow([
                r["ip"], r.get("name", ""), r["online"], r["player_count"], r["max_players"],
                r["map"], r["ping_ms"], r["jitter_ms"], r["ping_method"], r["error"]
            ])

    print(f"[DONE] Saved {len(results)} rows to {outcsv}", flush=True)
    print(f"[RECORDS] {len(results)}", flush=True)


if __name__ == "__main__":
    main()
