"""
Email Reports — Executive Summary Style
Sends automated weekly reports with narrative analysis for each location.
Matches the Red Nun manager's report format with data-driven insights.
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta
from dotenv import load_dotenv

from data_store import get_connection
from analytics import (
    get_daily_revenue,
)
from export import generate_weekly_excel

# 7shifts labor (replaces Toast labor)
try:
    from sevenshifts_client import get_labor_for_report
    SEVENSHIFTS_AVAILABLE = True
except ImportError:
    SEVENSHIFTS_AVAILABLE = False

load_dotenv()
logger = logging.getLogger(__name__)

LOCATION_NAMES = {
    "dennis": "Dennis Port",
    "chatham": "Chatham",
    None: "Both Locations",
}

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _get_last_week_range():
    """Get Monday-Sunday of the previous week."""
    today = datetime.now().date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday.strftime("%Y%m%d"), last_sunday.strftime("%Y%m%d")


def _fmt(val):
    """Format as currency."""
    if val is None or val == 0:
        return "$0"
    if abs(val) >= 1000:
        return f"${val:,.0f}"
    return f"${val:,.2f}"


def _fmt2(val):
    """Format as currency with cents."""
    if val is None:
        return "$0.00"
    return f"${val:,.2f}"


def _pct(val):
    if val is None:
        return "0.0%"
    return f"{val:.1f}%"


def _get_daily_breakdown(location, start_date, end_date):
    """Get daily revenue with day names for narrative use."""
    daily = get_daily_revenue(location, start_date, end_date)
    if not daily:
        return [], 0, 0, None, None, 0

    # Group by date
    by_date = {}
    for d in daily:
        bd = d["business_date"]
        if bd not in by_date:
            by_date[bd] = {"date": bd, "revenue": 0, "orders": 0, "discounts": 0}
        by_date[bd]["revenue"] += d["net_revenue"]
        by_date[bd]["orders"] += d["order_count"]
        by_date[bd]["discounts"] += d.get("discounts", 0)

    days = sorted(by_date.values(), key=lambda x: x["date"])
    total_rev = sum(d["revenue"] for d in days)
    total_orders = sum(d["orders"] for d in days)
    total_discounts = sum(d["discounts"] for d in days)

    # Best and worst day
    best = max(days, key=lambda x: x["revenue"]) if days else None
    worst = min(days, key=lambda x: x["revenue"]) if days else None

    # Add day names
    for d in days:
        try:
            dt = datetime.strptime(d["date"], "%Y%m%d")
            d["day_name"] = DAY_NAMES[dt.weekday()]
            d["short_date"] = dt.strftime("%b %d")
        except (ValueError, TypeError):
            d["day_name"] = "?"
            d["short_date"] = d["date"]

    return days, total_rev, total_orders, best, worst, total_discounts


def _get_labor_detail(location, start_date, end_date, total_rev):
    """Get labor breakdown from 7shifts (includes salaried + hourly)."""
    if not SEVENSHIFTS_AVAILABLE:
        return {
            "cost": 0, "pct": 0, "hours": 0, "foh_cost": 0, "boh_cost": 0,
            "mgmt_cost": 0, "foh_pct": 0, "boh_pct": 0, "splh": 0,
            "salaried_cost": 0, "hourly_cost": 0, "overtime_hours": 0,
            "overtime_cost": 0, "total_tips": 0, "employee_count": 0,
            "by_role": [], "by_employee": [],
        }

    labor = get_labor_for_report(location, start_date, end_date)

    labor_cost = labor.get("total_labor_cost") or 0
    total_hours = labor.get("total_hours") or 0
    foh_cost = labor.get("foh_cost") or 0
    boh_cost = labor.get("boh_cost") or 0
    mgmt_cost = labor.get("mgmt_cost") or 0

    # Calculate percentages against revenue
    labor_pct = (labor_cost / total_rev * 100) if total_rev > 0 else 0
    foh_pct = (foh_cost / total_rev * 100) if total_rev > 0 else 0
    boh_pct = (boh_cost / total_rev * 100) if total_rev > 0 else 0
    mgmt_pct = (mgmt_cost / total_rev * 100) if total_rev > 0 else 0

    # SPLH (sales per labor hour)
    splh = total_rev / total_hours if total_hours > 0 else 0

    return {
        "cost": labor_cost,
        "pct": labor_pct,
        "hours": total_hours,
        "foh_cost": foh_cost,
        "boh_cost": boh_cost,
        "mgmt_cost": mgmt_cost,
        "foh_pct": foh_pct,
        "boh_pct": boh_pct,
        "mgmt_pct": mgmt_pct,
        "splh": splh,
        "salaried_cost": labor.get("salaried_cost", 0),
        "hourly_cost": labor.get("hourly_cost", 0),
        "overtime_hours": labor.get("overtime_hours", 0),
        "overtime_cost": labor.get("overtime_cost", 0),
        "total_tips": labor.get("total_tips", 0),
        "employee_count": labor.get("employee_count", 0),
        "by_role": labor.get("by_role", []),
        "by_employee": labor.get("by_employee", []),
    }


def _get_cogs_data(location, days_back=30):
    """Get COGS data from MarginEdge if available."""
    conn = get_connection()
    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        if location:
            rows = conn.execute("""
                SELECT category_type, total_cost, invoice_count
                FROM me_cogs_summary
                WHERE location = ? AND period_start >= ? AND period_end <= ?
                  AND category_type != 'NON_COGS'
                ORDER BY total_cost DESC
            """, (location, start_date, end_date)).fetchall()
        else:
            rows = conn.execute("""
                SELECT category_type, SUM(total_cost) as total_cost,
                       SUM(invoice_count) as invoice_count
                FROM me_cogs_summary
                WHERE period_start >= ? AND period_end <= ?
                  AND category_type != 'NON_COGS'
                GROUP BY category_type
                ORDER BY total_cost DESC
            """, (start_date, end_date)).fetchall()

        cogs = [dict(r) for r in rows]
        total_cogs = sum(c["total_cost"] for c in cogs)
        conn.close()
        return cogs, total_cogs
    except Exception:
        conn.close()
        return [], 0


def _labor_status(pct):
    """Return color and label for labor percentage."""
    if pct <= 24:
        return "#3BA67C", "On Target"
    elif pct <= 27:
        return "#3BA67C", "Acceptable"
    elif pct <= 30:
        return "#D4943A", "Watch"
    else:
        return "#C43B3B", "Over Target"


def _build_executive_html(start_date, end_date, location=None):
    """Build executive summary email in manager's report style."""

    loc_name = LOCATION_NAMES.get(location, "Both Locations")

    # Pull all data
    days, total_rev, total_orders, best_day, worst_day, total_discounts = \
        _get_daily_breakdown(location, start_date, end_date)

    labor = _get_labor_detail(location, start_date, end_date, total_rev)
    cogs, total_cogs = _get_cogs_data(location)

    avg_check = total_rev / total_orders if total_orders > 0 else 0
    operating_days = len(days)
    avg_daily_rev = total_rev / operating_days if operating_days > 0 else 0
    discount_pct = (total_discounts / (total_rev + total_discounts) * 100) if (total_rev + total_discounts) > 0 else 0

    # COGS as % of revenue (annualize weekly to compare with 30-day COGS)
    cogs_pct = (total_cogs / total_rev * 100) if total_rev > 0 else 0

    # Format dates
    start_fmt = datetime.strptime(start_date, "%Y%m%d").strftime("%b %d")
    end_fmt = datetime.strptime(end_date, "%Y%m%d").strftime("%b %d, %Y")
    start_short = datetime.strptime(start_date, "%Y%m%d").strftime("%B %d")
    end_short = datetime.strptime(end_date, "%Y%m%d").strftime("%B %d")

    # Labor color
    labor_color, labor_status = _labor_status(labor["pct"])

    # Header color per location
    header_colors = {
        "dennis": "#C43B3B",
        "chatham": "#4A7FD4",
        None: "#2D3142",
    }
    header_bg = header_colors.get(location, "#C43B3B")

    # Build daily revenue rows for the scorecard table
    daily_rows = ""
    for d in days:
        rev_bar_width = min(int((d["revenue"] / (best_day["revenue"] if best_day else 1)) * 100), 100)
        daily_rows += f"""
        <tr>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;width:60px;">{d['day_name']}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;width:70px;">{d['short_date']}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;font-weight:600;width:80px;">{_fmt(d['revenue'])}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;width:50px;color:#888;">{d['orders']} ord</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;">
                <div style="background:#e8e8e8;border-radius:4px;height:14px;width:100%;">
                    <div style="background:{header_bg};border-radius:4px;height:14px;width:{rev_bar_width}%;"></div>
                </div>
            </td>
        </tr>"""

    # Build COGS rows
    cogs_rows = ""
    for c in cogs:
        cogs_rows += f"""
        <tr>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;">{c['category_type']}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;font-weight:600;">{_fmt(c['total_cost'])}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#888;">{c['invoice_count']} invoices</td>
        </tr>"""

    # Build labor by role rows (exclude management salary)
    role_rows = ""
    for r in labor.get("by_role", []):
        if r["role"] == "Management (Salary)":
            continue
        role_pct = (r["pay"] / labor["cost"] * 100) if labor["cost"] > 0 else 0
        role_rows += f"""
        <tr>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;">{r['role']}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;font-weight:600;">{_fmt(r['pay'])}</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#888;">{r['hours']:.0f} hrs</td>
            <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#888;">{r['shifts']} shifts</td>
        </tr>"""

    # Build narrative summary
    best_day_text = ""
    if best_day:
        try:
            dt = datetime.strptime(best_day["date"], "%Y%m%d")
            best_day_name = DAY_NAMES[dt.weekday()]
            best_day_short = dt.strftime("%b %d")
            best_day_text = f"{best_day_name} {best_day_short} was the best day at {_fmt(best_day['revenue'])} on {best_day['orders']} orders."
        except (ValueError, TypeError):
            best_day_text = ""

    worst_day_text = ""
    if worst_day and best_day and worst_day["date"] != best_day["date"]:
        try:
            dt = datetime.strptime(worst_day["date"], "%Y%m%d")
            worst_day_name = DAY_NAMES[dt.weekday()]
            worst_day_short = dt.strftime("%b %d")
            worst_day_text = f" Slowest was {worst_day_name} {worst_day_short} at {_fmt(worst_day['revenue'])}."
        except (ValueError, TypeError):
            worst_day_text = ""

    # Labor narrative
    labor_narrative = ""
    if labor["pct"] > 28:
        labor_narrative = f"""
        <p style="margin:12px 0;font-size:14px;line-height:1.6;">
            <strong>Labor needs attention.</strong> Total payroll ran <span style="color:{labor_color};font-weight:600;">{_pct(labor['pct'])}</span>
            of net sales ({_fmt(labor['cost'])} on {labor['hours']:,.0f} hours, {labor.get('employee_count',0)} employees).
            FOH was {_pct(labor['foh_pct'])} ({_fmt(labor['foh_cost'])}), BOH was {_pct(labor['boh_pct'])} ({_fmt(labor['boh_cost'])}).
            SPLH was {_fmt2(labor['splh'])}. There may be opportunity to tighten scheduling on slower days.
        </p>"""
    else:
        labor_narrative = f"""
        <p style="margin:12px 0;font-size:14px;line-height:1.6;">
            <strong>Labor is on target</strong> at <span style="color:{labor_color};font-weight:600;">{_pct(labor['pct'])}</span>
            ({_fmt(labor['cost'])} on {labor['hours']:,.0f} hours, {labor.get('employee_count',0)} employees).
            FOH {_pct(labor['foh_pct'])} ({_fmt(labor['foh_cost'])}), BOH {_pct(labor['boh_pct'])} ({_fmt(labor['boh_cost'])}).
            SPLH: {_fmt2(labor['splh'])}.
        </p>"""

    # Add salaried callout if present
    if labor.get("salaried_cost", 0) > 0:
        labor_narrative += f"""
        <p style="margin:4px 0 12px;font-size:13px;line-height:1.5;color:#666;">
            Includes {_fmt(labor['salaried_cost'])} salaried and {_fmt(labor['hourly_cost'])} hourly labor.
        </p>"""

    # Add overtime callout if present
    if labor.get("overtime_hours", 0) > 0:
        labor_narrative += f"""
        <p style="margin:4px 0 12px;font-size:13px;line-height:1.5;color:#C43B3B;">
            &#9888; Overtime: {labor['overtime_hours']:.1f} hours ({_fmt(labor['overtime_cost'])})
        </p>"""

    # Discount narrative
    discount_narrative = ""
    if discount_pct > 3:
        discount_narrative = f"""
        <p style="margin:12px 0;font-size:14px;line-height:1.6;">
            <strong>Discounts:</strong> {_fmt(total_discounts)} total ({_pct(discount_pct)} of gross sales). Worth reviewing if promotional discounts are optimized.
        </p>"""
    elif total_discounts > 0:
        discount_narrative = f"""
        <p style="margin:12px 0;font-size:14px;line-height:1.6;">
            <strong>Discounts:</strong> {_fmt(total_discounts)} ({_pct(discount_pct)} of gross) — well controlled.
        </p>"""

    # COGS narrative
    cogs_narrative = ""
    if total_cogs > 0:
        food_cost = next((c["total_cost"] for c in cogs if c["category_type"] == "FOOD"), 0)
        bev_cost = sum(c["total_cost"] for c in cogs if c["category_type"] in ("LIQUOR", "BEER", "WINE"))
        cogs_narrative = f"""
        <p style="margin:12px 0;font-size:14px;line-height:1.6;">
            <strong>Purchasing (30-day rolling):</strong> {_fmt(total_cogs)} total COGS.
            Food {_fmt(food_cost)}, beverages {_fmt(bev_cost)}.
        </p>"""

    html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#333;max-width:640px;margin:0 auto;">

        <!-- Header -->
        <div style="background:{header_bg};padding:24px 28px;border-radius:8px 8px 0 0;">
            <div style="font-size:11px;color:rgba(255,255,255,0.6);text-transform:uppercase;letter-spacing:2px;margin-bottom:4px;">Red Nun Bar &amp; Grill</div>
            <h1 style="color:white;margin:0;font-size:22px;">{loc_name} — Weekly Report</h1>
            <p style="color:rgba(255,255,255,0.75);margin:6px 0 0;font-size:14px;">
                {start_short} &ndash; {end_short} | {operating_days} Operating Days
            </p>
        </div>

        <!-- Situation Summary -->
        <div style="padding:24px 28px;border-left:1px solid #eee;border-right:1px solid #eee;">
            <p style="margin:0 0 12px;font-size:14px;line-height:1.6;">
                Net sales for the week were <strong>{_fmt(total_rev)}</strong> across {total_orders} orders,
                averaging {_fmt(avg_daily_rev)} per day with a <strong>{_fmt2(avg_check)}</strong> average check.
                {best_day_text}{worst_day_text}
            </p>

            {labor_narrative}
            {discount_narrative}
            {cogs_narrative}
        </div>

        <!-- Performance Scorecard -->
        <div style="padding:0 28px 20px;border-left:1px solid #eee;border-right:1px solid #eee;">
            <h2 style="font-size:15px;margin:0 0 12px;color:#333;border-bottom:2px solid {header_bg};padding-bottom:8px;">
                Performance Scorecard
            </h2>
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
                <tr style="background:#f7f7f7;">
                    <td style="padding:10px 14px;font-size:12px;color:#888;text-transform:uppercase;width:25%;text-align:center;">
                        Net Sales<br>
                        <span style="font-size:22px;font-weight:700;color:#333;">{_fmt(total_rev)}</span>
                    </td>
                    <td style="padding:10px 14px;font-size:12px;color:#888;text-transform:uppercase;width:25%;text-align:center;">
                        Labor %<br>
                        <span style="font-size:22px;font-weight:700;color:{labor_color};">{_pct(labor['pct'])}</span>
                    </td>
                    <td style="padding:10px 14px;font-size:12px;color:#888;text-transform:uppercase;width:25%;text-align:center;">
                        Avg Check<br>
                        <span style="font-size:22px;font-weight:700;color:#333;">{_fmt2(avg_check)}</span>
                    </td>
                    <td style="padding:10px 14px;font-size:12px;color:#888;text-transform:uppercase;width:25%;text-align:center;">
                        Orders<br>
                        <span style="font-size:22px;font-weight:700;color:#333;">{total_orders}</span>
                    </td>
                </tr>
            </table>

            <!-- Labor Detail Row -->
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
                <tr style="background:#fafafa;">
                    <td style="padding:8px 14px;font-size:12px;text-align:center;width:25%;">
                        <span style="color:#888;">Labor $</span><br>
                        <span style="font-weight:600;">{_fmt(labor['cost'])}</span>
                    </td>
                    <td style="padding:8px 14px;font-size:12px;text-align:center;width:25%;">
                        <span style="color:#888;">Hours</span><br>
                        <span style="font-weight:600;">{labor['hours']:,.0f}</span>
                    </td>
                    <td style="padding:8px 14px;font-size:12px;text-align:center;width:25%;">
                        <span style="color:#888;">FOH %</span><br>
                        <span style="font-weight:600;">{_pct(labor['foh_pct'])}</span>
                    </td>
                    <td style="padding:8px 14px;font-size:12px;text-align:center;width:25%;">
                        <span style="color:#888;">BOH %</span><br>
                        <span style="font-weight:600;">{_pct(labor['boh_pct'])}</span>
                    </td>
                </tr>
            </table>
        </div>

        <!-- Daily Breakdown -->
        <div style="padding:0 28px 20px;border-left:1px solid #eee;border-right:1px solid #eee;">
            <h2 style="font-size:15px;margin:0 0 12px;color:#333;border-bottom:2px solid {header_bg};padding-bottom:8px;">
                Daily Breakdown
            </h2>
            <table width="100%" cellpadding="0" cellspacing="0">
                {daily_rows}
                <tr style="background:#f7f7f7;font-weight:700;">
                    <td style="padding:8px 10px;font-size:13px;" colspan="2">TOTAL</td>
                    <td style="padding:8px 10px;font-size:13px;">{_fmt(total_rev)}</td>
                    <td style="padding:8px 10px;font-size:13px;color:#888;">{total_orders} ord</td>
                    <td></td>
                </tr>
            </table>
        </div>

        <!-- COGS Breakdown (if available) -->
        {"" if not role_rows else f'''
        <div style="padding:0 28px 20px;border-left:1px solid #eee;border-right:1px solid #eee;">
            <h2 style="font-size:15px;margin:0 0 12px;color:#333;border-bottom:2px solid {header_bg};padding-bottom:8px;">
                Labor by Role &mdash; 7shifts
            </h2>
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr style="background:#f4f4f4;">
                    <th style="padding:6px 10px;text-align:left;font-size:12px;font-weight:600;">Role</th>
                    <th style="padding:6px 10px;text-align:left;font-size:12px;font-weight:600;">Pay</th>
                    <th style="padding:6px 10px;text-align:left;font-size:12px;font-weight:600;">Hours</th>
                    <th style="padding:6px 10px;text-align:left;font-size:12px;font-weight:600;">Shifts</th>
                </tr>
                {role_rows}
                <tr style="background:#f7f7f7;font-weight:700;">
                    <td style="padding:8px 10px;font-size:13px;">TOTAL</td>
                    <td style="padding:8px 10px;font-size:13px;">{_fmt(labor["cost"])}</td>
                    <td style="padding:8px 10px;font-size:13px;">{labor["hours"]:.0f} hrs</td>
                    <td></td>
                </tr>
            </table>
        </div>
        '''}

        <!-- COGS Breakdown (if available) -->
        {"" if not cogs_rows else f'''
        <div style="padding:0 28px 20px;border-left:1px solid #eee;border-right:1px solid #eee;">
            <h2 style="font-size:15px;margin:0 0 12px;color:#333;border-bottom:2px solid {header_bg};padding-bottom:8px;">
                Purchasing (30-Day Rolling) &mdash; MarginEdge
            </h2>
            <table width="100%" cellpadding="0" cellspacing="0">
                {cogs_rows}
                <tr style="background:#f7f7f7;font-weight:700;">
                    <td style="padding:8px 10px;font-size:13px;">TOTAL</td>
                    <td style="padding:8px 10px;font-size:13px;">{_fmt(total_cogs)}</td>
                    <td></td>
                </tr>
            </table>
        </div>
        '''}

        <!-- Footer -->
        <div style="padding:16px 28px;background:#f8f8f8;border:1px solid #eee;
                     border-radius:0 0 8px 8px;font-size:12px;color:#888;">
            Full Excel report attached. Live dashboard:
            <a href="http://localhost:8080" style="color:{header_bg};">localhost:8080</a>
            <br>Generated automatically by Red Nun Analytics &middot; Toast POS + 7shifts + MarginEdge
        </div>

    </body>
    </html>
    """
    return html


def _send_email(subject, html_body, excel_path, gmail_user, gmail_password, recipient_list):
    """Send a single email with HTML body and Excel attachment."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipient_list)

    msg.attach(MIMEText(html_body, "html"))

    with open(excel_path, "rb") as f:
        attachment = MIMEBase("application", "octet-stream")
        attachment.set_payload(f.read())
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition",
            f"attachment; filename={os.path.basename(excel_path)}",
        )
        msg.attach(attachment)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, recipient_list, msg.as_string())


def send_weekly_report():
    """Generate and email weekly reports — one per location + combined."""
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    recipients = os.getenv("REPORT_RECIPIENTS", gmail_user)

    if not gmail_user or not gmail_password:
        logger.error("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        return False

    recipient_list = [r.strip() for r in recipients.split(",")]

    start_date, end_date = _get_last_week_range()
    start_fmt = datetime.strptime(start_date, "%Y%m%d").strftime("%b %d")
    end_fmt = datetime.strptime(end_date, "%Y%m%d").strftime("%b %d")

    logger.info(f"Generating weekly reports for {start_date} to {end_date}...")

    try:
        reports = [
            ("dennis", "Dennis Port"),
            ("chatham", "Chatham"),
        ]

        for location, loc_name in reports:
            excel_path = generate_weekly_excel(start_date, end_date, location=location)
            subject = f"Red Nun Weekly \u2014 {loc_name} \u2014 {start_fmt} to {end_fmt}"
            html_body = _build_executive_html(start_date, end_date, location)
            _send_email(subject, html_body, excel_path, gmail_user, gmail_password, recipient_list)
            logger.info(f"  Sent {loc_name} report to {', '.join(recipient_list)}")

        logger.info("All weekly reports sent successfully")
        return True

    except Exception as e:
        logger.error(f"Failed to send weekly reports: {e}")
        return False


# CLI entry point
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) == 3:
        start, end = sys.argv[1], sys.argv[2]
        excel_path = generate_weekly_excel(start, end)
        for loc in ["dennis", "chatham", None]:
            html = _build_executive_html(start, end, loc)
            name = loc or "combined"
            preview_path = os.path.join(
                os.path.dirname(__file__), "exports", f"email_preview_{name}.html"
            )
            with open(preview_path, "w") as f:
                f.write(html)
            print(f"Preview: {preview_path}")
    else:
        send_weekly_report()
