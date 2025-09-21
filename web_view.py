# web_view.py
# Async scan: start in background, UI returns immediately and polls status.
from flask import Flask, jsonify, render_template_string, make_response
import csv, os, datetime, subprocess, threading, json, webbrowser

app = Flask(__name__)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(PROJECT_DIR, "servers_output.csv")
QUERY_SCRIPT = os.path.join(PROJECT_DIR, "query_servers.py")
VENV_PY = os.path.join(PROJECT_DIR, "venv", "Scripts", "python.exe") if os.name == "nt" \
           else os.path.join(PROJECT_DIR, "venv", "bin", "python")
PYTHON_EXE = VENV_PY if os.path.exists(VENV_PY) else "python"

# 固定列顺序（player 是由 player_count/max_players 合并得到）
FIXED_COLUMNS = ["ip","name","online","player","map","ping_ms","jitter_ms","ping_method","error"]

# ---- 扫描状态（内存） ----
SCAN_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "stdout_tail": "",
    "stderr_tail": "",
    "last_csv_mtime": None,
}
SCAN_LOCK = threading.Lock()

def _csv_mtime_iso():
    if not os.path.exists(CSV_FILE):
        return None
    ts = os.path.getmtime(CSV_FILE)
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def _read_csv_rows():
    """读取 CSV，合并为 player，并按当前玩家数降序排序。"""
    rows = []
    if not os.path.exists(CSV_FILE):
        return rows

    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cur_str = (row.get("player_count") or "").strip()
            mx_str  = (row.get("max_players") or "").strip()

            # 解析当前玩家数用于排序；解析失败给 -1 让它排到最后
            try:
                cur_num = int(cur_str)
            except Exception:
                cur_num = -1

            # 组合显示用的 "cur/max"
            player_val = f"{cur_str}/{mx_str}" if (cur_str or mx_str) else ""

            out = {
                "ip":          row.get("ip", ""),
                "name":        row.get("name", ""),
                "online":      row.get("online", ""),
                "player":      player_val,
                "map":         row.get("map", ""),
                "ping_ms":     row.get("ping_ms", ""),
                "jitter_ms":   row.get("jitter_ms", ""),
                "ping_method": row.get("ping_method", ""),
                "error":       row.get("error", ""),
                "_cur_num":    cur_num,  # 仅用于排序，稍后会删掉
            }
            rows.append(out)

    # 按当前玩家数降序；空值/解析失败的在最后
    rows.sort(key=lambda r: r.get("_cur_num", -1), reverse=True)
    for r in rows:
        r.pop("_cur_num", None)

    return rows

def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

def _tail(s, n=40):
    return "\n".join((s or "").splitlines()[-n:])

def _run_scan_in_thread(timeout_sec=180):
    """后台线程：运行 query_servers.py（准确模式）并更新 SCAN_STATE。"""
    with SCAN_LOCK:
        if SCAN_STATE["running"]:
            return
        SCAN_STATE.update({
            "running": True,
            "started_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "ok": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "last_csv_mtime": _csv_mtime_iso(),
        })

    cmd = [PYTHON_EXE, QUERY_SCRIPT]
    ok, out_tail, err_tail = False, "", ""
    try:
        proc = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=timeout_sec)
        ok = (proc.returncode == 0)
        out_tail, err_tail = _tail(proc.stdout), _tail(proc.stderr)
    except subprocess.TimeoutExpired:
        ok, out_tail, err_tail = False, "", f"Scan timed out after {timeout_sec}s"
    except Exception as e:
        ok, out_tail, err_tail = False, "", f"{type(e).__name__}: {e}"

    with SCAN_LOCK:
        SCAN_STATE.update({
            "running": False,
            "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ok": ok,
            "stdout_tail": out_tail,
            "stderr_tail": err_tail,
            "last_csv_mtime": _csv_mtime_iso(),
        })

def start_scan():
    with SCAN_LOCK:
        if SCAN_STATE["running"]:
            return False
    t = threading.Thread(target=_run_scan_in_thread, daemon=True)
    t.start()
    return True

# ---------- 路由 ----------

@app.route("/start_scan", methods=["POST", "GET"])
def start_scan_route():
    started = start_scan()
    payload = {"started": started, "running": SCAN_STATE["running"]}
    return _no_cache(make_response(jsonify(payload)))

@app.route("/status")
def status_route():
    with SCAN_LOCK:
        payload = {
            "running": SCAN_STATE["running"],
            "started_at": SCAN_STATE["started_at"],
            "finished_at": SCAN_STATE["finished_at"],
            "ok": SCAN_STATE["ok"],
            "stdout_tail": SCAN_STATE["stdout_tail"],
            "stderr_tail": SCAN_STATE["stderr_tail"],
            "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "csv_mtime": _csv_mtime_iso(),
        }
    return _no_cache(make_response(jsonify(payload)))

@app.route("/data")
def data_route():
    payload = {
        "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "csv_mtime": _csv_mtime_iso(),
        "rows": _read_csv_rows(),
    }
    return _no_cache(make_response(jsonify(payload)))

# ----- 页面模板（不是 f-string，避免 {} 冲突） -----
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CS2 Zombie Escape Servers</title>
<style>
body { font-family: Arial, sans-serif; margin: 20px; }
h2 { margin: 0 0 12px; }
.controls { margin: 10px 0 16px; }
.info { font-size: 14px; color: #555; margin: 6px 0 12px; }
pre.log { background:#111; color:#0f0; padding:8px; overflow:auto; max-height:200px; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 14px; }
th { background-color: #333; color: white; }
tr:nth-child(even) { background-color: #f7f7f7; }
button { padding: 8px 14px; cursor: pointer; }
.status-ok { color: #0a0; font-weight: bold; }
.status-bad { color: #a00; font-weight: bold; }
.badge-ok { background:#e6ffed; color:#0a0; padding:2px 6px; border-radius:6px; font-weight:bold; }
.badge-bad { background:#ffecec; color:#a00; padding:2px 6px; border-radius:6px; font-weight:bold; }

/* ping 颜色梯度 */
.ping-g { background:#e6ffed; color:#0a0; }
.ping-y { background:#fffbe6; color:#8a6d00;}
.ping-o { background:#fff0e6; color:#a65c00;}
.ping-r { background:#ffecec; color:#a00; }

/* jitter 颜色 */
.jit-g { background:#e6ffed; color:#0a0; }
.jit-o { background:#fff0e6; color:#a65c00;}
.jit-r { background:#ffecec; color:#a00; }

/* player 合并列颜色：<=61 绿；==62 橙；>=63 红 */
.plr-g { background:#e6ffed; color:#0a0; }
.plr-o { background:#fff0e6; color:#a65c00; }
.plr-r { background:#ffecec; color:#a00; }

/* 可点击 IP 的样式 */
a.connect {
  color: #0366d6;
  text-decoration: none;
}
a.connect:hover { text-decoration: underline; }
</style>
<script>
const FIXED_COLUMNS = {{ fixed_columns | safe }};

let lastCsvMtime = null;
let statusTimer = null;

function toBool(v){
  if (typeof v === 'boolean') return v;
  if (v == null) return false;
  const s = String(v).trim().toLowerCase();
  return (s === 'true' || s === '1' || s === 'yes');
}
function renderCellText(h, v){
  if (h === 'online') return toBool(v) ? '✅ Online' : '❌ Offline';
  if (h === 'ping_ms' || h === 'jitter_ms'){
    if (v === null || v === undefined || v === '') return 'n/a';
    return String(v) + ' ms';
  }
  return (v ?? '');
}
function applyCellClass(td, h, v){
  if (h === 'online') {
    td.className = toBool(v) ? 'badge-ok' : 'badge-bad'; return;
  }
  if (h === 'ping_ms') {
    const n = Number(v); if (!isFinite(n)) return;
    if (n < 50) td.className = 'ping-g';
    else if (n < 100) td.className = 'ping-y';
    else if (n < 150) td.className = 'ping-o';
    else td.className = 'ping-r';
    return;
  }
  if (h === 'jitter_ms') {
    const n = Number(v); if (!isFinite(n)) return;
    if (n < 30) td.className = 'jit-g';
    else if (n < 60) td.className = 'jit-o';
    else td.className = 'jit-r';
    return;
  }
  if (h === 'player') {
    // v 形如 "63/64"
    const parts = String(v || "").split("/");
    const cur = Number(parts[0]);
    if (!isFinite(cur)) return;
    if (cur <= 61) td.className = 'plr-g';
    else if (cur === 62) td.className = 'plr-o';
    else if (cur >= 63) td.className = 'plr-r';
    return;
  }
  // 其它列不加样式
}

/* —— 连接 CS:GO/CS2 —— */
function connectUrl(addr){
  // 首选：通用 connect 协议
  return 'steam://connect/' + addr;
}
function altConnectUrl(addr){
  // 备用：run + +connect（730=CS:GO/CS2 appid）
  return 'steam://run/730//+connect%20' + encodeURIComponent(addr);
}
function tryConnect(addr){
  // 主链接由 <a> 默认处理；我们做一个轻量的备用触发，兼容部分环境
  setTimeout(()=>{
    const iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    iframe.src = altConnectUrl(addr);
    document.body.appendChild(iframe);
    setTimeout(()=>iframe.remove(), 3000);
  }, 150);
  return true; // 让 <a> 的默认行为继续执行
}

async function fetchJSON(u) {
  const res = await fetch(u + (u.includes('?')?'&':'?') + 't=' + Date.now(), {cache:'no-store'});
  return await res.json();
}
function renderTable(rows){
  const table = document.getElementById('tbl');
  table.innerHTML = '';
  if (!rows || rows.length === 0) { table.innerHTML = '<tr><td>No data yet</td></tr>'; return; }

  const htr = document.createElement('tr');
  FIXED_COLUMNS.forEach(h=>{ const th=document.createElement('th'); th.textContent=h; htr.appendChild(th); });
  table.appendChild(htr);

  rows.forEach(r=>{
    const tr=document.createElement('tr');
    FIXED_COLUMNS.forEach(h=>{
      const td=document.createElement('td');
      const val=r[h];

      if (h === 'ip' && val) {
        // 渲染为可点击链接
        const a = document.createElement('a');
        a.className = 'connect';
        a.href = connectUrl(val);
        a.textContent = val;
        a.title = '点击后将通过 Steam 连接到 ' + val;
        a.onclick = () => tryConnect(val);
        td.appendChild(a);
      } else {
        td.textContent = renderCellText(h,val);
      }

      applyCellClass(td,h,val);
      tr.appendChild(td);
    });
    table.appendChild(tr);
  });
}
async function refreshData(){
  const d = await fetchJSON('/data');
  document.getElementById('serverTime').textContent = d.server_time || 'n/a';
  document.getElementById('csvMtime').textContent = d.csv_mtime || 'n/a';
  renderTable(d.rows);
  lastCsvMtime = d.csv_mtime || lastCsvMtime;
}
async function pollStatus(){
  const s = await fetchJSON('/status');
  document.getElementById('scanStatus').innerHTML = s.running ? 'Scanning…' :
    (s.ok === true ? '<span class="status-ok">Scan OK</span>' :
     (s.ok === false ? '<span class="status-bad">Scan FAILED</span>' : 'Idle'));
  document.getElementById('stdoutTail').textContent = s.stdout_tail || '';
  document.getElementById('stderrTail').textContent = s.stderr_tail || '';
  document.getElementById('serverTime').textContent = s.server_time || 'n/a';
  document.getElementById('csvMtime').textContent = s.csv_mtime || 'n/a';
  if (!s.running && s.csv_mtime && lastCsvMtime && s.csv_mtime !== lastCsvMtime) {
    await refreshData();
  }
}
async function startScan(){
  await fetchJSON('/start_scan'); // fire & forget
  if (!statusTimer) statusTimer = setInterval(pollStatus, 1000);
}
async function onLoad(){
  await refreshData();
  await startScan();
}
window.onload = onLoad;
</script>
</head>
<body>
  <h2>CS2 Zombie Escape Servers</h2>
  <div class="controls">
    <button onclick="startScan()">Scan Now</button>
    <span class="info">点击表格里的 <b>ip</b> 可以直接通过 Steam 连接服务器。</span>
  </div>
  <div class="info">
    Data served at (server): <b id="serverTime">-</b><br>
    CSV last modified: <b id="csvMtime">-</b><br>
    Scan status: <span id="scanStatus">Idle</span>
  </div>
  <details>
    <summary>Show scan logs (last lines)</summary>
    <pre class="log" id="stdoutTail"></pre>
    <pre class="log" id="stderrTail"></pre>
  </details>
  <table id="tbl"></table>
</body>
</html>
"""

@app.route("/")
def index():
    # 用 Jinja 变量注入列名 JSON，避免 f-string 与 {} 冲突
    return render_template_string(INDEX_HTML, fixed_columns=json.dumps(FIXED_COLUMNS))

if __name__ == "__main__":
    import socket, threading, webbrowser, time

    def pick_free_port(preferred=5000, fallback=5001):
        for p in (preferred, fallback):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    continue
        return preferred

    port = pick_free_port(5000, 5001)
    url = f"http://127.0.0.1:{port}"
    print(f"Starting local web server on {url}")

    def open_once():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # 延迟一点点时间，等 Flask 完成绑定再打开（避免偶发竞态）
    opener = threading.Timer(0.6, open_once)
    opener.start()

    try:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)
    except OSError as e:
        # 极少见的竞态：探测成功但 run 失败，切换端口再开一次
        try:
            opener.cancel()
        except Exception:
            pass
        alt = 5001 if port != 5001 else 5000
        url = f"http://127.0.0.1:{alt}"
        print(f"[WARN] Port {port} failed: {e}. Falling back to {url}")
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        app.run(host="127.0.0.1", port=alt, debug=False, threaded=True, use_reloader=False)
