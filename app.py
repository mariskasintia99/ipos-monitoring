import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, Response, request, render_template_string

app = Flask(__name__)

# --- KONFIGURASI BOSKU ---
CF_API_TOKEN = "YAHVlmAL47gnHM2roQ8KSW8uOEnfWIeRjdO6b9ua"
CF_ACCOUNT_ID = "eb4b3a7ff38dbf069f2ecc29ae6637e4"
KV_NAMESPACE_ID = "7c6ae9f3416f4fdebd7f5a1ba437d917"
TELEGRAM_TOKEN_IPOS = "8222594585:AAHTZNHgwUm6bTvpt5DieR-5vFks4rhKHjE"
CHAT_ID_IPOS = "6117482148"

# ⚠️ SETUP NAWALA: PENAMAAN API SESUAI KTP BOSKU ⚠️
TARGETS_IPOS = [
    {
        "name": "CNNSLOT", 
        "key": "active_domains_cnn",
        "api_keys": [
            {"label": "API 1", "token": "ls_796e4ae8c9836dbcc93e5a45c67e18e6285c3b55c50a3ebc"},
            {"label": "API 4", "token": "ls_dc60f07366892ea3ee2407a891d7f1e7a82008e7b92bb2e2"}
        ]
    },
    {
        "name": "RTP8000", 
        "key": "active_domains_rtp",
        "api_keys": [
            {"label": "API 2", "token": "ls_b292d9ba81798d79a42ba5312b3653f04d1dbdb3a41f220b"},
            {"label": "API 5", "token": "ls_b7ac342740d2e7c434e7f589bbeae834f51b1634e1692b80"}
        ]
    },
    {
        "name": "RUBY8000", 
        "key": "active_domains_ruby",
        "api_keys": [
            {"label": "API 3", "token": "ls_4d9bae2ee5c8e27f58942145a421e289956d69d664e7f432"},
            {"label": "API 6", "token": "ls_d853e6be788dc6ac388215849737d2bbaaa9ec00cc02ceaa"}
        ]
    }
]

log_buffer = ""

def log(type_msg, msg):
    global log_buffer
    timestamp = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%H:%M:%S")
    line = f"[{timestamp}] [{type_msg}]  {msg}\n"
    print(line, end="")
    log_buffer += line

# --- FUNGSI CLOUDFLARE & TELEGRAM ---
def get_kv(key_name):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{key_name}"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"})
        if r.status_code == 200:
            return r.json()
        return []
    except: 
        return []

def update_kv(key_name, new_list):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{key_name}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    requests.put(url, headers=headers, data=json.dumps(new_list))

def send_and_pin(token, chat_id, message):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"})
        if r.status_code == 200:
            new_msg_id = r.json().get('result', {}).get('message_id')
            if new_msg_id:
                requests.post(f"https://api.telegram.org/bot{token}/pinChatMessage", data={"chat_id": chat_id, "message_id": new_msg_id})
            return True
        return False
    except: 
        return False

# --- GET ALL DOMAINS FROM KV (TANPA PING) ---
def get_all_domains():
    """Mengambil semua domain dari semua KV key"""
    all_domains = []
    for target in TARGETS_IPOS:
        domains = get_kv(target['key'])
        if domains and isinstance(domains, list):
            for domain in domains:
                all_domains.append({
                    "name": domain,
                    "url": f"https://{domain}/",
                    "brand": target['name'],
                    "key": target['key']
                })
    return all_domains

# --- CEK STATUS DOMAIN DARI KV (HANYA UNTUK TAMPILAN) ---
def get_domain_status_from_kv(domain):
    """Cek apakah domain ada di KV (artinya AKTIF)"""
    # Semua domain di KV dianggap AKTIF
    # Domain IPOS akan dihapus dari KV oleh run_api_check()
    return {"status": "up", "ping": None}

# --- MESIN UTAMA (MULTI-KEY & AUTO FAILOVER) ---
def run_api_check():
    global log_buffer
    log_buffer = "" 
    log("SYSTEM", "Memulai pengecekan Nawala (Mode Auto-Cadangan API)...")

    ada_perubahan = False
    global_report = []

    for target in TARGETS_IPOS:
        log("INFO", f"--- Memproses Brand: {target['name']} ---")
        domains = get_kv(target['key'])
        
        if not domains:
            log("INFO", "Tidak ada domain di KV. Skip.")
            continue
            
        api_keys = target.get("api_keys", [])
        active_key_idx = 0 
        blocked_domains = []
        chunk_size = 5 
        
        for i in range(0, len(domains), chunk_size):
            chunk = domains[i:i + chunk_size]
            chunk_berhasil = False
            
            while not chunk_berhasil and active_key_idx < len(api_keys):
                current_api_data = api_keys[active_key_idx]
                current_api_label = current_api_data["label"]
                current_api_token = current_api_data["token"]
                
                log("SYSTEM", f"Mengirim API Request ({len(chunk)} domain) via {current_api_label}...")
                
                url = "https://api.nawala.link/public-check-domain"
                headers = {"X-Api-Key": current_api_token, "Content-Type": "application/json"}
                payload = {"domain": ",".join(chunk)}
                
                try:
                    response = requests.post(url, headers=headers, json=payload, timeout=20)
                    res_json = response.json()
                    
                    used = response.headers.get('X-Ratelimit-Used')
                    if not used or used == "N/A":
                        rem = response.headers.get('X-Ratelimit-Remaining')
                        if rem and rem.isdigit(): 
                            used = 50 - int(rem)
                        else: 
                            used = "Cek Dashboard"

                    log("STATS", f"📊 Pemakaian API {target['name']} ({current_api_label}): {used}/50")
                    
                    if response.status_code == 429:
                        log("WARN", f"⚠️ Limit {current_api_label} HABIS TOTAL! Beralih ke API Cadangan...")
                        active_key_idx += 1 
                        time.sleep(2)
                        continue 
                    
                    if not isinstance(res_json, dict):
                        log("ERROR", f"Format respon Nawala kacau (Bukan Object): {res_json}")
                        break

                    if not res_json.get("success"):
                        log("ERROR", f"API Error pada {target['name']}: {res_json}")
                        break 
                    
                    chunk_berhasil = True
                    api_data = res_json.get("data", [])
                    
                    for item in api_data:
                        if not isinstance(item, dict):
                            continue
                        
                        dom = item.get("domain", "").lower().strip()
                        if item.get("nawala", {}).get("blocked") or item.get("network", {}).get("blocked"):
                            blocked_domains.append(dom)
                            
                except Exception as e:
                    log("ERROR", f"Gagal menghubungi API: {e}")
                    break
            
            if not chunk_berhasil:
                log("ERROR", f"🚨 SEMUA CADANGAN API {target['name']} HABIS! Melewati sisa domain brand ini.")
                break 
                
            time.sleep(1) 
            
        active, removed = [], []
        for d in domains:
            if d.lower().strip() in blocked_domains:
                removed.append(d)
                log("WARN", f"🔴 STATUS: IPOS ➜ {d} [AUTO DELETE]")
            else:
                active.append(d)
                log("SUCCESS", f"🟢 STATUS: AMAN ➜ {d}")
                
        if removed:
            update_kv(target['key'], active)
            ada_perubahan = True
            
        global_report.append({"name": target["name"], "active": active, "removed": removed})

    if ada_perubahan:
        log("INFO", "Mengirim laporan Telegram...")
        waktu_str = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d/%m/%Y, %H:%M:%S WIB")
        garis = "---------------------------------------"
        msg = f"📅 Waktu: {waktu_str}\n🌐 Source: Nawala.in (Auto Check Ipos)\n\n"
        for r in global_report:
            msg += f"🍄 UPDATE LINK [{r['name']}]\n{garis}\n"
            for d in r['removed']: 
                msg += f"🔴 {d} - IPOS\n"
            for d in r['active']: 
                msg += f"🟢 {d}\n"
            msg += f"{garis}\n"
        send_and_pin(TELEGRAM_TOKEN_IPOS, CHAT_ID_IPOS, msg)

    log("SUCCESS", "Pengecekan Nawala Selesai!")
    return log_buffer

# --- HTML TEMPLATE (DIPERKECIL) ---
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="id">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Status — IPOS Monitoring</title>
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" />
    <style>
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

      :root {
        --bg: #1a1a1a;
        --card: #242424;
        --border: #333;
        --accent: #c70000;
        --green: #22c55e;
        --red: #ef4444;
        --yellow: #f59e0b;
        --text: #f1f1f1;
        --muted: #888;
      }

      body {
        background: var(--bg);
        color: var(--text);
        font-family: "Roboto", Arial, sans-serif;
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        font-size: 12px;
      }

      /* ── HEADER ── */
      header {
        background: #111;
        border-bottom: 2px solid var(--accent);
        padding: 8px 20px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
      }

      .logo {
        display: inline-flex;
        align-items: center;
        text-decoration: none;
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--text);
      }

      .logo span { color: var(--accent); }

      .header-sub {
        font-size: 0.7rem;
        color: var(--muted);
      }

      /* ── HERO BANNER ── */
      .hero {
        text-align: center;
        padding: 12px 16px 10px;
      }

      .hero h1 {
        font-size: 1.2rem;
        font-weight: 700;
        margin-bottom: 4px;
      }

      .hero p {
        color: var(--muted);
        font-size: 0.75rem;
        max-width: 480px;
        margin: 0 auto 8px;
      }

      .overall-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 16px;
        border-radius: 50px;
        font-size: 0.85rem;
        font-weight: 700;
        transition: background 0.4s, color 0.4s;
      }

      .overall-badge.all-up   { background: rgba(34,197,94,0.15);  color: var(--green);  border: 1.5px solid var(--green); }
      .overall-badge.has-down { background: rgba(239,68,68,0.15);  color: var(--red);    border: 1.5px solid var(--red); }
      .overall-badge.checking { background: rgba(245,158,11,0.12); color: var(--yellow); border: 1.5px solid var(--yellow); }

      .pulse {
        width: 10px; height: 10px;
        border-radius: 50%;
        display: inline-block;
        animation: pulse 1.6s infinite;
      }
      .all-up   .pulse { background: var(--green); }
      .has-down .pulse { background: var(--red); }
      .checking .pulse { background: var(--yellow); }

      @keyframes pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50%       { opacity: 0.45; transform: scale(1.35); }
      }

      /* ── MAIN CONTENT ── */
      main {
        flex: 1;
        max-width: 950px;
        width: 100%;
        margin: 0 auto;
        padding: 0 16px 20px;
      }

      .section-title {
        font-size: 0.65rem;
        font-weight: 700;
        letter-spacing: 1px;
        text-transform: uppercase;
        color: var(--muted);
        margin: 10px 0 6px;
      }

      .brand-group {
        margin-bottom: 8px;
      }

      .brand-header {
        font-size: 0.8rem;
        font-weight: 700;
        color: var(--accent);
        padding: 4px 0 3px;
        border-bottom: 1px solid var(--border);
        margin-bottom: 5px;
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .brand-header .count {
        font-size: 0.6rem;
        font-weight: 400;
        color: var(--muted);
        background: var(--border);
        padding: 1px 8px;
        border-radius: 10px;
      }

      .status-list {
        display: flex;
        flex-direction: column;
        gap: 3px;
      }

      .status-card {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 4px 10px;
        display: flex;
        align-items: center;
        gap: 8px;
        transition: border-color 0.3s, box-shadow 0.3s;
        text-decoration: none;
        color: inherit;
        cursor: pointer;
      }

      .status-card:hover {
        box-shadow: 0 2px 10px rgba(0,0,0,0.3);
      }

      .status-card.up   { border-left: 3px solid var(--green); }
      .status-card.down { border-left: 3px solid var(--red); }
      .status-card.loading { border-left: 3px solid var(--yellow); }

      .status-icon {
        font-size: 0.85rem;
        width: 22px;
        text-align: center;
        flex-shrink: 0;
      }
      .up   .status-icon { color: var(--green); }
      .down .status-icon { color: var(--red); }
      .loading .status-icon { color: var(--yellow); }

      .status-info { flex: 1; min-width: 0; }

      .status-name {
        font-size: 0.75rem;
        font-weight: 700;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .status-url {
        font-size: 0.6rem;
        color: var(--muted);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .status-right {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 1px;
        flex-shrink: 0;
      }

      .status-badge {
        font-size: 0.6rem;
        font-weight: 700;
        padding: 1px 8px;
        border-radius: 12px;
        letter-spacing: 0.3px;
      }
      .up   .status-badge { background: rgba(34,197,94,0.18);  color: var(--green); }
      .down .status-badge { background: rgba(239,68,68,0.18);  color: var(--red); }
      .loading .status-badge { background: rgba(245,158,11,0.18); color: var(--yellow); }

      .status-ping {
        font-size: 0.55rem;
        color: var(--muted);
      }

      .controls {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin: 12px 0 0;
        flex-wrap: wrap;
      }

      .last-checked {
        font-size: 0.65rem;
        color: var(--muted);
      }

      .last-checked span { color: var(--text); font-weight: 500; }

      .btn-refresh {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 5px 14px;
        background: var(--accent);
        color: #fff;
        border: none;
        border-radius: 5px;
        font-family: inherit;
        font-size: 0.7rem;
        font-weight: 700;
        cursor: pointer;
        transition: background 0.2s;
      }

      .btn-refresh:hover  { background: #a50000; }
      .btn-refresh.spinning i { animation: spin 0.7s linear infinite; }

      @keyframes spin { to { transform: rotate(360deg); } }

      .refresh-bar-wrap {
        margin-top: 10px;
        background: var(--border);
        border-radius: 3px;
        height: 2px;
        overflow: hidden;
      }

      .refresh-bar {
        height: 100%;
        background: var(--accent);
        width: 100%;
        transform-origin: left;
        transition: transform linear;
      }

      .refresh-note {
        font-size: 0.6rem;
        color: var(--muted);
        margin-top: 3px;
        text-align: right;
      }

      .total-domains {
        font-size: 0.65rem;
        color: var(--muted);
        margin-top: 4px;
        text-align: center;
      }

      footer {
        text-align: center;
        padding: 8px;
        font-size: 0.6rem;
        color: var(--muted);
        border-top: 1px solid var(--border);
      }

      footer a { color: var(--accent); text-decoration: none; }
    </style>
  </head>
  <body>

    <header>
      <div class="logo">IPOS<span>Monitor</span></div>
      <div class="header-sub"><i class="fa fa-circle-dot" style="color:var(--accent)"></i>&nbsp; System Status</div>
    </header>

    <div class="hero">
      <h1>Status Layanan IPOS</h1>
      <p>Pantau kondisi semua domain IPOS secara real-time.</p>
      <div class="overall-badge checking" id="overallBadge">
        <span class="pulse"></span>
        <span id="overallText">Memeriksa...</span>
      </div>
    </div>

    <main>
      <div class="section-title"><i class="fa fa-server"></i>&nbsp; Daftar Domain</div>

      <div id="statusContainer"></div>

      <div class="total-domains" id="totalDomains">Total Domain: 0</div>

      <div class="controls">
        <div class="last-checked">Terakhir dicek: <span id="lastChecked">—</span></div>
        <button class="btn-refresh" id="btnRefresh" onclick="checkAll()">
          <i class="fa fa-rotate-right"></i> Refresh
        </button>
      </div>

      <div class="refresh-bar-wrap">
        <div class="refresh-bar" id="refreshBar"></div>
      </div>
      <div class="refresh-note">Auto-refresh setiap <span id="intervalLabel">15</span> menit</div>
    </main>

    <footer>&copy; 2025 IPOS Monitoring &mdash; <a href="/">Kembali</a></footer>

    <script>
      const SERVICES = {{ services|tojson }};
      const AUTO_REFRESH_SEC = 15 * 60;

      let results = SERVICES.map(() => ({ status: "up", ping: null }));
      let timerID = null;
      let barAnimID = null;

      function renderList() {
        const container = document.getElementById("statusContainer");
        const groups = {};
        SERVICES.forEach((svc, i) => {
          if (!groups[svc.brand]) groups[svc.brand] = [];
          groups[svc.brand].push({ ...svc, index: i });
        });

        let html = '';
        let totalDomains = 0;

        for (const [brand, items] of Object.entries(groups)) {
          totalDomains += items.length;
          html += `<div class="brand-group">`;
          html += `<div class="brand-header"><i class="fa fa-folder-open"></i> ${brand} <span class="count">${items.length}</span></div>`;
          html += `<div class="status-list">`;

          items.forEach((svc) => {
            const r = results[svc.index] || { status: "up", ping: null };
            const cls = r.status || "up";
            const badge = cls === "up" ? "AKTIF" : "IPOS";
            const icon = cls === "up" ? "fa-circle-check" : "fa-circle-xmark";
            const ping = (r.ping !== null && r.ping !== undefined) ? `${r.ping} ms` : "—";

            html += `
              <a class="status-card ${cls}" href="${svc.url}" target="_blank" rel="noopener noreferrer">
                <div class="status-icon"><i class="fa ${icon}"></i></div>
                <div class="status-info">
                  <div class="status-name">${svc.name}</div>
                  <div class="status-url">${svc.url}</div>
                </div>
                <div class="status-right">
                  <span class="status-badge">${badge}</span>
                  <span class="status-ping"><i class="fa fa-bolt" style="font-size:.5rem"></i> ${ping}</span>
                </div>
              </a>`;
          });

          html += `</div></div>`;
        }

        container.innerHTML = html;
        document.getElementById("totalDomains").textContent = `Total Domain: ${totalDomains}`;
      }

      function renderOverall() {
        const badge = document.getElementById("overallBadge");
        const text = document.getElementById("overallText");
        const allUp = results.every(r => r.status === "up");
        const anyDown = results.some(r => r.status === "down");

        if (!anyDown) {
          badge.className = "overall-badge all-up";
          text.textContent = "Semua Domain Normal";
        } else {
          const downCount = results.filter(r => r.status === "down").length;
          badge.className = "overall-badge has-down";
          text.textContent = `${downCount} Domain Bermasalah (IPOS)`;
        }
      }

      async function checkAll() {
        results = SERVICES.map(() => ({ status: "up", ping: null }));
        renderList();
        renderOverall();

        const btn = document.getElementById("btnRefresh");
        btn.classList.add("spinning");

        const now = new Date();
        document.getElementById("lastChecked").textContent =
          now.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

        // Reset auto-refresh bar
        startProgressBar();

        btn.classList.remove("spinning");
      }

      function startProgressBar() {
        clearTimeout(timerID);
        if (barAnimID) cancelAnimationFrame(barAnimID);

        const bar = document.getElementById("refreshBar");
        const total = AUTO_REFRESH_SEC * 1000;
        const start = performance.now();

        function tick(now) {
          const elapsed = now - start;
          const pct = Math.min(elapsed / total, 1);
          bar.style.transform = `scaleX(${1 - pct})`;
          if (pct < 1) {
            barAnimID = requestAnimationFrame(tick);
          }
        }
        barAnimID = requestAnimationFrame(tick);

        timerID = setTimeout(() => {
          checkAll();
        }, total);
      }

      document.getElementById("intervalLabel").textContent = "15";
      renderList();
      renderOverall();
      startProgressBar();
    </script>
  </body>
</html>
'''

# --- ENDPOINT UTAMA ---
LAST_RUN_TIME = None
LAST_LOG_OUTPUT = "Sistem baru menyala. Memuat data patroli..."
IS_RUNNING = False

@app.route('/')
def status_page():
    """Halaman utama dengan tampilan status IPOS"""
    all_domains = get_all_domains()
    return render_template_string(HTML_TEMPLATE, services=all_domains)

@app.route('/api/domains')
def api_domains():
    """API untuk mendapatkan daftar semua domain dari KV"""
    all_domains = get_all_domains()
    return Response(json.dumps(all_domains), mimetype='application/json')

@app.route('/jalankan-patroli', methods=['GET', 'HEAD'])
def endpoint_patroli():
    """Endpoint untuk menjalankan patroli Nawala"""
    hantu_chrome = request.headers.get('Purpose') == 'prefetch' or request.headers.get('Sec-Fetch-Purpose') == 'prefetch'
    if request.method == 'HEAD' or hantu_chrome:
        return Response("", status=200)

    global LAST_RUN_TIME, LAST_LOG_OUTPUT, IS_RUNNING
    sekarang = datetime.now()

    if IS_RUNNING:
        return Response("""
        <html>
            <head>
                <meta http-equiv="refresh" content="4">
                <title>MEMPROSES...</title>
            </head>
            <body style="background:#1e1e1e; color:#00ff00; font-family:monospace; text-align:center; padding-top:100px;">
                <h2>⚙️ ROBOT SEDANG KELILING PATROLI...</h2>
                <p style="color:#888;">Mohon tunggu sekitar 10 detik. Layar akan otomatis memuat hasil log yang bersih.</p>
            </body>
        </html>
        """, mimetype='text/html')

    if LAST_RUN_TIME and (sekarang - LAST_RUN_TIME).total_seconds() < 800:
        time_passed = int((sekarang - LAST_RUN_TIME).total_seconds())
        hasil_log = LAST_LOG_OUTPUT
        status_teks = "🟢 LIVE MONITORING ACTIVE"
        warna_status = "#00ff00"
    else:
        IS_RUNNING = True
        try:
            LAST_RUN_TIME = sekarang
            hasil_cek_baru = run_api_check()
            LAST_LOG_OUTPUT = hasil_cek_baru
            hasil_log = hasil_cek_baru
        finally:
            IS_RUNNING = False
        time_passed = 0
        status_teks = "🟢 LIVE MONITORING ACTIVE"
        warna_status = "#00ff00"

    return Response(f"""
    <html>
        <head>
            <meta http-equiv="refresh" content="{900 - time_passed if time_passed < 900 else 900}">
            <title>LIVE MONITORING - SATPAM NAWALA</title>
            <style>
                body {{ background:#1e1e1e; color:#00ff00; font-family:monospace; margin:0; padding:20px; }}
                .header-box {{ border-bottom: 1px dashed #444; padding-bottom: 15px; margin-bottom: 15px; }}
                .title-bar {{ display: flex; justify-content: space-between; color: #888; font-size: 14px; margin-bottom: 10px; }}
                .progress-bg {{ background: #333; width: 100%; height: 6px; border-radius: 3px; overflow: hidden; }}
                .progress-fill {{ background: #00ff00; height: 100%; width: 0%; box-shadow: 0 0 10px #00ff00; transition: width 1s linear; }}
                .timer {{ color: #00ff00; font-weight: bold; }}
                .status-badge {{ color: {warna_status}; font-weight:bold; }}
            </style>
        </head>
        <body>
            <div class="header-box">
                <div class="title-bar">
                    <div class="status-badge">{status_teks} | Interval: 15 Menit</div>
                    <div class="timer" id="countdown-text">Memuat...</div>
                </div>
                <div class="progress-bg">
                    <div class="progress-fill" id="progress-bar"></div>
                </div>
            </div>
            
            <pre style="color:#00ff00; font-family:monospace; font-size:14px; white-space:pre-wrap;">{hasil_log}</pre>

            <script>
                let totalSeconds = 900; 
                let timePassed = {time_passed}; 
                
                function updateTimer() {{
                    if (timePassed > totalSeconds) timePassed = totalSeconds;
                    
                    let timeLeft = totalSeconds - timePassed;
                    let percentage = (timePassed / totalSeconds) * 100;
                    
                    document.getElementById('progress-bar').style.width = percentage + '%';
                    
                    let m = Math.floor(timeLeft / 60);
                    let s = timeLeft % 60;
                    let s_display = s < 10 ? "0" + s : s;
                    
                    document.getElementById('countdown-text').innerText = "Patroli Berikutnya: " + m + ":" + s_display + " (" + percentage.toFixed(1) + "%)";
                    
                    timePassed++;
                }}
                
                updateTimer(); 
                setInterval(updateTimer, 1000); 
            </script>
        </body>
    </html>
    """, mimetype='text/html')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
