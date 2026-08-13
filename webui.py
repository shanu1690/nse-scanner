"""NSE Scanner Web GUI - click a button instead of typing commands.

Run:
  .venv/bin/python webui.py

Then open http://127.0.0.1:5000 in your browser (opens automatically).
The buttons run the exact same logic as the CLI (scanner.py), streaming
live output into the console panel.

On your phone (same WiFi): open http://<your-mac-ip>:5000  (IP shown below).
"""

import os
import socket
import sys
import threading
import time
import types
import webbrowser

from flask import Flask, Response, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scanner

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

JOBS = {}
LOCK = threading.Lock()
LAST_TABLES = {"tables": [], "updated": 0}


def _store_tables(tables):
    with LOCK:
        LAST_TABLES["tables"] = tables or []
        LAST_TABLES["updated"] = time.time()


class _Tee:
    def __init__(self, buf):
        self.buf = buf

    def write(self, data):
        if data:
            with LOCK:
                self.buf["out"].append(data)

    def flush(self):
        pass


def _run_job(job_id, fn):
    buf = {"out": [], "done": False, "error": None}
    JOBS[job_id] = buf
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(buf)
    sys.stderr = _Tee(buf)
    try:
        fn()
    except Exception as exc:
        with LOCK:
            buf["error"] = str(exc)
        print(f"[ERROR] {exc}")
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        with LOCK:
            buf["done"] = True


def _busy():
    with LOCK:
        return any(not b["done"] for b in JOBS.values())


def _start(fn):
    if _busy():
        return None
    job_id = f"job-{int(time.time() * 1000)}"
    t = threading.Thread(target=_run_job, args=(job_id, fn), daemon=True)
    t.start()
    return job_id


def _args(**kw):
    return types.SimpleNamespace(**kw)


def _fade_flag(v):
    return v if v in (True, False) else None


# ---------------------------------------------------------------- actions ---

def _a_scan(p):
    tables = scanner.cmd_scan(_args(mode=p.get("mode", "all"), refresh=False,
                                    top=p.get("top"), fade=_fade_flag(p.get("fade"))))
    _store_tables(tables)


def _a_track(p):
    scanner.cmd_track(_args(status=p.get("status", False), top=p.get("top"),
                            fade=_fade_flag(p.get("fade"))))


def _a_report(p):
    scanner.cmd_report(_args(email=p.get("email", False),
                             ntfy=p.get("ntfy", False), top=p.get("top"),
                             fade=_fade_flag(p.get("fade")),
                             morning=p.get("morning", False)))


def _a_backtest(p):
    scanner.cmd_backtest(_args(period=p.get("period", 6),
                               min_score=p.get("min_score", 60.0),
                               fade=_fade_flag(p.get("fade"))))


def _a_factors(p):
    scanner.cmd_factors(_args(period=p.get("period", 3),
                              min_score=p.get("min_score", 55.0)))


def _a_watch(p):
    scanner.cmd_watch(_args(symbol=p.get("symbol", "SBIN")))


ACTIONS = {
    "scan": _a_scan, "track": _a_track, "report": _a_report,
    "backtest": _a_backtest, "factors": _a_factors, "watch": _a_watch,
}

# ----------------------------------------------------------------- routes ---

@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True) or {}
    action = data.get("action")
    params = data.get("params") or {}
    fn = ACTIONS.get(action)
    if fn is None:
        return jsonify({"error": f"unknown action {action!r}"}), 400
    job_id = _start(lambda: fn(params))
    if job_id is None:
        return jsonify({"error": "Another task is already running. Wait for it to finish."}), 409
    return jsonify({"id": job_id})


@app.route("/api/poll/<job_id>")
def api_poll(job_id):
    buf = JOBS.get(job_id)
    if buf is None:
        return jsonify({"error": "unknown job"}), 404
    with LOCK:
        out = "".join(buf["out"])
        done = buf["done"]
        err = buf["error"]
    return jsonify({"done": done, "error": err, "out": out})


@app.route("/api/tables")
def api_tables():
    with LOCK:
        return jsonify(LAST_TABLES)


@app.route("/api/scorecard")
def api_scorecard():
    from nse import report, tracker
    try:
        rows = tracker.scorecard_data(scanner.DATA_CFG["lookback_days"])
        html = report.render_html(
            "Scorecard",
            {"headers": [], "rows": [], "fmt": []},
            {"headers": [], "rows": [], "fmt": []},
            rows, morning=False,
        )
        return jsonify({"html": html})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/status")
def api_status():
    from nse import report, tracker
    cfg = report.load_secrets()
    email_ok = bool(cfg["email"].get("app_password") and
                    "xxxx" not in cfg["email"].get("app_password", ""))
    reports = []
    rdir = os.path.join(report.ROOT, "data", "reports")
    if os.path.isdir(rdir):
        reports = sorted(os.listdir(rdir))[-3:]
    return jsonify({
        "email_ready": email_ok,
        "tracked_picks": len(tracker.load_picks()),
        "reports": reports,
        "style": scanner.MOM_CFG.get("style", "momentum"),
        "min_score": scanner.MOM_CFG.get("min_score"),
    })


# ------------------------------------------------------------------- page ---

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NSE Scanner Dashboard</title>
<style>
  :root {
    --bg: #0f1222; --card: rgba(255,255,255,.05); --line: rgba(255,255,255,.09);
    --txt: #e7e9f2; --dim: #9aa0b5; --acc: #6c8cff; --grn: #34d399;
    --red: #f87171; --amber: #fbbf24;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    background: radial-gradient(1200px 600px at 20% -10%, #1c2450 0%, var(--bg) 55%);
    color: var(--txt); min-height: 100vh; padding: 28px 22px 60px;
  }
  header { max-width: 1440px; margin: 0 auto 22px; }
  header h1 { font-size: 26px; letter-spacing: .3px; }
  header p { color: var(--dim); margin-top: 6px; font-size: 14px; }
  .chip { display:inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: 12px; background: var(--card); border: 1px solid var(--line);
    margin-right: 6px; margin-top: 10px; color: var(--dim); }
  .chip b { color: var(--txt); }
  main { max-width: 1440px; margin: 0 auto; }
  .grid { display: grid; gap: 14px; }
  .cards { grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
  .card h3 { font-size: 14px; color: var(--dim); font-weight: 600; margin-bottom: 12px; }
  .btn {
    display: block; width: 100%; padding: 12px 10px; border-radius: 10px;
    border: 1px solid var(--line); background: #1a1f3d; color: var(--txt);
    font-size: 14px; font-weight: 600; cursor: pointer; text-align: center;
    transition: transform .06s ease, background .15s ease;
  }
  .btn:hover { background: #232a52; }
  .btn:active { transform: translateY(1px); }
  .btn.primary { background: linear-gradient(135deg, #4f6ef7, #6c8cff); border: none; }
  .btn.primary:hover { filter: brightness(1.08); }
  .btn.warn { background: linear-gradient(135deg, #8a5a1f, #b0782e); border: none; }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  input, select {
    background: #141a35; border: 1px solid var(--line); color: var(--txt);
    border-radius: 8px; padding: 9px 10px; font-size: 13px; width: 100%;
  }
  label.chk { display: flex; align-items: center; gap: 8px; font-size: 13px;
    color: var(--dim); cursor: pointer; }
  label.chk input { width: auto; }
  .console {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px;
    line-height: 1.55; background: #070a18; border: 1px solid var(--line);
    border-radius: 12px; padding: 14px; height: 480px; overflow: auto;
    white-space: pre; color: #c9d2ff;
  }
  .console .ok { color: var(--grn); }
  .console .err { color: var(--red); }
  #runbar { display: none; padding: 10px 14px; border-radius: 10px; font-size: 13px;
    background: rgba(111, 140, 255, .1); border: 1px solid rgba(111,140,255,.35);
    color: #c3ccff; margin-bottom: 14px; }
  #runbar .spin { display: inline-block; width: 12px; height: 12px; margin-right: 8px;
    border: 2px solid #6c8cff; border-top-color: transparent; border-radius: 50%;
    animation: sp 0.8s linear infinite; vertical-align: middle; }
  @keyframes sp { to { transform: rotate(360deg); } }
  h2.sec { font-size: 16px; margin: 26px 0 12px; color: var(--acc); }
  .scorecard-box { display: none; }
  table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  th { background: #1a1f3d; padding: 7px 8px; text-align: left; }
  td { padding: 6px 8px; border-top: 1px solid var(--line); }
  details.guide summary { cursor: pointer; font-size: 16px; margin: 26px 0 12px; color: var(--acc); }
  details.guide[open] summary { margin-bottom: 14px; }
  details.guide h3 { font-size: 13px; color: var(--dim); font-weight: 600; margin: 18px 0 8px; }
  details.guide table { margin-bottom: 8px; }
  .guide-note { color: var(--dim); font-size: 13px; margin-bottom: 6px; }
  .results-head { display: flex; align-items: center; gap: 12px; margin: 26px 0 12px; }
  .results-head h2.sec { margin: 0; }
  .btn.small { width: auto; display: inline-block; padding: 6px 14px; font-size: 12.5px; }
  #results { display: none; }
  .rtable { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 12px 14px; margin-bottom: 18px; }
  .rtable-title { font-size: 14px; color: var(--acc); font-weight: 700; margin-bottom: 10px; }
  .tcol { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 10px; }
  .tcol .act { border: 1px solid var(--line); background: #1a1f3d; color: var(--txt);
    border-radius: 6px; padding: 4px 9px; font-size: 12px; cursor: pointer; }
  .tcol .act:hover { background: #232a52; }
  .tchip { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px;
    background: #141a35; border: 1px solid var(--line); border-radius: 999px;
    padding: 3px 9px; color: var(--dim); cursor: pointer; user-select: none; }
  .tchip input { accent-color: #6c8cff; width: auto; }
  .tchip.on { color: var(--txt); border-color: rgba(108, 140, 255, .55); }
  .res-scroll { max-height: 440px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; }
  table.res { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  table.res th { position: sticky; top: 0; background: #1c2348; padding: 8px 10px;
    text-align: left; cursor: pointer; white-space: nowrap; z-index: 1;
    border-bottom: 2px solid rgba(108, 140, 255, .4); }
  table.res th:hover { background: #263066; }
  table.res td { padding: 6px 10px; border-top: 1px solid var(--line); white-space: nowrap; }
  .foot { color: var(--dim); font-size: 12px; margin-top: 26px; text-align: center; }
</style>
</head>
<body>
<header>
  <h1>NSE Scanner Dashboard</h1>
  <p>One-click scans, scorecard and reports. Runs on this Mac - no data ever leaves your machine.</p>
  <span class="chip">Style: <b id="st-style">-</b></span>
  <span class="chip">Min score: <b id="st-score">-</b></span>
  <span class="chip">Email: <b id="st-email">-</b></span>
  <span class="chip">Tracked picks: <b id="st-picks">-</b></span>
  <span class="chip" id="st-reports" style="display:none"></span>
</header>
<main>
  <div id="runbar"><span class="spin"></span><span id="runtext">Working...</span></div>

  <h2 class="sec">Scans</h2>
  <div class="grid cards" style="margin-bottom:14px">
    <div class="card"><h3>Trading style for this run</h3>
      <label class="chk" style="margin-bottom:8px"><input type="radio" name="style" value="default" checked>
        Default (config = <b>fade</b>)</label>
      <label class="chk" style="margin-bottom:8px"><input type="radio" name="style" value="fade">
        Fade - buy the beaten-down</label>
      <label class="chk"><input type="radio" name="style" value="momentum">
        Momentum - buy the strong</label>
    </div>
  </div>
  <div class="grid cards">
    <div class="card"><h3>Full scan</h3>
      <button class="btn primary" onclick="run('scan',{mode:'all',fade:getFade()})">Delivery + Options</button>
      <br><br>
      <button class="btn" onclick="run('scan',{mode:'momentum',fade:getFade()})">Delivery only</button>
      <br><br>
      <button class="btn" onclick="run('scan',{mode:'options'})">Options only</button>
    </div>
    <div class="card"><h3>One stock deep-dive</h3>
      <input id="sym" placeholder="Symbol, e.g. SBIN">
      <br><br>
      <button class="btn" onclick="run('watch',{symbol:val('sym')})">Analyze stock</button>
    </div>
    <div class="card"><h3>Track &amp; scorecard</h3>
      <button class="btn" onclick="run('track',{fade:getFade()})">Save today's picks</button>
      <br><br>
      <button class="btn" onclick="refreshScorecard()">Show scorecard</button>
    </div>
    <div class="card"><h3>Honesty checks</h3>
      <button class="btn warn" onclick="run('backtest',{period:6,min_score:60,fade:getFade()})">Backtest (6m)</button>
      <br><br>
      <button class="btn warn" onclick="run('factors',{period:3,min_score:55})">Factor study</button>
    </div>
  </div>

  <h2 class="sec">Email / Push</h2>
  <div class="grid cards">
    <div class="card"><h3>Send report</h3>
      <div class="row" style="margin-bottom:12px">
        <label class="chk"><input type="checkbox" id="ce" checked> Email</label>
        <label class="chk"><input type="checkbox" id="cp"> Phone push</label>
        <label class="chk"><input type="checkbox" id="cm"> Morning list</label>
      </div>
      <button class="btn primary" onclick="run('report',{email:chk('ce'),ntfy:chk('cp'),morning:chk('cm'),fade:getFade()})">
        Build &amp; send report
      </button>
      <p style="color:var(--dim);font-size:12px;margin-top:10px">Report is always saved to data/reports/ even if sending is off.</p>
    </div>
  </div>

  <h2 class="sec">How to read the tables</h2>
  <!-- GUIDE -->
  <h2 class="sec">Results</h2>
  <div class="results-head" style="margin-top:0">
    <span id="results-note" style="color:var(--dim);font-size:12px"></span>
    <span class="btn small" id="clear-tables" onclick="clearResults()" style="display:none">Clear</span>
  </div>
  <div id="results"></div>
  <h2 class="sec">Live console</h2>
  <div class="console" id="console">Click any button above. Output appears here.</div>

  <div class="scorecard-box" id="scbox">
    <h2 class="sec">Scorecard</h2>
    <div id="sc"></div>
  </div>

  <div class="foot">Built for shanu.shah1690@gmail.com &bull; free &amp; local &bull; data from Yahoo Finance + NSE</div>
</main>
<script>
let current = null;
function val(id){ return document.getElementById(id).value; }
function chk(id){ return document.getElementById(id).checked; }
function getFade(){
  const v = document.querySelector('input[name="style"]:checked');
  if (!v) return null;
  if (v.value === 'fade') return true;
  if (v.value === 'momentum') return false;
  return null;
}
function log(txt, cls){ 
  const c = document.getElementById('console');
  const d = document.createElement('div');
  d.className = cls || '';
  d.textContent = txt;
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}
function run(action, params){
  if (current) { log('A task is already running.', 'err'); return; }
  log('>>> ' + action + (params && Object.keys(params).length ? ' ' + JSON.stringify(params) : ''), 'ok');
  fetch('/api/run', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action: action, params: params || {}})})
    .then(r => r.json().then(d => ({ok: r.ok, d})))
    .then(({ok, d}) => {
      if (!ok) { log(d.error, 'err'); return; }
      current = d.id;
      setBusy(true);
      poll();
    });
}
function poll(){
  if (!current) return;
  fetch('/api/poll/' + current).then(r => r.json()).then(d => {
    const c = document.getElementById('console');
    const had = c.dataset.n ? parseInt(c.dataset.n) : 0;
    const fresh = d.out.slice(had);
    if (fresh) {
      c.dataset.n = d.out.length;
      const pre = document.createElement('div');
      pre.textContent = fresh;
      c.appendChild(pre);
      c.scrollTop = c.scrollHeight;
    }
    if (d.done) {
      if (d.error) log('ERROR: ' + d.error, 'err');
      log('>>> done.', 'ok');
      current = null; setBusy(false); delete c.dataset.n;
      refreshStatus();
      loadTables();
    } else { setTimeout(poll, 1200); }
  }).catch(() => setTimeout(poll, 1500));
}

// ---- interactive results tables (column toggles, sort, sticky header, CSV) ---
let TABLE_STATE = {};
window._tables = [];

function clearResults(){
  TABLE_STATE = {}; window._tables = [];
  document.getElementById('results').innerHTML = '';
  document.getElementById('results').style.display = 'none';
  document.getElementById('clear-tables').style.display = 'none';
  document.getElementById('results-note').textContent = '';
}

function loadTables(){
  fetch('/api/tables').then(r => r.json()).then(d => {
    if (d.tables && d.tables.length) renderTables(d.tables);
  }).catch(() => {});
}

function renderTables(tables){
  window._tables = tables;
  const wrap = document.getElementById('results');
  document.getElementById('clear-tables').style.display = 'inline-block';
  document.getElementById('results-note').textContent =
    'Click a header to sort, tick a column to hide it, untick to bring it back.';
  wrap.innerHTML = '';
  for (const t of tables){
    const key = t.title;
    if (!TABLE_STATE[key]) TABLE_STATE[key] = {sortCol: -1, sortDir: 1, hidden: {}};
    wrap.appendChild(tableBlock(t, TABLE_STATE[key]));
  }
  wrap.style.display = 'block';
}

function visibleIndices(st, n){
  const out = [];
  for (let i = 0; i < n; i++) if (!st.hidden[i]) out.push(i);
  return out;
}

function cmp(a, b){
  const na = parseFloat(a), nb = parseFloat(b);
  if (!isNaN(na) && !isNaN(nb)) return na - nb;
  return String(a).localeCompare(String(b));
}

function sortRows(rows, st){
  if (st.sortCol < 0) return rows;
  const dir = st.sortDir;
  return rows.slice().sort((x, y) => cmp(x[st.sortCol], y[st.sortCol]) * dir);
}

function tableBlock(t, st){
  const box = document.createElement('div');
  box.className = 'rtable';
  const title = document.createElement('div');
  title.className = 'rtable-title';
  title.textContent = t.title;
  box.appendChild(title);

  const bar = document.createElement('div');
  bar.className = 'tcol';
  const bAll = document.createElement('button'); bAll.className = 'act'; bAll.textContent = 'Show all';
  bAll.onclick = () => { st.hidden = {}; renderTables(window._tables); };
  const bNone = document.createElement('button'); bNone.className = 'act'; bNone.textContent = 'Hide all';
  bNone.onclick = () => { for (let i = 0; i < t.headers.length; i++) st.hidden[i] = true; renderTables(window._tables); };
  const bCsv = document.createElement('button'); bCsv.className = 'act'; bCsv.textContent = 'Download CSV';
  bCsv.onclick = () => downloadCsv(t, st);
  bar.appendChild(bAll); bar.appendChild(bNone);
  t.headers.forEach((h, i) => {
    const lbl = document.createElement('label');
    lbl.className = 'tchip' + (st.hidden[i] ? '' : ' on');
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = !st.hidden[i];
    cb.onchange = () => { st.hidden[i] = !cb.checked; renderTables(window._tables); };
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(h));
    bar.appendChild(lbl);
  });
  bar.appendChild(bCsv);
  box.appendChild(bar);

  const scroller = document.createElement('div');
  scroller.className = 'res-scroll';
  const table = document.createElement('table');
  table.className = 'res';
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  const vis = visibleIndices(st, t.headers.length);
  vis.forEach(i => {
    const th = document.createElement('th');
    th.textContent = t.headers[i] + (st.sortCol === i ? (st.sortDir === 1 ? ' \u25B2' : ' \u25BC') : '');
    th.onclick = () => {
      if (st.sortCol === i) st.sortDir *= -1; else { st.sortCol = i; st.sortDir = 1; }
      renderTables(window._tables);
    };
    hr.appendChild(th);
  });
  thead.appendChild(hr); table.appendChild(thead);

  const tb = document.createElement('tbody');
  const rows = sortRows(t.rows, st);
  if (!rows.length){
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.textContent = 'No rows to show.';
    tr.appendChild(td); tb.appendChild(tr);
  }
  rows.forEach(r => {
    const tr = document.createElement('tr');
    vis.forEach(i => {
      const td = document.createElement('td');
      td.textContent = r[i] != null ? r[i] : '';
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  table.appendChild(tb);
  scroller.appendChild(table);
  box.appendChild(scroller);
  return box;
}

function downloadCsv(t, st){
  const vis = visibleIndices(st, t.headers.length);
  const lines = [vis.map(i => '"' + String(t.headers[i]).replace(/"/g, '""') + '"').join(',')];
  t.rows.forEach(r => lines.push(vis.map(i => '"' + String(r[i] != null ? r[i] : '').replace(/"/g, '""') + '"').join(',')));
  const blob = new Blob([lines.join('\\n')], {type: 'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (t.title.replace(/[^a-z0-9]+/gi, '_').toLowerCase()) + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}
function setBusy(b){
  document.querySelectorAll('.btn').forEach(x => x.disabled = b);
  document.getElementById('runbar').style.display = b ? 'block' : 'none';
}
function refreshScorecard(){
  fetch('/api/scorecard').then(r => r.json()).then(d => {
    const box = document.getElementById('scbox');
    if (d.error) { log('Scorecard error: ' + d.error, 'err'); return; }
    document.getElementById('sc').innerHTML = d.html;
    box.style.display = 'block';
  });
}
function refreshStatus(){
  fetch('/api/status').then(r => r.json()).then(d => {
    document.getElementById('st-style').textContent = d.style;
    document.getElementById('st-score').textContent = d.min_score;
    document.getElementById('st-email').textContent = d.email_ready ? 'READY' : 'not set';
    document.getElementById('st-picks').textContent = d.tracked_picks;
    const rep = document.getElementById('st-reports');
    if (d.reports && d.reports.length) {
      rep.style.display = 'inline-block';
      rep.innerHTML = 'Reports: <b>' + d.reports.map(x => x.replace('report_','').replace('.txt','')).join(', ') + '</b>';
    }
  });
}
refreshStatus();
</script>
</body>
</html>
"""

HEADER_GUIDE = {
    "Options scan": [
        ("SYMBOL", "Stock ticker (e.g. SBIN)."),
        ("SCORE", "Strength out of 100. 70+ = strong setup, 55-70 = watch, 35 and below = avoid."),
        ("DIR", "Bias of the signal: CE = bullish (buy a call), PE = bearish (buy a put)."),
        ("SPOT", "Current market price of the stock."),
        ("EXPIRY", "Date the option expires (we always use the nearest expiry)."),
        ("DTE", "Days left until the option expires."),
        ("PCR", "Put-to-call ratio (put OI / call OI). Below 0.7 = bullish tone, above 1.3 = bearish."),
        ("ATM_IV", "Implied volatility of at-the-money options, in %. How much the market expects the stock to swing."),
        ("IVR%", "IV Rank - today's IV vs its own recent history. Above 75 = premiums are expensive, below 25 = cheap. A tag like (5d) means it's still building history."),
        ("EXP_MOVE%", "Expected Move - the +- percentage the market prices in by expiry. 8% means traders expect the stock to move roughly +-8%."),
        ("dOI", "Change in Open Interest today. Positive = new contracts opened, negative = contracts closed. With the price move it shows fresh buying vs short build-up."),
        ("LOTSIZE", "Number of shares in 1 option contract (e.g. 500 = one contract covers 500 shares)."),
        ("TOTAL_AMT", "Total amount in Rs to buy 1 contract = premium x lotsize. The minimum capital for that trade."),
        ("PICK", "The strike price we recommend trading."),
        ("PREM", "Premium (price) of that option, per share."),
        ("BREAKEVEN", "The spot price at which the trade stops losing money."),
    ],
    "Delivery scan": [
        ("SYMBOL", "Stock ticker."),
        ("SCORE", "Strength out of 100. 70+ = buy, 55-70 = watch, below = avoid."),
        ("CMP", "Current market price."),
        ("ENTRY", "Suggested buy price - place a limit order at or below this."),
        ("STOP", "Loss-cut level - exit if the price falls here."),
        ("TGT2", "Second (and main) profit target."),
        ("R:R", "Reward-to-risk ratio. 2:1 means you gain twice what you risk."),
        ("RISK%", "How much of the price you risk from entry to stop, in %."),
        ("52W_DIST%", "How far the price is below its 52-week high. Low = beaten-down stock."),
        ("VOL_Z", "Trading volume vs its normal level. High = unusually heavy activity."),
        ("ACTION", "What to do: BUY / WATCH / AVOID / FADE-BUY (buy the beaten-down)."),
    ],
    "Scorecard": [
        ("SAVED", "Date the pick was saved."),
        ("SYMBOL", "Stock ticker."),
        ("DIR / STRIKE", "Option side (CE/PE) and strike we picked."),
        ("PREM", "Premium paid per share when the pick was saved."),
        ("BREAKEVEN", "Spot price needed for the trade to break even."),
        ("SPOT", "Current market price."),
        ("TO_BE%", "How much (%) the stock still needs to move to reach breakeven."),
        ("STATUS", "OPEN = trade still running. WIN/LOSS = how it closed."),
        ("EXPIRY", "Date the option expires."),
    ],
}


def _render_guide():
    """HTML for the collapsible header guide (inserted into PAGE)."""
    blocks = []
    for group, entries in HEADER_GUIDE.items():
        rows = "".join(
            f"<tr><td><b>{hdr}</b></td><td>{meaning}</td></tr>" for hdr, meaning in entries
        )
        blocks.append(f"<h3>{group}</h3>"
                      f"<table><tr><th style='width:180px'>Header</th><th>What it means</th></tr>"
                      f"{rows}</table>")
    return (
        '<details class="guide">'
        '<summary>Show header explanations (for new users)</summary>'
        '<p class="guide-note">Every header in the scan tables, in plain English. '
        "These tables are scan results, not financial advice - research before you trade.</p>"
        + "".join(blocks)
        + "</details>"
    )


PAGE = PAGE.replace("<!-- GUIDE -->", _render_guide())


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    host = "0.0.0.0"
    port = int(os.environ.get("NSE_UI_PORT", "5000"))
    ip = _lan_ip()
    print(f"NSE Scanner GUI:  http://127.0.0.1:{port}")
    if ip != "127.0.0.1":
        print(f"  On your phone (same WiFi): http://{ip}:{port}")
    print("  Press Ctrl+C to stop.")
    if "--no-open" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
