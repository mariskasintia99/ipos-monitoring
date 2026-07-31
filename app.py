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

# Store IPOS domains from last patrol
IPOS_DOMAINS = []
LAST_PATROL_RESULT = "No patrol run yet"

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

# --- CHECK URL STATUS WITH PING ---
def check_service_status(url):
    try:
        start = time.time()
        response = requests.head(url, timeout=10, allow_redirects=True)
        ping_ms = int((time.time() - start) * 1000)
        if response.status_code < 400:
            return {"status": "active", "ping": ping_ms}
        return {"status": "active", "ping": None}  # Always active if in KV
    except:
        return {"status": "active", "ping": None}  # Always active if in KV

# --- MESIN UTAMA (MULTI-KEY & AUTO FAILOVER) ---
def run_api_check():
    global log_buffer, IPOS_DOMAINS, LAST_PATROL_RESULT
    log_buffer = "" 
    IPOS_DOMAINS = []
    
    log("SYSTEM", "Memulai pengecekan Nawala (Mode Auto-Cadangan API)...")

    ada_perubahan = False
    global_report = []
    all_removed = []

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
                all_removed.append(d)
                log("WARN", f"🔴 STATUS: IPOS ➜ {d} [AUTO DELETE]")
            else:
                active.append(d)
                log("SUCCESS", f"🟢 STATUS: AMAN ➜ {d}")
                
        if removed:
            update_kv(target['key'], active)
            ada_perubahan = True
            
        global_report.append({"name": target["name"], "active": active, "removed": removed})

    # Save IPOS domains for display
    IPOS_DOMAINS = all_removed
    
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
        LAST_PATROL_RESULT = f"Patrol completed at {datetime.now(timezone.utc) + timedelta(hours=7):%H:%M:%S} - {len(all_removed)} domains removed"
    else:
        LAST_PATROL_RESULT = f"Patrol completed at {datetime.now(timezone.utc) + timedelta(hours=7):%H:%M:%S} - No IPOS domains found"

    log("SUCCESS", "Pengecekan Nawala Selesai!")
    return log_buffer

# --- HTML TEMPLATE (ENGLISH, WITH PING, IPOS STATUS FROM KV) ---
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>IPOS Monitoring</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" />
    <style>
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

      body {
        font-family: "Inter", -apple-system, sans-serif;
        height: 100vh;
        overflow: hidden;
        background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
        background-size: 400% 400%;
        animation: gradientBG 15s ease infinite;
        color: #fff;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 16px;
      }

      @keyframes gradientBG {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
      }

      .particles {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 0;
        overflow: hidden;
      }

      .particle {
        position: absolute;
        width: 6px;
        height: 6px;
        background: rgba(255, 255, 255, 0.08);
        border-radius: 50%;
        animation: float linear infinite;
      }

      @keyframes float {
        0% { transform: translateY(100vh) scale(0); opacity: 0; }
        10% { opacity: 1; }
        90% { opacity: 1; }
        100% { transform: translateY(-10vh) scale(1); opacity: 0; }
      }

      .app-container {
        position: relative;
        z-index: 1;
        width: 100%;
        max-width: 1200px;
        height: 100%;
        max-height: 800px;
        background: rgba(255, 255, 255, 0.06);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-radius: 24px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        padding: 20px 28px 16px;
        display: flex;
        flex-direction: column;
        box-shadow: 0 25px 60px rgba(0,0,0,0.6);
      }

      /* ── HEADER ── */
      header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-shrink: 0;
        padding-bottom: 10px;
        border-bottom: 2px solid rgba(255,255,255,0.08);
      }

      .logo {
        font-size: 1.6rem;
        font-weight: 900;
        letter-spacing: -0.5px;
        background: linear-gradient(135deg, #fff 30%, #f093fb 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
      }

      .logo i {
        -webkit-text-fill-color: #f093fb;
        margin-right: 8px;
      }

      .header-status {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 0.85rem;
        color: rgba(255,255,255,0.6);
      }

      .status-dot {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        display: inline-block;
        animation: pulse-dot 1.5s ease-in-out infinite;
      }

      .status-dot.green { background: #00e676; box-shadow: 0 0 20px rgba(0, 230, 118, 0.4); }
      .status-dot.red { background: #ff1744; box-shadow: 0 0 20px rgba(255, 23, 68, 0.4); }

      @keyframes pulse-dot {
        0%, 100% { transform: scale(1); opacity: 1; }
        50% { transform: scale(1.3); opacity: 0.6; }
      }

      /* ── HERO ── */
      .hero {
        text-align: center;
        flex-shrink: 0;
        padding: 8px 0 4px;
      }

      .hero h1 {
        font-size: 1.4rem;
        font-weight: 800;
        letter-spacing: -0.5px;
      }

      .hero p {
        color: rgba(255,255,255,0.4);
        font-size: 0.8rem;
        margin-top: 2px;
      }

      .overall-badge {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        padding: 6px 24px;
        border-radius: 50px;
        font-size: 1rem;
        font-weight: 700;
        margin-top: 4px;
        transition: all 0.4s;
        cursor: default;
      }

      .overall-badge.all-up {
        background: rgba(0, 230, 118, 0.12);
        color: #00e676;
        border: 1.5px solid rgba(0, 230, 118, 0.25);
        box-shadow: 0 0 30px rgba(0, 230, 118, 0.08);
      }
      .overall-badge.has-ipos {
        background: rgba(255, 23, 68, 0.12);
        color: #ff1744;
        border: 1.5px solid rgba(255, 23, 68, 0.25);
        box-shadow: 0 0 30px rgba(255, 23, 68, 0.08);
      }

      .overall-badge .pulse-icon {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
        animation: pulse-badge 1.2s ease-in-out infinite;
      }
      .all-up .pulse-icon { background: #00e676; }
      .has-ipos .pulse-icon { background: #ff1744; }

      @keyframes pulse-badge {
        0%, 100% { transform: scale(1); opacity: 1; }
        50% { transform: scale(1.5); opacity: 0.5; }
      }

      /* ── MAIN ── */
      main {
        flex: 1;
        min-height: 0;
        display: flex;
        flex-direction: column;
        padding-top: 6px;
        overflow: hidden;
      }

      .section-title {
        font-size: 0.65rem;
        font-weight: 700;
        letter-spacing: 2px;
        text-transform: uppercase;
        color: rgba(255,255,255,0.25);
        flex-shrink: 0;
        padding-bottom: 4px;
      }

      .status-scroll {
        flex: 1;
        overflow-y: auto;
        padding-right: 6px;
      }

      .status-scroll::-webkit-scrollbar {
        width: 4px;
      }
      .status-scroll::-webkit-scrollbar-track {
        background: rgba(255,255,255,0.05);
        border-radius: 4px;
      }
      .status-scroll::-webkit-scrollbar-thumb {
        background: rgba(255,255,255,0.2);
        border-radius: 4px;
      }

      .brand-group {
        margin-bottom: 10px;
      }

      .brand-header {
        font-size: 1.1rem;
        font-weight: 700;
        color: #f093fb;
        padding: 4px 0 3px;
        border-bottom: 1px solid rgba(255,255,255,0.06);
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        gap: 12px;
      }

      .brand-header .count {
        font-size: 0.7rem;
        font-weight: 400;
        color: rgba(255,255,255,0.3);
        background: rgba(255,255,255,0.06);
        padding: 1px 12px;
        border-radius: 12px;
      }

      .status-list {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }

      .status-card {
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 8px;
        padding: 8px 16px;
        display: flex;
        align-items: center;
        gap: 14px;
        text-decoration: none;
        color: #fff;
        transition: all 0.25s ease;
        cursor: pointer;
        min-height: 44px;
      }

      .status-card:hover {
        background: rgba(255, 255, 255, 0.08);
        border-color: rgba(255, 255, 255, 0.15);
        transform: translateX(4px);
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
      }

      .status-card.active { border-left: 4px solid #00e676; }
      .status-card.ipos { border-left: 4px solid #ff1744; }

      .status-icon {
        font-size: 1rem;
        width: 24px;
        text-align: center;
        flex-shrink: 0;
      }
      .active .status-icon { color: #00e676; }
      .ipos .status-icon { color: #ff1744; }

      .status-info {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        gap: 16px;
        flex-wrap: wrap;
      }

      .status-name {
        font-size: 0.95rem;
        font-weight: 700;
        white-space: nowrap;
      }

      .status-url {
        font-size: 0.7rem;
        color: rgba(255,255,255,0.3);
        white-space: nowrap;
      }

      .status-right {
        display: flex;
        align-items: center;
        gap: 14px;
        flex-shrink: 0;
      }

      .status-ping {
        font-size: 0.7rem;
        color: rgba(255,255,255,0.4);
        font-weight: 600;
        min-width: 60px;
        text-align: right;
        transition: all 0.5s ease;
      }
      .status-ping .fa-bolt {
        color: #f093fb;
        margin-right: 4px;
      }
      .status-ping.updating {
        animation: ping-flash 0.3s ease;
      }
      @keyframes ping-flash {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.3; }
      }

      .status-badge {
        font-size: 0.7rem;
        font-weight: 700;
        padding: 3px 16px;
        border-radius: 20px;
        letter-spacing: 0.5px;
      }
      .active .status-badge {
        background: rgba(0, 230, 118, 0.12);
        color: #00e676;
      }
      .ipos .status-badge {
        background: rgba(255, 23, 68, 0.12);
        color: #ff1744;
      }

      /* ── FOOTER ── */
      .footer-controls {
        flex-shrink: 0;
        padding-top: 10px;
        border-top: 1px solid rgba(255,255,255,0.06);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        flex-wrap: wrap;
      }

      .last-checked {
        font-size: 0.8rem;
        color: rgba(255,255,255,0.4);
      }
      .last-checked span { color: rgba(255,255,255,0.7); font-weight: 500; }

      .timer-wrapper {
        display: flex;
        align-items: center;
        gap: 10px;
      }

      .timer-circle {
        position: relative;
        width: 40px;
        height: 40px;
      }

      .timer-circle svg {
        transform: rotate(-90deg);
        width: 40px;
        height: 40px;
      }

      .timer-circle .bg {
        fill: none;
        stroke: rgba(255,255,255,0.08);
        stroke-width: 3;
      }

      .timer-circle .progress {
        fill: none;
        stroke: #f093fb;
        stroke-width: 3;
        stroke-linecap: round;
        transition: stroke-dashoffset 0.3s linear;
      }

      .timer-text {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        font-size: 0.6rem;
        font-weight: 700;
        color: #fff;
        font-variant-numeric: tabular-nums;
      }

      .timer-label {
        font-size: 0.65rem;
        color: rgba(255,255,255,0.3);
      }

      .total-domains {
        font-size: 0.85rem;
        color: rgba(255,255,255,0.4);
        font-weight: 600;
      }

      .btn-refresh {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 7px 20px;
        background: linear-gradient(135deg, #f093fb, #f5576c);
        color: #fff;
        border: none;
        border-radius: 8px;
        font-family: inherit;
        font-size: 0.75rem;
        font-weight: 700;
        cursor: pointer;
        transition: all 0.3s;
      }
      .btn-refresh:hover {
        transform: scale(1.04);
        box-shadow: 0 8px 30px rgba(245, 87, 108, 0.3);
      }
      .btn-refresh.spinning i { animation: spin 0.7s linear infinite; }

      @keyframes spin { to { transform: rotate(360deg); } }

      .patrol-result {
        font-size: 0.7rem;
        color: rgba(255,255,255,0.3);
        text-align: center;
        padding-top: 4px;
        flex-shrink: 0;
      }

      /* ── RESPONSIVE ── */
      @media (max-width: 768px) {
        .app-container { padding: 14px 16px 12px; max-height: 100vh; border-radius: 16px; }
        .logo { font-size: 1.2rem; }
        .hero h1 { font-size: 1.1rem; }
        .brand-header { font-size: 0.9rem; }
        .status-name { font-size: 0.8rem; }
        .status-url { font-size: 0.6rem; }
        .status-ping { font-size: 0.6rem; min-width: 50px; }
        .status-card { padding: 6px 12px; min-height: 38px; gap: 10px; }
        .status-info { gap: 8px; }
        .status-badge { font-size: 0.6rem; padding: 2px 12px; }
        .overall-badge { font-size: 0.8rem; padding: 4px 16px; }
        .footer-controls { gap: 8px; }
        .last-checked { font-size: 0.65rem; }
        .total-domains { font-size: 0.7rem; }
        .btn-refresh { font-size: 0.65rem; padding: 5px 14px; }
        .timer-circle { width: 34px; height: 34px; }
        .timer-circle svg { width: 34px; height: 34px; }
        .timer-text { font-size: 0.5rem; }
        .timer-label { font-size: 0.55rem; }
        .header-status { font-size: 0.7rem; }
        .status-right { gap: 8px; }
        .patrol-result { font-size: 0.6rem; }
      }

      @media (max-width: 480px) {
        .app-container { padding: 10px 10px 8px; border-radius: 12px; }
        .logo { font-size: 1rem; }
        .hero h1 { font-size: 0.95rem; }
        .hero p { font-size: 0.65rem; }
        .brand-header { font-size: 0.75rem; }
        .status-name { font-size: 0.7rem; }
        .status-url { font-size: 0.5rem; display: none; }
        .status-ping { font-size: 0.5rem; min-width: 40px; }
        .status-card { padding: 5px 10px; min-height: 32px; gap: 8px; }
        .status-icon { font-size: 0.8rem; width: 20px; }
        .status-badge { font-size: 0.5rem; padding: 1px 10px; }
        .overall-badge { font-size: 0.65rem; padding: 3px 12px; gap: 6px; }
        .footer-controls { gap: 6px; }
        .last-checked { font-size: 0.55rem; }
        .total-domains { font-size: 0.6rem; }
        .btn-refresh { font-size: 0.55rem; padding: 4px 10px; gap: 4px; }
        .timer-circle { width: 28px; height: 28px; }
        .timer-circle svg { width: 28px; height: 28px; }
        .timer-text { font-size: 0.45rem; }
        .timer-label { font-size: 0.5rem; }
        .header-status { font-size: 0.6rem; }
        .status-dot { width: 8px; height: 8px; }
        .status-right { gap: 6px; }
        .patrol-result { font-size: 0.5rem; }
      }
    </style>
  </head>
  <body>

    <!-- Floating Particles -->
    <div class="particles" id="particles"></div>

    <div class="app-container">
      <!-- HEADER -->
      <header>
        <div class="logo"><i class="fas fa-shield-halved"></i>IPOS<span style="-webkit-text-fill-color:#f093fb;">Monitoring</span></div>
        <div class="header-status">
          <span class="status-dot green" id="statusDot"></span>
          <span id="statusLabel">Monitoring Active</span>
        </div>
      </header>

      <!-- HERO -->
      <div class="hero">
        <h1>IPOS Service Status</h1>
        <p>Real-time monitoring of all domains</p>
        <div class="overall-badge all-up" id="overallBadge">
          <span class="pulse-icon"></span>
          <span id="overallText">All Domains Normal</span>
        </div>
      </div>

      <!-- MAIN -->
      <main>
        <div class="section-title"><i class="fas fa-server"></i>&nbsp; Domain List</div>
        <div class="status-scroll" id="statusContainer"></div>
      </main>

      <!-- FOOTER -->
      <div class="footer-controls">
        <div class="last-checked">
          <i class="fas fa-clock"></i>&nbsp; Last Checked: <span id="lastChecked">—</span>
        </div>

        <div class="timer-wrapper">
          <div class="timer-circle">
            <svg viewBox="0 0 40 40">
              <circle class="bg" cx="20" cy="20" r="17" />
              <circle class="progress" id="timerProgress" cx="20" cy="20" r="17"
                stroke-dasharray="106.81"
                stroke-dashoffset="0" />
            </svg>
            <span class="timer-text" id="timerText">15:00</span>
          </div>
          <span class="timer-label">Auto-refresh</span>
        </div>

        <div class="total-domains" id="totalDomains">Total Domains: 0</div>

        <button class="btn-refresh" id="btnRefresh" onclick="refreshAll()">
          <i class="fas fa-sync"></i> Refresh Status
        </button>
      </div>
      <div class="patrol-result" id="patrolResult">Last patrol: Not run yet</div>
    </div>

    <script>
      // ── PARTICLES ──
      (function createParticles() {
        const container = document.getElementById('particles');
        const count = 25;
        for (let i = 0; i < count; i++) {
          const particle = document.createElement('div');
          particle.className = 'particle';
          particle.style.left = Math.random() * 100 + '%';
          particle.style.width = (Math.random() * 4 + 2) + 'px';
          particle.style.height = particle.style.width;
          particle.style.animationDuration = (Math.random() * 15 + 10) + 's';
          particle.style.animationDelay = (Math.random() * 15) + 's';
          particle.style.opacity = Math.random() * 0.3 + 0.05;
          container.appendChild(particle);
        }
      })();

      // ── DATA ──
      const SERVICES = {{ services|tojson }};
      const IPOS_DOMAINS = {{ ipos_domains|tojson }};
      const AUTO_REFRESH_SEC = 15 * 60;

      let results = SERVICES.map(() => ({ status: "active", ping: null }));
      let timerID = null;
      let timeLeft = AUTO_REFRESH_SEC;
      let isUpdating = false;

      // ── CHECK SINGLE SERVICE (with ping) ──
      async function checkOne(url) {
        const start = Date.now();
        try {
          const response = await fetch(url, {
            method: "HEAD",
            mode: "no-cors",
            cache: "no-store",
            signal: AbortSignal.timeout(10000),
          });
          return { status: "active", ping: Date.now() - start };
        } catch {
          return { status: "active", ping: null };
        }
      }

      // ── RENDER ──
      function renderList() {
        const container = document.getElementById("statusContainer");
        const groups = {};
        
        // Build IPOS set for quick lookup
        const iposSet = new Set(IPOS_DOMAINS.map(d => d.toLowerCase()));
        
        SERVICES.forEach((svc, i) => {
          if (!groups[svc.brand]) groups[svc.brand] = [];
          const isIpos = iposSet.has(svc.name.toLowerCase());
          groups[svc.brand].push({ ...svc, index: i, isIpos: isIpos });
        });

        let html = '';
        let total = 0;

        for (const [brand, items] of Object.entries(groups)) {
          total += items.length;
          html += `<div class="brand-group">`;
          html += `<div class="brand-header"><i class="fas fa-folder"></i> ${brand} <span class="count">${items.length} domains</span></div>`;
          html += `<div class="status-list">`;

          items.forEach((svc) => {
            const r = results[svc.index] || { status: "active", ping: null };
            const isIpos = svc.isIpos;
            const cls = isIpos ? "ipos" : "active";
            const badge = isIpos ? "IPOS" : "ACTIVE";
            const icon = isIpos ? "fa-circle-xmark" : "fa-circle-check";
            const pingDisplay = (r.ping !== null && r.ping !== undefined) ? `${r.ping} ms` : "—";
            const pingClass = r.ping !== null ? "" : "down";

            html += `
              <a class="status-card ${cls}" href="${svc.url}" target="_blank" rel="noopener noreferrer">
                <div class="status-icon"><i class="fas ${icon}"></i></div>
                <div class="status-info">
                  <span class="status-name">${svc.name}</span>
                  <span class="status-url">${svc.url}</span>
                </div>
                <div class="status-right">
                  <span class="status-ping ${pingClass}"><i class="fas fa-bolt"></i> ${pingDisplay}</span>
                  <span class="status-badge">${badge}</span>
                </div>
              </a>`;
          });

          html += `</div></div>`;
        }

        container.innerHTML = html;
        document.getElementById("totalDomains").textContent = `Total Domains: ${total}`;
      }

      // ── OVERALL ──
      function renderOverall() {
        const badge = document.getElementById("overallBadge");
        const text = document.getElementById("overallText");
        const dot = document.getElementById("statusDot");
        const label = document.getElementById("statusLabel");

        const iposCount = IPOS_DOMAINS.length;

        if (iposCount === 0) {
          badge.className = "overall-badge all-up";
          text.textContent = "All Domains Normal";
          dot.className = "status-dot green";
          label.textContent = "Monitoring Active";
        } else {
          badge.className = "overall-badge has-ipos";
          text.textContent = `${iposCount} Domain(s) Nawala / IPOS`;
          dot.className = "status-dot red";
          label.textContent = `${iposCount} Domain(s) IPOS`;
        }
      }

      // ── TIMER ──
      function updateTimer() {
        const progress = document.getElementById("timerProgress");
        const text = document.getElementById("timerText");
        const circumference = 106.81;
        const offset = circumference * (1 - timeLeft / AUTO_REFRESH_SEC);
        progress.style.strokeDashoffset = offset;

        const mins = Math.floor(timeLeft / 60);
        const secs = Math.floor(timeLeft % 60);
        text.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

        if (timeLeft <= 0) {
          refreshAll();
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

      // ── REFRESH ALL (with real ping) ──
      async function refreshAll() {
        if (isUpdating) return;
        isUpdating = true;

        const btn = document.getElementById("btnRefresh");
        btn.classList.add("spinning");

        // Set loading state
        results = SERVICES.map(() => ({ status: "active", ping: null }));
        renderList();

        // Check each domain with real ping
        const batchSize = 10;
        for (let i = 0; i < SERVICES.length; i += batchSize) {
          const batch = SERVICES.slice(i, i + batchSize);
          await Promise.all(
            batch.map(async (svc, idx) => {
              const realIdx = i + idx;
              const r = await checkOne(svc.url);
              results[realIdx] = r;
              renderList();
            })
          );
        }

        // Update last checked time
        const now = new Date();
        document.getElementById("lastChecked").textContent =
          now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

        renderOverall();
        timeLeft = AUTO_REFRESH_SEC;
        updateTimer();

        btn.classList.remove("spinning");
        isUpdating = false;
      }

      // ── FETCH IPOS STATUS FROM SERVER ──
      async function fetchIposStatus() {
        try {
          const response = await fetch('/api/ipos-status');
          const data = await response.json();
          const iposDomains = data.ipos_domains || [];
          const patrolResult = data.last_patrol || 'Not run yet';
          
          document.getElementById('patrolResult').textContent = `Last patrol: ${patrolResult}`;
          
          // Update IPOS_DOMAINS and re-render
          window.IPOS_DOMAINS = iposDomains;
          renderList();
          renderOverall();
        } catch (e) {
          console.error('Failed to fetch IPOS status:', e);
        }
      }

      // ── INIT ──
      window.IPOS_DOMAINS = IPOS_DOMAINS;
      renderList();
      renderOverall();
      startTimer();

      // Initial check after page load
      setTimeout(() => {
        refreshAll();
      }, 500);

      // Fetch IPOS status periodically
      setInterval(fetchIposStatus, 30000); // Every 30 seconds
      
      // Also fetch on page visibility change
      document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
          fetchIposStatus();
        }
      });
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
    return render_template_string(
        HTML_TEMPLATE, 
        services=all_domains,
        ipos_domains=IPOS_DOMAINS
    )

@app.route('/api/ipos-status')
def api_ipos_status():
    """API untuk mendapatkan status IPOS terkini"""
    return Response(
        json.dumps({
            "ipos_domains": IPOS_DOMAINS,
            "last_patrol": LAST_PATROL_RESULT
        }),
        mimetype='application/json'
    )

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
