#!/bin/bash
# ──────────────────────────────────────────────────────────────────────
# Vendor Scraper Orchestrator — Red Nun Dashboard
# Runs all vendor scrapers in sequence, then imports downloaded files.
# Each scraper updates vendor_session_status via the dashboard API.
# If a scraper exits non-zero, marks it as expired in the API.
#
# Cron: 0 7 * * * cd ~/vendor-scrapers && ./run_all.sh >> ~/vendor-scrapers/run_all.log 2>&1
# ──────────────────────────────────────────────────────────────────────

PYTHON="/opt/rednun/venv/bin/python3"
API="http://127.0.0.1:8080"
LOG_PREFIX="[run_all]"

echo ""
echo "=================================================================="
echo "$LOG_PREFIX Vendor Scraper Run: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================================="

# Track overall stats
TOTAL_OK=0
TOTAL_FAIL=0
FAILED_VENDORS=""

# ── Helper: run a scraper and handle exit code ───────────────────────
run_scraper() {
    local VENDOR_NAME="$1"
    local SCRAPER_DIR="$2"
    local SCRAPER_CMD="$3"
    local SESSION_NAME="$4"  # Name used in vendor_session_status table

    echo ""
    echo "──────────────────────────────────────────────────────────────"
    echo "$LOG_PREFIX Starting: $VENDOR_NAME"
    echo "$LOG_PREFIX   Dir: $SCRAPER_DIR"
    echo "$LOG_PREFIX   Cmd: $SCRAPER_CMD"
    echo "──────────────────────────────────────────────────────────────"

    if [ ! -d "$SCRAPER_DIR" ]; then
        echo "$LOG_PREFIX   [SKIP] Directory not found: $SCRAPER_DIR"
        return
    fi

    cd "$SCRAPER_DIR" || return

    # Run the scraper with a 10-minute timeout
    timeout 600 $PYTHON $SCRAPER_CMD 2>&1
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "$LOG_PREFIX   [OK] $VENDOR_NAME completed successfully"
        TOTAL_OK=$((TOTAL_OK + 1))
        # Safety net: clear any stale expired status
        curl -s -X POST "$API/api/vendor-sessions/update" \
            -H "Content-Type: application/json" \
            -d "{\"vendor_name\":\"$SESSION_NAME\",\"status\":\"healthy\"}" \
            > /dev/null 2>&1
    elif [ $EXIT_CODE -eq 124 ]; then
        # timeout killed it
        echo "$LOG_PREFIX   [TIMEOUT] $VENDOR_NAME killed after 10 minutes"
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        FAILED_VENDORS="$FAILED_VENDORS $VENDOR_NAME(timeout)"
        # Update session status to reflect the timeout
        curl -s -X POST "$API/api/vendor-sessions/update" \
            -H "Content-Type: application/json" \
            -d "{\"vendor_name\":\"$SESSION_NAME\",\"status\":\"expired\",\"failure_reason\":\"timeout_in_run_all\"}" \
            > /dev/null 2>&1
    else
        echo "$LOG_PREFIX   [FAIL] $VENDOR_NAME exited with code $EXIT_CODE"
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        FAILED_VENDORS="$FAILED_VENDORS $VENDOR_NAME(exit:$EXIT_CODE)"
        # Scraper already updates its own status on failure, but mark expired
        # as a safety net if the scraper crashed before updating
        curl -s -X POST "$API/api/vendor-sessions/update" \
            -H "Content-Type: application/json" \
            -d "{\"vendor_name\":\"$SESSION_NAME\",\"status\":\"expired\",\"failure_reason\":\"scraper_crash_exit_$EXIT_CODE\"}" \
            > /dev/null 2>&1
    fi
}

# ── Run all scrapers ─────────────────────────────────────────────────

# 1. US Foods (CSV downloads)
run_scraper \
    "US Foods" \
    "$HOME/usfoods-scraper" \
    "usfoods_invoice_scraper.py" \
    "US Foods"

# 2. PFG (PDF downloads)
run_scraper \
    "PFG" \
    "$HOME/vendor-scrapers/pfg" \
    "scraper.py" \
    "Performance Foodservice"

# 3. VTInfo — L. Knife + Colonial (CSV downloads)
run_scraper \
    "VTInfo (L. Knife + Colonial)" \
    "$HOME/vendor-scrapers/vtinfo" \
    "scraper.py" \
    "L. Knife & Son, Inc."

# 4. Southern Glazer's — Chatham (PDF downloads)
run_scraper \
    "Southern Glazer's (Chatham)" \
    "$HOME/vendor-scrapers/southern-glazers" \
    "scraper_chatham.py" \
    "Southern Glazer's Beverage Company (chatham)"

# 5. Southern Glazer's — Dennis (PDF downloads)
run_scraper \
    "Southern Glazer's (Dennis)" \
    "$HOME/vendor-scrapers/southern-glazers" \
    "scraper_dennis.py" \
    "Southern Glazer's Beverage Company (dennis)"

# 6. Martignetti (PDF downloads)
run_scraper \
    "Martignetti" \
    "$HOME/vendor-scrapers/martignetti" \
    "scraper.py" \
    "Martignetti Companies"

# 7. Craft Collective (PDF downloads)
run_scraper \
    "Craft Collective" \
    "$HOME/vendor-scrapers/craft-collective" \
    "scraper.py" \
    "Craft Collective Inc"

# ── Import downloaded CSV files ──────────────────────────────────────

echo ""
echo "──────────────────────────────────────────────────────────────"
echo "$LOG_PREFIX Running import_downloads.py..."
echo "──────────────────────────────────────────────────────────────"

cd "$HOME/vendor-scrapers" || true
$PYTHON common/import_downloads.py 2>&1
IMPORT_EXIT=$?

if [ $IMPORT_EXIT -eq 0 ]; then
    echo "$LOG_PREFIX   [OK] Import completed"
else
    echo "$LOG_PREFIX   [WARN] Import exited with code $IMPORT_EXIT"
fi

# ── Summary ──────────────────────────────────────────────────────────

echo ""
echo "=================================================================="
echo "$LOG_PREFIX SUMMARY — $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================================="
echo "$LOG_PREFIX   Scrapers OK:     $TOTAL_OK"
echo "$LOG_PREFIX   Scrapers FAILED: $TOTAL_FAIL"
if [ -n "$FAILED_VENDORS" ]; then
    echo "$LOG_PREFIX   Failed:$FAILED_VENDORS"
fi
echo "=================================================================="
