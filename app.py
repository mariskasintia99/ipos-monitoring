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

IPOS_DOMAINS = []
LAST_CHECK_RESULT = "No check run yet"
IS_CHECK_RUNNING = False
CHECK_LOG = []

log_buffer = ""

def log(type_msg, msg):
    global log_buffer
    timestamp = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%H:%M:%S")
    line = f"[{timestamp}] [{type_msg}]  {msg}\n"
    print(line, end="")
    log_buffer += line
    CHECK_LOG.append(line)

# --- FUNGSI CLOUDFLARE & TELEGRAM ---
def get_kv(key_name):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{key_name}"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"})
        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, list):
                    return data
                return []
            except:
                return []
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
    all_domains = []
    for target in TARGETS_IPOS:
        domains = get_kv(target['key'])
        if domains and isinstance(domains, list):
            for domain in domains:
                if domain and isinstance(domain, str):
                    domain = domain.strip()
                    if domain:
                        all_domains.append({
                            "name": domain,
                            "url": f"https://{domain}/",
                            "brand": target['name'],
                            "key": target['key']
                        })
    return all_domains

# --- MESIN UTAMA ---
def run_check():
    global IPOS_DOMAINS, LAST_CHECK_RESULT, CHECK_LOG
    CHECK_LOG = []
    IPOS_DOMAINS = []
    
    log("SYSTEM", "=" * 50)
    log("SYSTEM", "🚀 MEMULAI PENGECEKAN KE NAWALA")
    log("SYSTEM", "=" * 50)

    ada_perubahan = False
    global_report = []
    all_removed = []

    for target in TARGETS_IPOS:
        log("INFO", f"--- Memproses Brand: {target['name']} ---")
        domains = get_kv(target['key'])
        
        if not domains:
            log("INFO", f"Tidak ada domain di {target['name']}.")
            continue
            
        log("INFO", f"Domain saat ini ({len(domains)}): {domains}")
            
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
                        is_blocked = item.get("nawala", {}).get("blocked") or item.get("network", {}).get("blocked")
                        if is_blocked:
                            blocked_domains.append(dom)
                            log("WARN", f"🔴 {dom} TERDETEKSI IPOS!")
                            
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
            log("INFO", f"✅ {len(removed)} domain dihapus dari {target['name']}")
            log("INFO", f"Domain tersisa: {active}")
            
        global_report.append({"name": target["name"], "active": active, "removed": removed})

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
        LAST_CHECK_RESULT = f"Checking completed at {datetime.now(timezone.utc) + timedelta(hours=7):%H:%M:%S} - {len(all_removed)} domains removed from KV Domain Storage"
    else:
        LAST_CHECK_RESULT = f"Checking completed at {datetime.now(timezone.utc) + timedelta(hours=7):%H:%M:%S} - No IPOS domains found"

    log("SYSTEM", "=" * 50)
    log("SUCCESS", "✅ PENGECEKAN SELESAI!")
    log("SYSTEM", "=" * 50)
    return "\\n".join(CHECK_LOG)

# --- HTML TEMPLATE ---
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
    <meta http-equiv="Pragma" content="no-cache" />
    <meta http-equiv="Expires" content="0" />
    <title>IPOS Monitoring</title>
    <!-- Favicon -->
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🛡️</text></svg>" />
    <link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🛡️</text></svg>" />
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
        padding: 14px 22px 10px;
        display: flex;
        flex-direction: column;
        box-shadow: 0 25px 60px rgba(0,0,0,0.6);
      }

      header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-shrink: 0;
        padding-bottom: 6px;
        border-bottom: 2px solid rgba(255,255,255,0.08);
      }

      .logo {
        font-size: 1.5rem;
        font-weight: 900;
        letter-spacing: -0.5px;
        background: linear-gradient(135deg, #fff 30%, #f093fb 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .logo i { -webkit-text-fill-color: #f093fb; margin-right: 6px; }

      .header-right {
        display: flex;
        align-items: center;
        gap: 16px;
      }

      .real-time-clock {
        font-size: 0.8rem;
        font-weight: 500;
        color: rgba(255,255,255,0.4);
        font-variant-numeric: tabular-nums;
        background: rgba(255,255,255,0.05);
        padding: 2px 12px;
        border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.06);
        min-width: 70px;
        text-align: center;
      }

      .header-status {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 0.85rem;
        color: rgba(255,255,255,0.5);
      }
      .status-dot {
        width: 12px; height: 12px;
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

      /* ── HERO - CENTERED ── */
      .hero-center {
        text-align: center;
        flex-shrink: 0;
        padding: 4px 0 3px;
      }
      .hero-center h1 {
        font-size: 1.3rem;
        font-weight: 800;
        letter-spacing: -0.3px;
      }
      .hero-center p {
        color: rgba(255,255,255,0.3);
        font-size: 0.8rem;
        margin-top: 1px;
      }

      .overall-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        padding: 5px 24px;
        border-radius: 50px;
        font-size: 1rem;
        font-weight: 700;
        transition: all 0.4s;
        cursor: default;
        margin-top: 2px;
        height: 36px;
        min-width: 190px;
      }
      .overall-badge.all-up {
        background: rgba(0, 230, 118, 0.12);
        color: #00e676;
        border: 1.5px solid rgba(0, 230, 118, 0.25);
      }
      .overall-badge.has-ipos {
        background: rgba(255, 23, 68, 0.12);
        color: #ff1744;
        border: 1.5px solid rgba(255, 23, 68, 0.25);
      }
      .overall-badge.checking {
        background: rgba(245, 158, 11, 0.12);
        color: #f59e0b;
        border: 1.5px solid rgba(245, 158, 11, 0.25);
        animation: pulse-glow 0.8s ease-in-out infinite;
      }
      @keyframes pulse-glow {
        0%, 100% { box-shadow: 0 0 20px rgba(245, 158, 11, 0.1); }
        50% { box-shadow: 0 0 40px rgba(245, 158, 11, 0.3); }
      }
      .overall-badge .pulse-icon {
        width: 10px; height: 10px;
        border-radius: 50%;
        display: inline-block;
        animation: pulse-badge 1.2s ease-in-out infinite;
      }
      .all-up .pulse-icon { background: #00e676; }
      .has-ipos .pulse-icon { background: #ff1744; }
      .checking .pulse-icon { background: #f59e0b; }
      @keyframes pulse-badge {
        0%, 100% { transform: scale(1); opacity: 1; }
        50% { transform: scale(1.5); opacity: 0.5; }
      }

      /* ── TOP BAR (Auto-refresh + Refresh button) ── */
      .top-bar {
        display: flex;
        align-items: center;
        gap: 14px;
        flex-shrink: 0;
        padding: 4px 0 6px;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        margin-bottom: 4px;
      }
      .top-bar .timer-section {
        display: flex;
        align-items: center;
        gap: 10px;
        flex: 1;
      }
      .top-bar .timer-section .label {
        font-size: 0.7rem;
        color: rgba(255,255,255,0.3);
        font-weight: 500;
        letter-spacing: 1px;
        text-transform: uppercase;
      }
      .top-bar .timer-section .progress-track {
        flex: 1;
        height: 5px;
        background: rgba(255,255,255,0.06);
        border-radius: 4px;
        overflow: hidden;
        min-width: 60px;
      }
      .top-bar .timer-section .progress-fill {
        height: 100%;
        background: linear-gradient(90deg, #f093fb, #f5576c);
        border-radius: 4px;
        width: 100%;
        transition: width 0.5s linear;
        box-shadow: 0 0 10px rgba(245, 87, 108, 0.3);
      }
      .top-bar .timer-section .time-text {
        font-size: 0.8rem;
        font-weight: 600;
        color: rgba(255,255,255,0.5);
        font-variant-numeric: tabular-nums;
        min-width: 45px;
        text-align: right;
      }
      .top-bar .timer-section .icon-sand {
        color: #f093fb;
        font-size: 1rem;
        animation: spin-sand 4s linear infinite;
      }
      @keyframes spin-sand {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
      .top-bar .btn-refresh {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 16px;
        background: linear-gradient(135deg, #f093fb, #f5576c);
        color: #fff;
        border: none;
        border-radius: 6px;
        font-family: inherit;
        font-size: 0.75rem;
        font-weight: 700;
        cursor: pointer;
        transition: all 0.3s;
        flex-shrink: 0;
      }
      .top-bar .btn-refresh:hover {
        transform: scale(1.03);
        box-shadow: 0 6px 20px rgba(245, 87, 108, 0.2);
      }
      .top-bar .btn-refresh.spinning i { animation: spin 0.7s linear infinite; }
      .top-bar .btn-refresh:disabled { opacity: 0.4; cursor: not-allowed; transform: none !important; }
      @keyframes spin { to { transform: rotate(360deg); } }

      /* ── MAIN ── */
      main {
        flex: 1;
        min-height: 0;
        display: flex;
        flex-direction: column;
        padding-top: 2px;
        overflow: hidden;
      }

      .section-title {
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 2px;
        text-transform: uppercase;
        color: rgba(255,255,255,0.2);
        flex-shrink: 0;
        padding-bottom: 2px;
        display: flex;
        justify-content: space-between;
        align-items: center;
      }

      .copy-all-btn {
        font-size: 0.75rem;
        font-weight: 600;
        color: rgba(255,255,255,0.4);
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.08);
        padding: 2px 14px;
        border-radius: 14px;
        cursor: pointer;
        transition: all 0.3s;
        font-family: inherit;
        display: flex;
        align-items: center;
        gap: 5px;
        height: 28px;
        flex-shrink: 0;
      }
      .copy-all-btn:hover {
        background: rgba(255,255,255,0.08);
        color: #fff;
        border-color: rgba(255,255,255,0.15);
      }
      .copy-all-btn.copied {
        background: rgba(0, 230, 118, 0.12);
        color: #00e676;
        border-color: rgba(0, 230, 118, 0.2);
      }
      .copy-all-btn i { font-size: 0.6rem; }

      .status-scroll {
        flex: 1;
        overflow-y: auto;
        padding-right: 4px;
      }
      .status-scroll::-webkit-scrollbar { width: 3px; }
      .status-scroll::-webkit-scrollbar-track { background: rgba(255,255,255,0.05); border-radius: 4px; }
      .status-scroll::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 4px; }

      .brand-group {
        margin-bottom: 4px;
      }
      .brand-header {
        font-size: 1rem;
        font-weight: 700;
        color: #f093fb;
        padding: 2px 0 2px;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        margin-bottom: 2px;
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .brand-header .count {
        font-size: 0.65rem;
        font-weight: 400;
        color: rgba(255,255,255,0.25);
        background: rgba(255,255,255,0.05);
        padding: 0 10px;
        border-radius: 12px;
      }

      .status-list {
        display: flex;
        flex-direction: column;
        gap: 1px;
      }
      .status-card {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.04);
        border-radius: 5px;
        padding: 3px 12px;
        display: flex;
        align-items: center;
        gap: 10px;
        min-height: 32px;
        transition: all 0.2s ease;
      }
      .status-card:hover {
        background: rgba(255, 255, 255, 0.05);
        border-color: rgba(255, 255, 255, 0.08);
      }
      .status-card.active { border-left: 3px solid #00e676; }
      .status-card.ipos { border-left: 3px solid #ff1744; }

      .status-icon {
        font-size: 0.85rem;
        width: 20px;
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
        gap: 12px;
        flex-wrap: wrap;
      }
      .status-name {
        font-size: 0.9rem;
        font-weight: 600;
        white-space: nowrap;
      }
      .status-url {
        font-size: 0.6rem;
        color: rgba(255,255,255,0.2);
        white-space: nowrap;
      }

      .status-right {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-shrink: 0;
      }
      .status-badge {
        font-size: 0.65rem;
        font-weight: 700;
        padding: 1px 12px;
        border-radius: 12px;
        height: 24px;
        display: flex;
        align-items: center;
      }
      .active .status-badge {
        background: rgba(0, 230, 118, 0.1);
        color: #00e676;
      }
      .ipos .status-badge {
        background: rgba(255, 23, 68, 0.1);
        color: #ff1744;
      }

      .copy-btn {
        font-size: 0.65rem;
        color: rgba(255,255,255,0.25);
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.05);
        padding: 0 10px;
        border-radius: 10px;
        cursor: pointer;
        transition: all 0.3s;
        font-family: inherit;
        display: flex;
        align-items: center;
        gap: 3px;
        white-space: nowrap;
        height: 24px;
      }
      .copy-btn:hover {
        background: rgba(255,255,255,0.06);
        color: #fff;
        border-color: rgba(255,255,255,0.12);
      }
      .copy-btn.copied {
        background: rgba(0, 230, 118, 0.1);
        color: #00e676;
        border-color: rgba(0, 230, 118, 0.15);
      }
      .copy-btn i { font-size: 0.5rem; }

      /* ── IPOS SECTION ── */
      .ipos-section {
        margin-top: 3px;
        border-top: 1px solid rgba(255,23,68,0.15);
        padding-top: 3px;
      }
      .ipos-header {
        font-size: 0.85rem;
        font-weight: 700;
        color: #ff1744;
        padding: 1px 0 1px;
        border-bottom: 1px solid rgba(255,23,68,0.1);
        margin-bottom: 2px;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .ipos-header .count {
        font-size: 0.6rem;
        font-weight: 400;
        color: rgba(255,255,255,0.3);
        background: rgba(255,23,68,0.1);
        padding: 0 8px;
        border-radius: 10px;
      }
      .ipos-list {
        display: flex;
        flex-direction: column;
        gap: 1px;
      }
      .ipos-card {
        background: rgba(255, 23, 68, 0.05);
        border: 1px solid rgba(255, 23, 68, 0.1);
        border-radius: 5px;
        padding: 3px 12px;
        display: flex;
        align-items: center;
        gap: 10px;
        min-height: 28px;
        border-left: 3px solid #ff1744;
      }
      .ipos-card .status-name {
        font-size: 0.9rem;
        font-weight: 600;
        color: #ff1744;
      }
      .ipos-card .status-badge {
        background: rgba(255, 23, 68, 0.12);
        color: #ff1744;
        font-size: 0.6rem;
        padding: 1px 12px;
        border-radius: 12px;
        font-weight: 700;
        height: 22px;
        display: flex;
        align-items: center;
      }
      .ipos-card .copy-btn {
        border-color: rgba(255, 23, 68, 0.15);
        color: rgba(255,255,255,0.3);
        height: 22px;
        font-size: 0.6rem;
      }
      .ipos-card .copy-btn:hover {
        border-color: rgba(255, 23, 68, 0.3);
        color: #fff;
      }
      .ipos-card .status-icon {
        font-size: 0.75rem;
        width: 18px;
        color: #ff1744;
      }

      /* ── FOOTER ── */
      .footer-controls {
        flex-shrink: 0;
        padding-top: 5px;
        border-top: 1px solid rgba(255,255,255,0.04);
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .footer-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
      }
      .footer-left {
        display: flex;
        align-items: center;
        gap: 16px;
        flex-wrap: wrap;
      }
      .last-checked {
        font-size: 0.75rem;
        color: rgba(255,255,255,0.3);
      }
      .last-checked span { color: rgba(255,255,255,0.5); font-weight: 500; }
      .check-result {
        font-size: 0.7rem;
        color: rgba(255,255,255,0.2);
      }
      .check-result span { color: rgba(255,255,255,0.35); }

      .total-domains {
        font-size: 0.85rem;
        color: rgba(255,255,255,0.35);
        font-weight: 600;
        flex-shrink: 0;
      }

      .footer-copyright {
        text-align: center;
        padding: 3px 0 0;
        font-size: 0.55rem;
        color: rgba(255,255,255,0.12);
        flex-shrink: 0;
        border-top: 1px solid rgba(255,255,255,0.03);
        margin-top: 2px;
      }
      .footer-copyright a {
        color: rgba(255,255,255,0.18);
        text-decoration: none;
        transition: color 0.3s;
      }
      .footer-copyright a:hover { color: #f093fb; }

      .toast {
        position: fixed;
        bottom: 30px;
        left: 50%;
        transform: translateX(-50%);
        background: rgba(0, 230, 118, 0.9);
        color: #fff;
        padding: 8px 20px;
        border-radius: 10px;
        font-size: 0.8rem;
        font-weight: 600;
        opacity: 0;
        transition: opacity 0.3s ease;
        pointer-events: none;
        z-index: 999;
        box-shadow: 0 8px 30px rgba(0,0,0,0.4);
      }
      .toast.show { opacity: 1; }
      .toast.error { background: rgba(255, 23, 68, 0.9); }

      @media (max-width: 768px) {
        .app-container { padding: 10px 14px 8px; border-radius: 16px; }
        .logo { font-size: 1.2rem; }
        .hero-center h1 { font-size: 1.1rem; }
        .status-name { font-size: 0.8rem; }
        .status-url { font-size: 0.5rem; display: none; }
        .overall-badge { font-size: 0.8rem; padding: 4px 16px; height: 32px; min-width: 160px; }
        .copy-btn { font-size: 0.55rem; padding: 0 8px; height: 20px; }
        .copy-btn i { font-size: 0.4rem; }
        .copy-all-btn { font-size: 0.65rem; padding: 2px 12px; height: 24px; }
        .status-badge { font-size: 0.55rem; padding: 1px 10px; height: 20px; }
        .status-card { padding: 2px 10px; min-height: 28px; gap: 8px; }
        .footer-left { gap: 10px; }
        .last-checked { font-size: 0.65rem; }
        .check-result { font-size: 0.6rem; }
        .total-domains { font-size: 0.75rem; }
        .brand-header { font-size: 0.85rem; }
        .status-icon { font-size: 0.75rem; width: 18px; }
        .ipos-card .status-name { font-size: 0.8rem; }
        .ipos-header { font-size: 0.75rem; }
        .ipos-card { padding: 2px 10px; min-height: 24px; }
        .ipos-card .status-badge { font-size: 0.55rem; padding: 1px 10px; height: 20px; }
        .ipos-card .copy-btn { font-size: 0.55rem; height: 20px; padding: 0 8px; }
        .ipos-card .status-icon { font-size: 0.65rem; width: 16px; }
        .top-bar .timer-section .label { font-size: 0.6rem; }
        .top-bar .timer-section .time-text { font-size: 0.7rem; min-width: 40px; }
        .top-bar .timer-section .icon-sand { font-size: 0.85rem; }
        .top-bar .btn-refresh { font-size: 0.65rem; padding: 3px 12px; }
        .section-title { font-size: 0.6rem; }
        .real-time-clock { font-size: 0.6rem; padding: 1px 8px; }
      }

      @media (max-width: 480px) {
        .app-container { padding: 6px 8px 4px; border-radius: 12px; }
        .logo { font-size: 1rem; }
        .hero-center h1 { font-size: 0.9rem; }
        .hero-center p { font-size: 0.6rem; }
        .overall-badge { font-size: 0.65rem; padding: 3px 12px; height: 28px; min-width: 140px; }
        .status-name { font-size: 0.7rem; }
        .status-url { display: none; }
        .copy-btn { font-size: 0.5rem; padding: 0 6px; height: 18px; }
        .copy-btn i { font-size: 0.4rem; }
        .copy-all-btn { font-size: 0.55rem; padding: 1px 10px; height: 20px; }
        .status-badge { font-size: 0.5rem; padding: 1px 8px; height: 18px; }
        .status-card { padding: 2px 6px; min-height: 24px; gap: 6px; }
        .brand-header { font-size: 0.7rem; }
        .brand-header .count { font-size: 0.5rem; padding: 0 6px; }
        .status-icon { font-size: 0.65rem; width: 16px; }
        .footer-left { gap: 6px; flex-wrap: wrap; }
        .last-checked { font-size: 0.55rem; }
        .check-result { font-size: 0.5rem; }
        .total-domains { font-size: 0.65rem; }
        .footer-copyright { font-size: 0.45rem; }
        .toast { font-size: 0.6rem; padding: 5px 12px; bottom: 10px; }
        .section-title { font-size: 0.5rem; }
        .ipos-card .status-name { font-size: 0.7rem; }
        .ipos-header { font-size: 0.65rem; }
        .ipos-card { padding: 2px 6px; min-height: 22px; }
        .ipos-card .status-badge { font-size: 0.5rem; padding: 1px 8px; height: 18px; }
        .ipos-card .copy-btn { font-size: 0.5rem; height: 18px; padding: 0 6px; }
        .ipos-card .status-icon { font-size: 0.55rem; width: 14px; }
        .top-bar .timer-section .label { font-size: 0.5rem; }
        .top-bar .timer-section .time-text { font-size: 0.6rem; min-width: 35px; }
        .top-bar .timer-section .icon-sand { font-size: 0.7rem; }
        .top-bar .btn-refresh { font-size: 0.55rem; padding: 2px 10px; }
        .top-bar { gap: 8px; }
        .real-time-clock { font-size: 0.5rem; padding: 1px 6px; }
      }
    </style>
  </head>
  <body>

    <div class="particles" id="particles"></div>

    <div class="app-container">
      <!-- HEADER -->
      <header>
        <div class="logo">
          <span><i class="fas fa-shield-halved"></i>IPOS<span style="-webkit-text-fill-color:#f093fb;">Monitoring</span></span>
        </div>
        <div class="header-right">
          <span class="real-time-clock" id="realTimeClock">--:--:--</span>
          <div class="header-status">
            <span class="status-dot green" id="statusDot"></span>
            <span id="statusLabel">Monitoring Active</span>
          </div>
        </div>
      </header>

      <!-- HERO - CENTERED -->
      <div class="hero-center">
        <h1>IPOS Service Status</h1>
        <p>Real-time monitoring of all domains</p>
        <div class="overall-badge all-up" id="overallBadge">
          <span class="pulse-icon"></span>
          <span id="overallText">All Domains Normal</span>
        </div>
      </div>

      <!-- TOP BAR (Auto-refresh + Refresh button) -->
      <div class="top-bar">
        <div class="timer-section">
          <i class="fas fa-hourglass-half icon-sand"></i>
          <span class="label">Auto-refresh</span>
          <div class="progress-track">
            <div class="progress-fill" id="timerProgressFill" style="width:100%;"></div>
          </div>
          <span class="time-text" id="timerText">15:00</span>
        </div>
        <button class="btn-refresh" id="btnRefresh" onclick="runCheck()">
          <i class="fas fa-sync"></i> Refresh
        </button>
      </div>

      <!-- MAIN -->
      <main>
        <div class="status-scroll" id="statusContainer">
          <!-- Rendered by JS -->
        </div>
      </main>

      <!-- FOOTER -->
      <div class="footer-controls">
        <div class="footer-row">
          <div class="footer-left">
            <div class="last-checked">
              <i class="fas fa-clock"></i>&nbsp; Last Checked: <span id="lastChecked">—</span>
            </div>
            <div class="check-result" id="checkResult">—</div>
          </div>
          <div class="total-domains" id="totalDomains">Total Domains: 0</div>
        </div>
      </div>
      
      <div class="footer-copyright">
        &copy; 2025 IPOS Monitoring — <a href="/">Back to Dashboard</a>
      </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
      // ── PARTICLES ──
      (function createParticles() {
        const container = document.getElementById('particles');
        const count = 20;
        for (let i = 0; i < count; i++) {
          const p = document.createElement('div');
          p.className = 'particle';
          p.style.left = Math.random() * 100 + '%';
          p.style.width = (Math.random() * 4 + 2) + 'px';
          p.style.height = p.style.width;
          p.style.animationDuration = (Math.random() * 15 + 10) + 's';
          p.style.animationDelay = (Math.random() * 15) + 's';
          p.style.opacity = Math.random() * 0.3 + 0.05;
          container.appendChild(p);
        }
      })();

      // ── TOAST ──
      function showToast(msg, isError = false) {
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.className = 'toast' + (isError ? ' error' : '');
        t.classList.add('show');
        clearTimeout(t._timeout);
        t._timeout = setTimeout(() => t.classList.remove('show'), 2500);
      }

      // ── DATA ──
      let SERVICES = {{ services|tojson }};
      let IPOS_DOMAINS = {{ ipos_domains|tojson }};
      const AUTO_REFRESH_SEC = 15 * 60;

      let timerID = null;
      let timeLeft = AUTO_REFRESH_SEC;
      let isCheckRunning = false;

      // ── JAM REAL-TIME ASIA/BANGKOK (GMT+7) ──
      function updateRealTimeClock() {
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-US', { 
          hour: '2-digit', 
          minute: '2-digit', 
          second: '2-digit',
          hour12: false,
          timeZone: 'Asia/Bangkok'
        });
        const clock = document.getElementById('realTimeClock');
        if (clock) clock.textContent = timeStr;
      }
      setInterval(updateRealTimeClock, 1000);
      updateRealTimeClock();

      // ── UPDATE LAST CHECKED (Asia/Bangkok) ──
      function updateLastChecked() {
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-US', { 
          hour: '2-digit', 
          minute: '2-digit', 
          second: '2-digit',
          hour12: false,
          timeZone: 'Asia/Bangkok'
        });
        document.getElementById('lastChecked').textContent = timeStr;
      }

      // ── SIMPAN STATE KE LOCALSTORAGE ──
      function saveTimerState() {
        localStorage.setItem('ipos_timerRemaining', Math.floor(timeLeft));
        localStorage.setItem('ipos_timerTimestamp', Date.now());
      }

      function loadTimerState() {
        const savedRemaining = localStorage.getItem('ipos_timerRemaining');
        const savedTimestamp = localStorage.getItem('ipos_timerTimestamp');
        if (savedRemaining && savedTimestamp) {
          const elapsed = (Date.now() - parseInt(savedTimestamp)) / 1000;
          timeLeft = Math.max(0, parseInt(savedRemaining) - elapsed);
          if (timeLeft <= 0) {
            timeLeft = AUTO_REFRESH_SEC;
            localStorage.removeItem('ipos_timerRemaining');
            localStorage.removeItem('ipos_timerTimestamp');
          }
        }
        updateTimer();
      }

      // ── RENDER ──
    function renderList(services, iposDomains) {
      const container = document.getElementById("statusContainer");
      const groups = {};
      const iposSet = new Set(iposDomains.map(d => d.toLowerCase()));
      
      services.forEach((svc) => {
        if (!groups[svc.brand]) groups[svc.brand] = [];
        const isIpos = iposSet.has(svc.name.toLowerCase());
        groups[svc.brand].push({ ...svc, isIpos });
      });
    
      let html = '';
      let total = 0;
      let isFirstBrand = true; // Flag untuk brand pertama
    
      for (const [brand, items] of Object.entries(groups)) {
        total += items.length;
        html += `<div class="brand-group">`;
        
        // Brand header dengan Copy All hanya di brand pertama
        html += `<div class="brand-header">`;
        html += `<div class="brand-left">`;
        html += `<i class="fas fa-folder"></i> ${brand}`;
        html += `<span class="count">${items.length} domains</span>`;
        html += `</div>`;
        
        if (isFirstBrand) {
          html += `<button class="copy-all-btn" onclick="copyAll()">`;
          html += `<i class="fas fa-copy"></i> Copy All`;
          html += `</button>`;
          isFirstBrand = false;
        }
        html += `</div>`; // tutup brand-header
        
        html += `<div class="status-list">`;

          items.forEach((svc) => {
            const cls = svc.isIpos ? "ipos" : "active";
            const badge = svc.isIpos ? "IPOS" : "ACTIVE";
            const icon = svc.isIpos ? "fa-circle-xmark" : "fa-circle-check";

            html += `
              <div class="status-card ${cls}">
                <div class="status-icon"><i class="fas ${icon}"></i></div>
                <div class="status-info">
                  <span class="status-name">${svc.name}</span>
                  <span class="status-url">${svc.url}</span>
                </div>
                <div class="status-right">
                  <button class="copy-btn" onclick="copyDomain('${svc.name}')">
                    <i class="fas fa-copy"></i> Copy
                  </button>
                  <span class="status-badge">${badge}</span>
                </div>
              </div>`;
          });

          html += `</div></div>`;
        }

        // ── IPOS SECTION ──
        if (iposDomains && iposDomains.length > 0) {
          html += `<div class="ipos-section">`;
          html += `<div class="ipos-header"><i class="fas fa-triangle-exclamation"></i> IPOS Domains <span class="count">${iposDomains.length} domains</span></div>`;
          html += `<div class="ipos-list">`;
          
          iposDomains.forEach((domain) => {
            html += `
              <div class="ipos-card">
                <div class="status-icon"><i class="fas fa-circle-xmark"></i></div>
                <span class="status-name">${domain}</span>
                <div style="flex:1;"></div>
                <div class="status-right">
                  <button class="copy-btn" onclick="copyDomain('${domain}')">
                    <i class="fas fa-copy"></i> Copy
                  </button>
                  <span class="status-badge">IPOS</span>
                </div>
              </div>`;
          });
          
          html += `</div></div>`;
        }

        container.innerHTML = html;
        document.getElementById("totalDomains").textContent = `Total Domains: ${total}`;
      }

      // ── OVERALL BADGE ──
      function renderOverall(iposCount) {
        const badge = document.getElementById("overallBadge");
        const text = document.getElementById("overallText");
        const dot = document.getElementById("statusDot");
        const label = document.getElementById("statusLabel");

        if (isCheckRunning) {
          badge.className = "overall-badge checking";
          text.textContent = "Checking...";
          dot.className = "status-dot";
          dot.style.background = "#f59e0b";
          label.textContent = "Checking...";
          return;
        }
        dot.style.background = "";

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

      // ── COPY ──
      function copyDomain(domain) {
        navigator.clipboard.writeText(domain).then(() => {
          showToast(`Copied: ${domain}`);
          document.querySelectorAll('.copy-btn').forEach(btn => {
            const parent = btn.closest('.status-card') || btn.closest('.ipos-card');
            if (parent?.querySelector('.status-name')?.textContent === domain) {
              btn.classList.add('copied');
              btn.innerHTML = '<i class="fas fa-check"></i> Copied';
              setTimeout(() => {
                btn.classList.remove('copied');
                btn.innerHTML = '<i class="fas fa-copy"></i> Copy';
              }, 1200);
            }
          });
        }).catch(() => {
          const ta = document.createElement('textarea');
          ta.value = domain;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          showToast(`Copied: ${domain}`);
        });
      }

      // ── COPY ALL ──
      function copyAll() {
        const domains = SERVICES.map(s => s.name);
        const text = domains.join('\\n');
        navigator.clipboard.writeText(text).then(() => {
          showToast(`Copied ${domains.length} domains`);
          const btn = document.getElementById('copyAllBtn');
          btn.classList.add('copied');
          btn.innerHTML = '<i class="fas fa-check"></i> Copied All';
          setTimeout(() => {
            btn.classList.remove('copied');
            btn.innerHTML = '<i class="fas fa-copy"></i> Copy All';
          }, 1500);
        }).catch(() => {
          const ta = document.createElement('textarea');
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          showToast(`Copied ${domains.length} domains`);
        });
      }

      // ── TIMER ──
      function updateTimer() {
        const progress = document.getElementById("timerProgressFill");
        const text = document.getElementById("timerText");
        const pct = (timeLeft / AUTO_REFRESH_SEC) * 100;
        progress.style.width = pct + '%';
        const m = Math.floor(timeLeft / 60);
        const s = Math.floor(timeLeft % 60);
        text.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        saveTimerState();
        
        if (timeLeft <= 0 && !isCheckRunning) {
          runCheck();
          timeLeft = AUTO_REFRESH_SEC;
          saveTimerState();
        }
      }

      function startTimer() {
        if (timerID) clearInterval(timerID);
        timerID = setInterval(() => {
          if (!isCheckRunning) { 
            timeLeft--; 
            updateTimer(); 
          }
        }, 1000);
        updateTimer();
      }

      // ── RUN CHECK ──
      async function runCheck() {
        if (isCheckRunning) return;
        isCheckRunning = true;
        const btn = document.getElementById("btnRefresh");
        btn.disabled = true;
        btn.classList.add("spinning");
        
        renderOverall(0);
        document.getElementById("checkResult").textContent = "Checking...";
        document.getElementById("lastChecked").textContent = "Checking...";
        
        try {
          const resp = await fetch('/api/run-check', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
          const data = await resp.json();
          
          if (data.success) {
            SERVICES = data.services || [];
            IPOS_DOMAINS = data.ipos_domains || [];
            renderList(SERVICES, IPOS_DOMAINS);
            renderOverall(IPOS_DOMAINS.length);
            
            // Update Last Checked dengan GMT+7
            updateLastChecked();
            
            document.getElementById("checkResult").textContent = data.check_result || 'Check completed';
            if (IPOS_DOMAINS.length > 0) {
              showToast(`${IPOS_DOMAINS.length} domain(s) detected as IPOS and removed`);
            } else {
              showToast('No IPOS domains found. All domains are safe!');
            }
          } else {
            showToast('Check failed: ' + (data.error || 'Unknown error'), true);
          }
        } catch (e) {
          showToast('Check failed: ' + e.message, true);
        }
        
        isCheckRunning = false;
        btn.disabled = false;
        btn.classList.remove("spinning");
        timeLeft = AUTO_REFRESH_SEC;
        saveTimerState();
        updateTimer();
      }

      // ── FETCH STATUS ──
      async function fetchStatus() {
        if (isCheckRunning) return;
        try {
          const resp = await fetch('/api/ipos-status');
          const data = await resp.json();
          document.getElementById('checkResult').textContent = data.last_check || 'No check run yet';
          IPOS_DOMAINS = data.ipos_domains || [];
          renderList(SERVICES, IPOS_DOMAINS);
          renderOverall(IPOS_DOMAINS.length);
        } catch (e) { console.error(e); }
      }

      // ── INIT ──
      renderList(SERVICES, IPOS_DOMAINS);
      renderOverall(IPOS_DOMAINS.length);
      
      // Load timer state dari localStorage
      loadTimerState();
      startTimer();
      
      // Set initial check result
      document.getElementById('checkResult').textContent = "{{ last_check_result|default('No check run yet') }}";
      
      // Set initial Last Checked
      updateLastChecked();
      
      // Force refresh data saat halaman dimuat (tanpa reset timer)
      window.addEventListener('load', function() {
        fetchStatus();
        updateLastChecked();
      });
      
      // Auto check setiap 15 menit (900 detik)
      setInterval(function() {
        if (!isCheckRunning) {
          runCheck();
        }
      }, 15 * 60 * 1000);
      
      // Fetch status setiap 15 detik (untuk update check result)
      setInterval(fetchStatus, 15000);
      
      // Fetch saat tab menjadi aktif kembali (tanpa reset timer)
      document.addEventListener('visibilitychange', function() {
        if (!document.hidden && !isCheckRunning) {
          fetchStatus();
          updateLastChecked();
        }
      });
    </script>
  </body>
</html>
'''

# --- ENDPOINT ---
LAST_RUN_TIME = None
LAST_LOG_OUTPUT = "Sistem baru menyala. Memuat data patroli..."
IS_RUNNING = False

@app.route('/')
def status_page():
    all_domains = get_all_domains()
    return render_template_string(
        HTML_TEMPLATE, 
        services=all_domains,
        ipos_domains=IPOS_DOMAINS,
        last_check_result=LAST_CHECK_RESULT
    )

@app.route('/api/ipos-status')
def api_ipos_status():
    return Response(
        json.dumps({
            "ipos_domains": IPOS_DOMAINS,
            "last_check": LAST_CHECK_RESULT
        }),
        mimetype='application/json'
    )

@app.route('/api/run-check', methods=['POST'])
def api_run_check():
    global IS_RUNNING
    
    if IS_RUNNING:
        return Response(
            json.dumps({"success": False, "error": "Check already running"}),
            mimetype='application/json',
            status=409
        )
    
    try:
        IS_RUNNING = True
        log_result = run_check()
        all_domains = get_all_domains()
        
        return Response(
            json.dumps({
                "success": True,
                "services": all_domains,
                "ipos_domains": IPOS_DOMAINS,
                "check_result": LAST_CHECK_RESULT,
                "log": log_result
            }),
            mimetype='application/json'
        )
    except Exception as e:
        return Response(
            json.dumps({"success": False, "error": str(e)}),
            mimetype='application/json',
            status=500
        )
    finally:
        IS_RUNNING = False

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
                <h2>⚙️ ROBOT SEDANG KELILING...</h2>
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
            hasil_cek_baru = run_check()
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
                    
                    document.getElementById('countdown-text').innerText = "Pengecekan Berikutnya: " + m + ":" + s_display + " (" + percentage.toFixed(1) + "%)";
                    
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
