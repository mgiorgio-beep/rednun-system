"""
Email Reports
Sends automated weekly reports with Excel attachment every Monday morning.
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

from analytics import (
    get_daily_revenue,
    get_labor_summary,
    get_pour_cost_by_category,
    get_server_performance,
)
from export import generate_weekly_excel

load_dotenv()
logger = logging.getLogger(__name__)


def _get_last_week_range():
    """Get Monday-Sunday of the previous week."""
    today = datetime.now().date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday.strftime("%Y%m%d"), last_sunday.strftime("%Y%m%d")


def _format_money(val):
    """Format a number as currency."""
    if val is None:
        return "$0.00"
    return f"${val:,.2f}"


def _format_pct(val):
    """Format a number as percentage."""
    if val is None:
        return "0.0%"
    return f"{val:.1f}%"


def _build_summary_html(start_date, end_date):
    """Build an HTML email body with key metrics summary."""

    # Pull data for both locations
    daily_rev = get_daily_revenue(None, start_date, end_date)
    labor = get_labor_summary(None, start_date, end_date)
    pour = get_pour_cost_by_category(None, start_date, end_date)
    servers = get_server_performance(None, start_date, end_date, limit=5)

    # Aggregate revenue
    total_revenue = sum(d["net_revenue"] for d in daily_rev) if daily_rev else 0
    total_tips = sum(d["tips"] for d in daily_rev) if daily_rev else 0
    total_orders = sum(d["order_count"] for d in daily_rev) if daily_rev else 0
    avg_check = total_revenue / total_orders if total_orders > 0 else 0

    # Revenue by location
    dennis_rev = sum(d["net_revenue"] for d in daily_rev if d["location"] == "dennis")
    chatham_rev = sum(d["net_revenue"] for d in daily_rev if d["location"] == "chatham")

    # Labor
    labor_cost = labor.get("total_labor_cost") or 0
    labor_pct = labor.get("labor_pct") or 0
    total_hours = labor.get("total_hours") or 0

    # Pour cost
    total_bev_rev = sum(p["revenue"] for p in pour) if pour else 0
    total_bev_cost = sum(p["cost"] for p in pour) if pour else 0
    overall_pour_pct = (total_bev_cost / total_bev_rev * 100) if total_bev_rev > 0 else 0

    # Format date range for display
    start_fmt = datetime.strptime(start_date, "%Y%m%d").strftime("%b %d")
    end_fmt = datetime.strptime(end_date, "%Y%m%d").strftime("%b %d, %Y")

    # Build pour cost rows
    pour_rows = ""
    for p in pour:
        color = "#3BA67C" if (p["pour_cost_pct"] or 0) <= 25 else (
            "#D4943A" if (p["pour_cost_pct"] or 0) <= 30 else "#C43B3B"
        )
        pour_rows += f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">{p['category']}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">{_format_money(p['revenue'])}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;color:{color};font-weight:600;">
                {_format_pct(p['pour_cost_pct'])}
            </td>
        </tr>"""

    # Build server rows
    server_rows = ""
    for i, s in enumerate(servers, 1):
        server_rows += f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">{i}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">{s['server_name']}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">{s['location']}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">{_format_money(s['total_sales'])}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">{_format_money(s['avg_check'])}</td>
        </tr>"""

    # Labor color
    labor_color = "#3BA67C" if labor_pct <= 28 else (
        "#D4943A" if labor_pct <= 31 else "#C43B3B"
    )

    html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#333;max-width:640px;margin:0 auto;">

        <!-- Header -->
        <div style="background:#C43B3B;padding:24px;border-radius:8px 8px 0 0;">
            <h1 style="color:white;margin:0;font-size:22px;">Red Nun Weekly Report</h1>
            <p style="color:rgba(255,255,255,0.8);margin:6px 0 0;font-size:14px;">
                {start_fmt} — {end_fmt}
            </p>
        </div>

        <!-- KPIs -->
        <div style="background:#f8f8f8;padding:20px;border:1px solid #eee;">
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td style="padding:12px;text-align:center;width:25%;">
                        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;">Revenue</div>
                        <div style="font-size:24px;font-weight:700;color:#333;margin-top:4px;">{_format_money(total_revenue)}</div>
                        <div style="font-size:11px;color:#888;margin-top:2px;">DP: {_format_money(dennis_rev)} · CH: {_format_money(chatham_rev)}</div>
                    </td>
                    <td style="padding:12px;text-align:center;width:25%;">
                        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;">Labor %</div>
                        <div style="font-size:24px;font-weight:700;color:{labor_color};margin-top:4px;">{_format_pct(labor_pct)}</div>
                        <div style="font-size:11px;color:#888;margin-top:2px;">{_format_money(labor_cost)} · {total_hours:,.0f} hrs</div>
                    </td>
                    <td style="padding:12px;text-align:center;width:25%;">
                        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;">Pour Cost</div>
                        <div style="font-size:24px;font-weight:700;color:#333;margin-top:4px;">{_format_pct(overall_pour_pct)}</div>
                        <div style="font-size:11px;color:#888;margin-top:2px;">Bev rev: {_format_money(total_bev_rev)}</div>
                    </td>
                    <td style="padding:12px;text-align:center;width:25%;">
                        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;">Avg Check</div>
                        <div style="font-size:24px;font-weight:700;color:#333;margin-top:4px;">{_format_money(avg_check)}</div>
                        <div style="font-size:11px;color:#888;margin-top:2px;">{total_orders} orders</div>
                    </td>
                </tr>
            </table>
        </div>

        <!-- Pour Cost Breakdown -->
        <div style="padding:20px;">
            <h2 style="font-size:16px;margin:0 0 12px;color:#333;">Pour Cost by Category</h2>
            <table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;">
                <tr style="background:#f4f4f4;">
                    <th style="padding:8px 12px;text-align:left;font-weight:600;">Category</th>
                    <th style="padding:8px 12px;text-align:left;font-weight:600;">Revenue</th>
                    <th style="padding:8px 12px;text-align:left;font-weight:600;">Pour Cost %</th>
                </tr>
                {pour_rows}
            </table>
        </div>

        <!-- Top Servers -->
        <div style="padding:0 20px 20px;">
            <h2 style="font-size:16px;margin:0 0 12px;color:#333;">Top 5 Servers</h2>
            <table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;">
                <tr style="background:#f4f4f4;">
                    <th style="padding:8px 12px;text-align:left;font-weight:600;">#</th>
                    <th style="padding:8px 12px;text-align:left;font-weight:600;">Server</th>
                    <th style="padding:8px 12px;text-align:left;font-weight:600;">Location</th>
                    <th style="padding:8px 12px;text-align:left;font-weight:600;">Sales</th>
                    <th style="padding:8px 12px;text-align:left;font-weight:600;">Avg Check</th>
                </tr>
                {server_rows}
            </table>
        </div>

        <!-- Footer -->
        <div style="padding:16px 20px;background:#f8f8f8;border-top:1px solid #eee;
                     border-radius:0 0 8px 8px;font-size:12px;color:#888;">
            Full report attached as Excel file. View your live dashboard at
            <a href="http://localhost:8080" style="color:#C43B3B;">localhost:8080</a>
            <br>Generated automatically by Red Nun Analytics
        </div>

    </body>
    </html>
    """
    return html


def send_weekly_report():
    """Generate and email the weekly report."""
    gmail_user = os.getenv("GMAIL_ADDRESS")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    recipients = os.getenv("REPORT_RECIPIENTS", gmail_user)

    if not gmail_user or not gmail_password:
        logger.error("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        return False

    recipient_list = [r.strip() for r in recipients.split(",")]

    # Date range: last Monday through Sunday
    start_date, end_date = _get_last_week_range()

    start_fmt = datetime.strptime(start_date, "%Y%m%d").strftime("%b %d")
    end_fmt = datetime.strptime(end_date, "%Y%m%d").strftime("%b %d")

    logger.info(f"Generating weekly report for {start_date} to {end_date}...")

    try:
        # Generate Excel file
        excel_path = generate_weekly_excel(start_date, end_date)

        # Build email
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Red Nun Weekly Report — {start_fmt} to {end_fmt}"
        msg["From"] = gmail_user
        msg["To"] = ", ".join(recipient_list)

        # HTML body with summary
        html_body = _build_summary_html(start_date, end_date)
        msg.attach(MIMEText(html_body, "html"))

        # Attach Excel file
        with open(excel_path, "rb") as f:
            attachment = MIMEBase("application", "octet-stream")
            attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition",
                f"attachment; filename={os.path.basename(excel_path)}",
            )
            msg.attach(attachment)

        # Send via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, recipient_list, msg.as_string())

        logger.info(f"Weekly report sent to {', '.join(recipient_list)}")
        return True

    except Exception as e:
        logger.error(f"Failed to send weekly report: {e}")
        return False


# CLI entry point for testing
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Allow testing with custom date range
    if len(sys.argv) == 3:
        start, end = sys.argv[1], sys.argv[2]
        excel_path = generate_weekly_excel(start, end)
        html = _build_summary_html(start, end)

        # Save preview
        preview_path = os.path.join(os.path.dirname(__file__), "exports", "email_preview.html")
        with open(preview_path, "w") as f:
            f.write(html)
        print(f"Excel: {excel_path}")
        print(f"Email preview: {preview_path}")
    else:
        send_weekly_report()
