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

# --- GET ALL DOMAINS FROM KV ---
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

# --- HTML TEMPLATE (MODERN DENGAN ANIMASI TIMER) ---
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="id">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Status — IPOS Monitoring</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" />
    <style>
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

      :root {
        --bg: #0d0d0d;
        --card: #1a1a1a;
        --border: #2a2a2a;
        --accent: #e50914;
        --green: #00e676;
        --red: #ff1744;
        --yellow: #ffea00;
        --text: #f5f5f5;
        --muted: #888;
        --glow-green: 0 0 20px rgba(0, 230, 118, 0.3);
        --glow-red: 0 0 20px rgba(255, 23, 68, 0.3);
      }

      html, body {
        height: 100%;
        margin: 0;
        padding: 0;
        background: var(--bg);
        color: var(--text);
        font-family: "Inter", -apple-system, sans-serif;
        overflow: hidden;
      }

      .app-container {
        display: flex;
        flex-direction: column;
        height: 100vh;
        max-height: 100vh;
        padding: 12px 24px 10px;
        overflow: hidden;
      }

      /* ── HEADER ── */
      header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 0 6px;
        border-bottom: 2px solid var(--accent);
        flex-shrink: 0;
      }

      .logo {
        font-size: 1.6rem;
        font-weight: 900;
        letter-spacing: -0.5px;
        background: linear-gradient(135deg, #fff 30%, var(--accent) 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
      }

      .logo i {
        -webkit-text-fill-color: var(--accent);
        margin-right: 6px;
      }

      .header-status {
        font-size: 0.75rem;
        color: var(--muted);
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .header-status .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        display: inline-block;
        animation: pulse-dot 1.5s infinite;
      }

      .header-status .dot.green { background: var(--green); box-shadow: var(--glow-green); }
      .header-status .dot.red { background: var(--red); box-shadow: var(--glow-red); }

      @keyframes pulse-dot {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.4; transform: scale(0.8); }
      }

      /* ── HERO ── */
      .hero {
        text-align: center;
        padding: 10px 0 6px;
        flex-shrink: 0;
      }

      .hero h1 {
        font-size: 1.4rem;
        font-weight: 800;
        letter-spacing: -0.5px;
      }

      .hero p {
        color: var(--muted);
        font-size: 0.8rem;
        margin-top: 2px;
      }

      /* ── OVERALL BADGE ── */
      .overall-badge {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        padding: 6px 22px;
        border-radius: 50px;
        font-size: 0.95rem;
        font-weight: 700;
        margin-top: 4px;
        transition: all 0.4s;
      }

      .overall-badge.all-up {
        background: rgba(0, 230, 118, 0.12);
        color: var(--green);
        border: 1.5px solid var(--green);
        box-shadow: var(--glow-green);
      }
      .overall-badge.has-down {
        background: rgba(255, 23, 68, 0.12);
        color: var(--red);
        border: 1.5px solid var(--red);
        box-shadow: var(--glow-red);
      }

      .overall-badge .pulse-icon {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
        animation: pulse-badge 1.2s ease-in-out infinite;
      }
      .all-up .pulse-icon { background: var(--green); }
      .has-down .pulse-icon { background: var(--red); }

      @keyframes pulse-badge {
        0%, 100% { transform: scale(1); opacity: 1; }
        50% { transform: scale(1.4); opacity: 0.6; }
      }

      /* ── MAIN CONTENT ── */
      main {
        flex: 1;
        min-height: 0;
        display: flex;
        flex-direction: column;
        padding: 6px 0 0;
        overflow: hidden;
      }

      .section-title {
        font-size: 0.65rem;
        font-weight: 700;
        letter-spacing: 1.5px;
        text-transform: uppercase;
        color: var(--muted);
        flex-shrink: 0;
        padding-bottom: 4px;
      }

      .status-scroll {
        flex: 1;
        overflow-y: auto;
        padding-right: 4px;
      }

      .status-scroll::-webkit-scrollbar {
        width: 4px;
      }
      .status-scroll::-webkit-scrollbar-track {
        background: var(--border);
        border-radius: 4px;
      }
      .status-scroll::-webkit-scrollbar-thumb {
        background: var(--accent);
        border-radius: 4px;
      }

      .brand-group {
        margin-bottom: 6px;
      }

      .brand-header {
        font-size: 0.8rem;
        font-weight: 700;
        color: var(--accent);
        padding: 3px 0 2px;
        border-bottom: 1px solid var(--border);
        margin-bottom: 4px;
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
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 4px;
      }

      .status-card {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 4px 10px;
        display: flex;
        align-items: center;
        gap: 8px;
        text-decoration: none;
        color: inherit;
        transition: all 0.25s ease;
        cursor: pointer;
        min-height: 32px;
      }

      .status-card:hover {
        border-color: var(--accent);
        transform: translateY(-1px);
        box-shadow: 0 4px 16px rgba(0,0,0,0.5);
      }

      .status-card.up { border-left: 3px solid var(--green); }
      .status-card.down { border-left: 3px solid var(--red); }

      .status-icon {
        font-size: 0.8rem;
        width: 20px;
        text-align: center;
        flex-shrink: 0;
      }
      .up .status-icon { color: var(--green); }
      .down .status-icon { color: var(--red); }

      .status-info {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
      }

      .status-name {
        font-size: 0.7rem;
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .status-url {
        font-size: 0.55rem;
        color: var(--muted);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .status-right {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        flex-shrink: 0;
        gap: 1px;
      }

      .status-badge {
        font-size: 0.55rem;
        font-weight: 700;
        padding: 1px 8px;
        border-radius: 10px;
        letter-spacing: 0.3px;
      }
      .up .status-badge {
        background: rgba(0, 230, 118, 0.15);
        color: var(--green);
      }
      .down .status-badge {
        background: rgba(255, 23, 68, 0.15);
        color: var(--red);
      }

      /* ── FOOTER CONTROLS ── */
      .footer-controls {
        flex-shrink: 0;
        padding-top: 6px;
        border-top: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
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
        padding: 5px 16px;
        background: var(--accent);
        color: #fff;
        border: none;
        border-radius: 6px;
        font-family: inherit;
        font-size: 0.7rem;
        font-weight: 700;
        cursor: pointer;
        transition: all 0.25s;
      }
      .btn-refresh:hover {
        background: #b71c1c;
        transform: scale(1.03);
      }
      .btn-refresh.spinning i { animation: spin 0.7s linear infinite; }

      @keyframes spin { to { transform: rotate(360deg); } }

      /* ── TIMER CIRCLE ── */
      .timer-wrapper {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-shrink: 0;
      }

      .timer-circle {
        position: relative;
        width: 36px;
        height: 36px;
      }

      .timer-circle svg {
        transform: rotate(-90deg);
        width: 36px;
        height: 36px;
      }

      .timer-circle .bg {
        fill: none;
        stroke: var(--border);
        stroke-width: 3;
      }

      .timer-circle .progress {
        fill: none;
        stroke: var(--accent);
        stroke-width: 3;
        stroke-linecap: round;
        transition: stroke-dashoffset 0.3s linear;
      }

      .timer-text {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        font-size: 0.55rem;
        font-weight: 700;
        color: var(--text);
        font-variant-numeric: tabular-nums;
      }

      .timer-label {
        font-size: 0.6rem;
        color: var(--muted);
      }

      .total-domains {
        font-size: 0.6rem;
        color: var(--muted);
        flex-shrink: 0;
      }

      /* ── RESPONSIVE ── */
      @media (max-width: 640px) {
        .app-container { padding: 8px 12px 6px; }
        .logo { font-size: 1.2rem; }
        .hero h1 { font-size: 1.1rem; }
        .status-list { grid-template-columns: 1fr; }
        .timer-wrapper { gap: 6px; }
        .timer-circle { width: 30px; height: 30px; }
        .timer-circle svg { width: 30px; height: 30px; }
        .header-status { font-size: 0.6rem; }
        .overall-badge { font-size: 0.75rem; padding: 4px 14px; }
      }

      @media (min-width: 1024px) {
        .status-list { grid-template-columns: 1fr 1fr 1fr; }
      }
    </style>
  </head>
  <body>
    <div class="app-container">
      <!-- HEADER -->
      <header>
        <div class="logo"><i class="fas fa-shield-halved"></i>IPOS<span style="-webkit-text-fill-color:var(--accent);">Monitor</span></div>
        <div class="header-status">
          <span class="dot green" id="statusDot"></span>
          <span id="statusLabel">Online</span>
        </div>
      </header>

      <!-- HERO -->
      <div class="hero">
        <h1>Status Layanan IPOS</h1>
        <p>Pantau kondisi semua domain secara real-time</p>
        <div class="overall-badge all-up" id="overallBadge">
          <span class="pulse-icon"></span>
          <span id="overallText">Semua Domain Normal</span>
        </div>
      </div>

      <!-- MAIN -->
      <main>
        <div class="section-title"><i class="fas fa-server"></i>&nbsp; Daftar Domain</div>
        <div class="status-scroll" id="statusContainer"></div>
      </main>

      <!-- FOOTER CONTROLS -->
      <div class="footer-controls">
        <div class="last-checked">
          <i class="fas fa-clock"></i>&nbsp; <span id="lastChecked">—</span>
        </div>

        <div class="timer-wrapper">
          <div class="timer-circle">
            <svg viewBox="0 0 36 36">
              <circle class="bg" cx="18" cy="18" r="15.5" />
              <circle class="progress" id="timerProgress" cx="18" cy="18" r="15.5"
                stroke-dasharray="97.39"
                stroke-dashoffset="0" />
            </svg>
            <span class="timer-text" id="timerText">15:00</span>
          </div>
          <span class="timer-label">Auto-refresh</span>
        </div>

        <div class="total-domains" id="totalDomains">Total: 0</div>

        <button class="btn-refresh" id="btnRefresh" onclick="checkAll()">
          <i class="fas fa-sync"></i> Refresh
        </button>
      </div>
    </div>

    <script>
      const SERVICES = {{ services|tojson }};
      const AUTO_REFRESH_SEC = 15 * 60;

      let results = SERVICES.map(() => ({ status: "up" }));
      let timerID = null;
      let animID = null;
      let timeLeft = AUTO_REFRESH_SEC;

      // ── RENDER ──
      function renderList() {
        const container = document.getElementById("statusContainer");
        const groups = {};
        SERVICES.forEach((svc, i) => {
          if (!groups[svc.brand]) groups[svc.brand] = [];
          groups[svc.brand].push({ ...svc, index: i });
        });

        let html = '';
        let total = 0;

        for (const [brand, items] of Object.entries(groups)) {
          total += items.length;
          html += `<div class="brand-group">`;
          html += `<div class="brand-header"><i class="fas fa-folder"></i> ${brand} <span class="count">${items.length}</span></div>`;
          html += `<div class="status-list">`;

          items.forEach((svc) => {
            const r = results[svc.index] || { status: "up" };
            const cls = r.status || "up";
            const badge = cls === "up" ? "AKTIF" : "IPOS";
            const icon = cls === "up" ? "fa-circle-check" : "fa-circle-xmark";

            html += `
              <a class="status-card ${cls}" href="${svc.url}" target="_blank" rel="noopener noreferrer">
                <div class="status-icon"><i class="fas ${icon}"></i></div>
                <div class="status-info">
                  <div class="status-name">${svc.name}</div>
                  <div class="status-url">${svc.url}</div>
                </div>
                <div class="status-right">
                  <span class="status-badge">${badge}</span>
                </div>
              </a>`;
          });

          html += `</div></div>`;
        }

        container.innerHTML = html;
        document.getElementById("totalDomains").textContent = `Total: ${total}`;
      }

      // ── OVERALL STATUS ──
      function renderOverall() {
        const badge = document.getElementById("overallBadge");
        const text = document.getElementById("overallText");
        const allUp = results.every(r => r.status === "up");
        const anyDown = results.some(r => r.status === "down");
        const dot = document.getElementById("statusDot");
        const label = document.getElementById("statusLabel");

        if (!anyDown) {
          badge.className = "overall-badge all-up";
          text.textContent = "Semua Domain Normal";
          dot.className = "dot green";
          label.textContent = "All Systems Go";
        } else {
          const downCount = results.filter(r => r.status === "down").length;
          badge.className = "overall-badge has-down";
          text.textContent = `${downCount} Domain Bermasalah (IPOS)`;
          dot.className = "dot red";
          label.textContent = `${downCount} Issue(s)`;
        }
      }

      // ── TIMER ──
      function updateTimer() {
        const progress = document.getElementById("timerProgress");
        const text = document.getElementById("timerText");
        const circumference = 97.39;
        const offset = circumference * (1 - timeLeft / AUTO_REFRESH_SEC);
        progress.style.strokeDashoffset = offset;

        const mins = Math.floor(timeLeft / 60);
        const secs = Math.floor(timeLeft % 60);
        text.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

        if (timeLeft <= 0) {
          checkAll();
          timeLeft = AUTO_REFRESH_SEC;
        }
      }

      function startTimer() {
        if (timerID) clearInterval(timerID);
        timerID = setInterval(() => {
          timeLeft--;
          updateTimer();
        }, 1000);
        updateTimer();
      }

      // ── CHECK ALL ──
      async function checkAll() {
        const btn = document.getElementById("btnRefresh");
        btn.classList.add("spinning");

        results = SERVICES.map(() => ({ status: "up" }));
        renderList();
        renderOverall();

        const now = new Date();
        document.getElementById("lastChecked").textContent =
          now.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

        timeLeft = AUTO_REFRESH_SEC;
        updateTimer();

        btn.classList.remove("spinning");
      }

      // ── INIT ──
      renderList();
      renderOverall();
      startTimer();
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
    all_domains = get_all_domains()
    return render_template_string(HTML_TEMPLATE, services=all_domains)

@app.route('/api/domains')
def api_domains():
    all_domains = get_all_domains()
    return Response(json.dumps(all_domains), mimetype='application/json')

@app.route('/jalankan-patroli', methods=['GET', 'HEAD'])
def endpoint_patroli():
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
