#!/usr/bin/env python3
"""
Red Nun Dashboard — Beelink Migration Runbook Generator

Inspects the current server and generates a step-by-step migration runbook.
Output: /opt/rednun/migration_runbook.txt

Usage: python3 /opt/rednun/migration_runbook.py
"""

import os
import sys
import subprocess
import shutil
import json
import socket
from datetime import datetime
from pathlib import Path

APP_DIR = "/opt/rednun"
DB_FILE = "/opt/rednun/toast_data.db"
SERVICE_NAME = "rednun"
OUTPUT_FILE = "/opt/rednun/migration_runbook.txt"


def run(cmd, fallback="(unavailable)"):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return result.stdout.strip() or fallback
    except Exception:
        return fallback


def section(title):
    bar = "=" * 70
    return f"\n{bar}\n  {title}\n{bar}\n"


def subsection(title):
    return f"\n--- {title} ---\n"


def gather_info():
    info = {}

    # System basics
    info["hostname"] = socket.gethostname()
    info["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    info["os"] = run("lsb_release -d | cut -f2")
    info["kernel"] = run("uname -r")
    info["arch"] = run("uname -m")
    info["uptime"] = run("uptime -p")
    info["whoami"] = run("whoami")

    # Python
    info["python_version"] = run("python3 --version")
    info["python_path"] = run("which python3")
    info["pip_packages"] = run("pip3 freeze 2>/dev/null || pip freeze 2>/dev/null")
    info["pip_package_count"] = len(info["pip_packages"].splitlines())

    # Running user / service
    info["service_status"] = run(f"systemctl is-active {SERVICE_NAME}")
    info["service_file"] = run(f"systemctl cat {SERVICE_NAME} 2>/dev/null")
    info["gunicorn_cmd"] = run(
        f"systemctl cat {SERVICE_NAME} 2>/dev/null | grep -i 'execstart' | head -1"
    )
    info["service_user"] = run(
        f"systemctl cat {SERVICE_NAME} 2>/dev/null | grep -i '^User' | head -1"
    )

    # Nginx
    info["nginx_status"] = run("systemctl is-active nginx")
    nginx_conf_file = f"/etc/nginx/sites-enabled/{SERVICE_NAME}"
    if os.path.exists(nginx_conf_file):
        info["nginx_conf"] = open(nginx_conf_file).read()
        info["nginx_conf_file"] = nginx_conf_file
    else:
        # Try default
        info["nginx_conf"] = run("nginx -T 2>/dev/null | head -80")
        info["nginx_conf_file"] = "(check /etc/nginx/sites-enabled/)"

    # Crontab
    info["crontab"] = run("crontab -l 2>/dev/null")

    # Environment / .env file
    env_file = os.path.join(APP_DIR, ".env")
    if os.path.exists(env_file):
        lines = open(env_file).readlines()
        # Redact secrets
        redacted = []
        secret_keys = {"api_key", "secret", "token", "password", "passwd", "pwd"}
        for line in lines:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                if any(s in k.lower() for s in secret_keys):
                    redacted.append(f"{k}=<REDACTED>")
                else:
                    redacted.append(line)
            else:
                redacted.append(line)
        info["env_vars"] = "\n".join(redacted)
        info["env_keys"] = [
            line.split("=")[0]
            for line in lines
            if "=" in line and not line.startswith("#")
        ]
    else:
        info["env_vars"] = "(no .env file found)"
        info["env_keys"] = []

    # Database
    if os.path.exists(DB_FILE):
        size_bytes = os.path.getsize(DB_FILE)
        info["db_size_mb"] = round(size_bytes / 1024 / 1024, 1)
        info["db_size_gb"] = round(size_bytes / 1024 / 1024 / 1024, 2)
    else:
        info["db_size_mb"] = 0
        info["db_size_gb"] = 0

    # Disk
    total, used, free = shutil.disk_usage(APP_DIR)
    info["disk_total_gb"] = round(total / 1024**3, 1)
    info["disk_used_gb"] = round(used / 1024**3, 1)
    info["disk_free_gb"] = round(free / 1024**3, 1)

    # App directory size
    info["app_dir_size"] = run(f"du -sh {APP_DIR} 2>/dev/null | cut -f1")

    # Key files
    key_files = [
        "server.py", "data_store.py", "invoice_processor.py", "analytics.py",
        "toast_client.py", "sync.py", "auth_routes.py", "invoice_routes.py",
        "inventory_routes.py", "catalog_routes.py", "product_mapping_routes.py",
        "storage_routes.py", "sports_guide.py", "inventory_ai_routes.py",
        "order_guide_routes.py", "specials_routes.py", "email_invoice_poller.py",
        "gmail_auth.py", "marginedge_sync.py", "thermostat.py", "forecast.py",
        "email_report.py", "export.py", "batch_ocr.py", ".env",
        "google_credentials.json", "gmail_token.pickle", "google_token.pickle",
        "requirements.txt", "run_sync.sh",
    ]
    info["file_list"] = []
    for f in key_files:
        fp = os.path.join(APP_DIR, f)
        exists = os.path.exists(fp)
        size = f"{round(os.path.getsize(fp)/1024, 1)}K" if exists else ""
        info["file_list"].append((f, exists, size))

    # Key directories
    key_dirs = [
        "static", "invoice_images", "invoice_images_archive",
        "templates", "logs",
    ]
    info["dir_list"] = []
    for d in key_dirs:
        dp = os.path.join(APP_DIR, d)
        exists = os.path.exists(dp)
        count = len(os.listdir(dp)) if exists else 0
        info["dir_list"].append((d, exists, count))

    # SSL cert
    info["ssl_cert"] = run(
        "certbot certificates 2>/dev/null | grep -A5 'dashboard.rednun.com' | head -10"
    )
    info["ssl_expiry"] = run(
        "echo | openssl s_client -connect dashboard.rednun.com:443 2>/dev/null "
        "| openssl x509 -noout -dates 2>/dev/null | grep notAfter"
    )

    # Ports listening
    info["ports"] = run("ss -tlnp 2>/dev/null | grep -E ':(80|443|8080)\\s'")

    # Memory
    info["memory"] = run("free -h | grep Mem")

    # CPU
    info["cpu"] = run("lscpu | grep 'Model name' | cut -d: -f2 | xargs")

    return info


def build_runbook(info):
    lines = []
    lines.append("RED NUN DASHBOARD — BEELINK MIGRATION RUNBOOK")
    lines.append(f"Generated: {info['date']} on {info['hostname']}")
    lines.append(f"Destination: Beelink SER5 (Chatham location, home ISP)")
    lines.append("")
    lines.append("=" * 70)
    lines.append("  CURRENT SERVER SNAPSHOT")
    lines.append("=" * 70)
    lines.append(f"OS:          {info['os']}")
    lines.append(f"Kernel:      {info['kernel']} ({info['arch']})")
    lines.append(f"Python:      {info['python_version']} at {info['python_path']}")
    lines.append(f"Service:     {SERVICE_NAME} ({info['service_status']})")
    lines.append(f"Nginx:       {info['nginx_status']}")
    lines.append(f"DB Size:     {info['db_size_mb']} MB ({info['db_size_gb']} GB)")
    lines.append(f"App Dir:     {APP_DIR} ({info['app_dir_size']})")
    lines.append(f"Disk:        {info['disk_used_gb']}GB used / {info['disk_total_gb']}GB total ({info['disk_free_gb']}GB free)")
    lines.append(f"Memory:      {info['memory']}")
    lines.append(f"CPU:         {info['cpu']}")
    lines.append(f"Run as:      {info['service_user'] or info['whoami']}")
    lines.append(f"Pip pkgs:    {info['pip_package_count']} packages installed")
    lines.append("")

    # Environment variable keys
    lines.append(subsection("Required Environment Variables"))
    if info["env_keys"]:
        for k in info["env_keys"]:
            lines.append(f"  {k}")
    else:
        lines.append("  (check .env file manually)")
    lines.append("")

    # File inventory
    lines.append(subsection("Files to Transfer"))
    for fname, exists, size in info["file_list"]:
        status = f"[YES {size}]" if exists else "[MISSING]"
        lines.append(f"  {status:<14} {fname}")
    lines.append("")
    for dname, exists, count in info["dir_list"]:
        status = f"[YES {count} files]" if exists else "[MISSING]"
        lines.append(f"  {status:<14} {dname}/")
    lines.append("")

    # SSL cert info
    lines.append(subsection("SSL Certificate"))
    lines.append(info["ssl_cert"] or "  (run: certbot certificates)")
    lines.append(f"  Expiry: {info['ssl_expiry'] or '(check manually)'}")
    lines.append("")

    # DDNS note
    lines.append(subsection("DDNS"))
    lines.append("  dashboard.rednun.com → Cloudflare A record (proxied)")
    lines.append("  DDNS script: /opt/rednun/ddns_update.sh")
    lines.append("  Token file:  /opt/rednun/.cloudflare_api_token (CREATE THIS ON BEELINK)")
    lines.append("")

    # ==============================
    # THE 15-STEP RUNBOOK
    # ==============================
    lines.append(section("MIGRATION RUNBOOK — 15 STEPS"))

    steps = [
        (
            "STEP 1: Prepare Beelink Hardware",
            [
                "- Connect Beelink SER5 to Chatham network via Ethernet (not WiFi)",
                "- Assign static local IP via router DHCP reservation",
                "- Note Beelink local IP (e.g. 192.168.1.100)",
                "- Ensure Chatham ISP modem/router allows port forwarding",
                "- Confirm you can SSH into Beelink from another machine",
            ],
        ),
        (
            "STEP 2: Install Ubuntu Server on Beelink",
            [
                "- Install Ubuntu 22.04 or 24.04 LTS Server (minimal install)",
                "- Create user: adduser rednun (or use root if preferred)",
                "- Enable SSH: sudo systemctl enable ssh && sudo systemctl start ssh",
                "- Run: sudo apt-get update && sudo apt-get upgrade -y",
            ],
        ),
        (
            "STEP 3: Install Python & System Dependencies",
            [
                f"- Target Python: {info['python_version']}",
                "- sudo apt-get install -y python3 python3-pip python3-venv",
                "- sudo apt-get install -y nginx certbot python3-certbot-nginx",
                "- sudo apt-get install -y sqlite3 libsqlite3-dev",
                "- sudo apt-get install -y curl git build-essential",
            ],
        ),
        (
            "STEP 4: Copy Application Files from DigitalOcean",
            [
                f"- From DigitalOcean (159.65.180.102), run:",
                f"  rsync -avz --exclude='*.pyc' --exclude='__pycache__'",
                f"      --exclude='toast_data.db'",
                f"      {APP_DIR}/ user@BEELINK_IP:{APP_DIR}/",
                f"- Then copy DB separately (it's {info['db_size_mb']} MB):",
                f"  rsync -avz --progress {DB_FILE} user@BEELINK_IP:{DB_FILE}",
                f"- Also copy: invoice_images/ invoice_images_archive/ static/",
                "- Verify all MISSING files above are created on Beelink",
            ],
        ),
        (
            "STEP 5: Install Python Dependencies",
            [
                f"- cd {APP_DIR}",
                "- pip3 install -r requirements.txt",
                "  (if requirements.txt missing, see pip packages list below)",
                "- Key packages: flask flask-cors gunicorn anthropic google-auth",
                "  google-auth-oauthlib google-api-python-client apscheduler",
                "  python-dotenv openpyxl requests",
                "- Test import: python3 -c 'import flask, anthropic, gunicorn'",
            ],
        ),
        (
            "STEP 6: Configure Environment Variables",
            [
                f"- Create {APP_DIR}/.env with these keys:",
            ]
            + [f"    {k}=<YOUR_VALUE>" for k in info["env_keys"]]
            + [
                "- chmod 600 .env",
                "- CRITICAL: Copy exact values from DigitalOcean .env",
                "  ssh root@159.65.180.102 cat /opt/rednun/.env",
            ],
        ),
        (
            "STEP 7: Create Cloudflare API Token & Configure DDNS",
            [
                "- Go to: https://dash.cloudflare.com/profile/api-tokens",
                "- Create token: Zone:DNS:Edit — scoped to rednun.com",
                "- Save token: echo 'YOUR_TOKEN' > /opt/rednun/.cloudflare_api_token",
                "- chmod 600 /opt/rednun/.cloudflare_api_token",
                "- Test: /opt/rednun/ddns_update.sh",
                "  Should log to /opt/rednun/ddns.log",
                "- First run will set initial IP even if 'unchanged'",
                "  (delete .ddns_last_ip to force update on first run)",
            ],
        ),
        (
            "STEP 8: Configure Port Forwarding on Chatham Router",
            [
                "- Log into Chatham router admin panel",
                "- Forward port 80  → Beelink LAN IP:80",
                "- Forward port 443 → Beelink LAN IP:443",
                "- (Port 8080 does NOT need to be exposed — Nginx proxies to it)",
                "- Verify ISP does not block inbound 80/443 (some residential ISPs do)",
                "  If blocked, use Cloudflare Tunnel instead of port forwarding",
            ],
        ),
        (
            "STEP 9: Configure Nginx on Beelink",
            [
                "- Copy or recreate Nginx config:",
                f"  sudo cp /etc/nginx/sites-available/{SERVICE_NAME} (from DO server)",
                f"  sudo ln -s /etc/nginx/sites-available/{SERVICE_NAME} /etc/nginx/sites-enabled/",
                "- Or recreate manually (proxy_pass http://127.0.0.1:8080)",
                "- Test: sudo nginx -t",
                "- Enable: sudo systemctl enable nginx && sudo systemctl start nginx",
            ],
        ),
        (
            "STEP 10: Obtain SSL Certificate via Let's Encrypt",
            [
                "- First, ensure dashboard.rednun.com DNS resolves to Beelink's public IP",
                "  (either via DDNS script or manual Cloudflare update)",
                "- IMPORTANT: Temporarily set Cloudflare to DNS-only (orange → grey cloud)",
                "  so Let's Encrypt can reach the server for domain validation",
                "- Run: sudo certbot --nginx -d dashboard.rednun.com",
                "- After cert issued, re-enable Cloudflare proxy (grey → orange cloud)",
                f"- Current cert expiry: {info['ssl_expiry'] or 'check certbot certificates'}",
                "- Auto-renewal: sudo systemctl enable certbot.timer",
            ],
        ),
        (
            "STEP 11: Create Systemd Service",
            [
                f"- Create /etc/systemd/system/{SERVICE_NAME}.service:",
                "  [Unit]",
                "  Description=Red Nun Dashboard",
                "  After=network.target",
                "",
                "  [Service]",
                f"  WorkingDirectory={APP_DIR}",
                f"  ExecStart=/usr/local/bin/gunicorn -w 2 -b 127.0.0.1:8080 server:app",
                "  Restart=always",
                "  RestartSec=5",
                f"  EnvironmentFile={APP_DIR}/.env",
                "",
                "  [Install]",
                "  WantedBy=multi-user.target",
                "",
                "- sudo systemctl daemon-reload",
                f"- sudo systemctl enable {SERVICE_NAME}",
                f"- sudo systemctl start {SERVICE_NAME}",
                f"- sudo systemctl status {SERVICE_NAME}",
                "- Test: curl http://127.0.0.1:8080/api/health",
            ],
        ),
        (
            "STEP 12: Set Up Cron Jobs",
            [
                "- crontab -e and add:",
                "  # Toast sync every 10 min during business hours",
                "  */10 6-23 * * * /opt/rednun/run_sync.sh >> /opt/rednun/logs/sync.log 2>&1",
                "",
                "  # MarginEdge sync daily 10:30 AM",
                "  30 10 * * * python3 /opt/rednun/marginedge_sync.py >> /opt/rednun/logs/me.log 2>&1",
                "",
                "  # Email invoice poller every 5 min",
                "  */5 * * * * python3 /opt/rednun/email_invoice_poller.py >> /opt/rednun/logs/email.log 2>&1",
                "",
                "  # Local invoice watcher every 5 min",
                "  */5 * * * * python3 /opt/rednun/local_invoice_watcher.py >> /opt/rednun/logs/watch.log 2>&1",
                "",
                "  # Thermostat fetch every 5 min",
                "  */5 * * * * python3 /opt/rednun/thermostat_fetch.py >> /opt/rednun/logs/therm.log 2>&1",
                "",
                "  # Sports guide daily 10 AM",
                "  0 10 * * * python3 /opt/rednun/sports_guide.py >> /opt/rednun/logs/sports.log 2>&1",
                "",
                "  # DDNS update every 5 min",
                "  */5 * * * * /opt/rednun/ddns_update.sh",
                "",
                "  # Nightly backup 3 AM",
                "  0 3 * * * /opt/rednun/backup.sh >> /opt/rednun/logs/backup.log 2>&1",
            ],
        ),
        (
            "STEP 13: Re-authorize Gmail OAuth Token",
            [
                "- The gmail_token.pickle from DO will NOT work on Beelink",
                "  (tokens are tied to the OAuth session, not the machine)",
                "- Re-run Gmail auth on Beelink:",
                "  python3 /opt/rednun/gmail_auth.py --url",
                "  (visit URL in browser, paste code back)",
                "  python3 /opt/rednun/gmail_auth.py --code <code>",
                "- Google Drive token (google_token.pickle) should transfer OK",
                "  but re-authorize if Drive watcher fails",
            ],
        ),
        (
            "STEP 14: Smoke Test Everything",
            [
                "- curl http://127.0.0.1:8080/api/health",
                "- curl -k https://dashboard.rednun.com/api/health",
                "- Test login at https://dashboard.rednun.com",
                "- Check invoice scanning (upload a test image)",
                "- Check Toast sync: POST /api/sync/daily",
                "- Check thermostat data: /api/thermostats",
                "- Check order guide: /order-guide",
                "- Check chalkboard: /specials",
                "- Verify DDNS log: tail /opt/rednun/ddns.log",
                "- Verify cron running: grep CRON /var/log/syslog | tail -20",
            ],
        ),
        (
            "STEP 15: Decommission DigitalOcean Droplet",
            [
                "- WAIT at least 1 week after Beelink migration is confirmed stable",
                "- Take final DB backup from DO: cp toast_data.db toast_data_do_final.db",
                "- Cancel DigitalOcean subscription (saves ~$6/month)",
                "- Keep DO snapshots for 30 days as fallback",
                "- Update any DNS records that still point to 159.65.180.102",
                "- Remove 159.65.180.102 from any firewall/access rules",
                "- Note: If Chatham ISP is unreliable, consider keeping DO as hot-standby",
            ],
        ),
    ]

    for i, (title, bullets) in enumerate(steps, 1):
        lines.append(f"\n{'─' * 70}")
        lines.append(f"  {title}")
        lines.append(f"{'─' * 70}")
        for b in bullets:
            lines.append(b)

    # Pip packages appendix
    lines.append(section("APPENDIX A: Installed Pip Packages"))
    lines.append(info["pip_packages"])

    # Crontab appendix
    lines.append(section("APPENDIX B: Current Crontab"))
    lines.append(info["crontab"] or "(empty)")

    # Nginx config appendix
    lines.append(section("APPENDIX C: Current Nginx Config"))
    lines.append(f"File: {info['nginx_conf_file']}")
    lines.append(info["nginx_conf"])

    # Service file appendix
    lines.append(section(f"APPENDIX D: Current Systemd Service ({SERVICE_NAME}.service)"))
    lines.append(info["service_file"])

    # Env vars (redacted)
    lines.append(section("APPENDIX E: Environment Variables (secrets redacted)"))
    lines.append(info["env_vars"])

    lines.append(f"\n{'=' * 70}")
    lines.append(f"  END OF RUNBOOK — Generated {info['date']}")
    lines.append(f"{'=' * 70}\n")

    return "\n".join(lines)


def main():
    print("Red Nun Dashboard — Migration Runbook Generator")
    print(f"Inspecting server...")

    info = gather_info()

    print(f"  OS: {info['os']}")
    print(f"  Python: {info['python_version']}")
    print(f"  DB size: {info['db_size_mb']} MB")
    print(f"  App dir: {info['app_dir_size']}")
    print(f"  Pip packages: {info['pip_package_count']}")

    print(f"\nGenerating runbook...")
    runbook = build_runbook(info)

    with open(OUTPUT_FILE, "w") as f:
        f.write(runbook)

    print(f"\nRunbook written to: {OUTPUT_FILE}")
    print(f"  {round(os.path.getsize(OUTPUT_FILE) / 1024, 1)} KB")
    print("\nDone. Review the runbook before migration.")


if __name__ == "__main__":
    main()
