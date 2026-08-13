"""EOD report card + delivery (email / push notification).

Builds a plain-text daily report (today's picks + live scorecard of every
tracked pick + the honest backtest reminder) and sends it via Gmail SMTP
and/or ntfy.sh push. All credentials live in secrets.yaml, never in code.

secrets.yaml (project root):
    email:
      from: your.gmail@gmail.com
      to: shanu.shah1690@gmail.com
      app_password: "16-char Gmail app password"
    ntfy:
      topic: "nse-scanner-eod"     # your phone subscribes to ntfy.sh/<topic>

Set EMAIL_FROM / EMAIL_TO / EMAIL_APP_PASSWORD env vars as an alternative.
"""

import datetime
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(ROOT, "data", "reports")


def load_secrets():
    """Return {"email": {...}, "ntfy": {...}} from secrets.yaml + env."""
    import yaml
    out = {"email": {}, "ntfy": {}}
    path = os.path.join(ROOT, "secrets.yaml")
    if os.path.exists(path):
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
        out["email"] = cfg.get("email", {}) or {}
        out["ntfy"] = cfg.get("ntfy", {}) or {}
    out["email"].setdefault("from", os.environ.get("EMAIL_FROM", ""))
    out["email"].setdefault("to", os.environ.get("EMAIL_TO", ""))
    out["email"].setdefault("app_password", os.environ.get("EMAIL_APP_PASSWORD", ""))
    out["ntfy"].setdefault("topic", os.environ.get("NTFY_TOPIC", ""))
    return out


def html_table(headers, rows, fmt=None):
    """Build a clean, styled HTML table for email. fmt: list of column types
    ('num' right-align, 'pct' color-coded) aligned to headers."""
    from html import escape

    if not rows:
        return ""
    fmt = fmt or []

    def col_align(j):
        return "right" if j < len(fmt) and fmt[j] == "num" else "left"

    head = []
    for j, h in enumerate(headers):
        head.append(f"<th style='padding:6px 8px;text-align:{col_align(j)}'>"
                    + escape(str(h)) + "</th>")
    body = []
    for i, row in enumerate(rows):
        zebra = "odd" if i % 2 else "even"
        tds = []
        for j, val in enumerate(row):
            style = "text-align:" + col_align(j)
            if j < len(fmt) and fmt[j] == "pct":
                txt = str(val)
                if txt.startswith("+"):
                    style += ";color:#1a7f37;font-weight:bold"
                elif txt.startswith("-"):
                    style += ";color:#c62828;font-weight:bold"
            tds.append(f"<td class='{zebra}' style='{style}'>{escape(str(val))}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (
        "<table style='border-collapse:collapse;font-family:Arial,Helvetica,sans-"
        "serif;font-size:12px;width:100%'>"
        f"<tr style='background:#1a1a2e;color:#fff'>" + "".join(head) + "</tr>"
        + "".join(body)
        + "</table>"
    )


def render_html(subject, delivery, option, scorecard_data, morning=False):
    """Assemble a pretty HTML email body.
    delivery / option: {"headers": [...], "rows": [...], "fmt": [...]}
    scorecard_data: tracker.scorecard_data() dict or None."""
    from html import escape

    head_html = (
        "<style>body{font-family:Arial,Helvetica,sans-serif;color:#222;"
        "font-size:13px;line-height:1.5}th{white-space:nowrap}"
        ".odd{background:#f5f7fa}.even{background:#fff}</style>"
        "<h2 style='color:#1a1a2e'>" + escape(subject) + "</h2>"
    )
    if morning:
        head_html += (
            "<div style='background:#fff8e1;border-left:4px solid #f9a825;"
            "padding:10px 14px;margin:10px 0'>"
            "<b>Buy rules for today:</b><br>"
            "1) Pick only 1-2 names.<br>"
            "2) Place a LIMIT order at or below ENTRY.<br>"
            "3) SKIP any stock that opens more than ~1.5% above ENTRY "
            "(don't chase).<br>"
            "4) Set STOP and TARGETS, then leave it alone.<br>"
            "5) Options = only if you understand premium loss.</div>"
        )

    sections = []
    if delivery and delivery.get("rows"):
        sections.append("<h3 style='color:#1565c0'>Today's Delivery Picks</h3>"
                        + html_table(delivery["headers"], delivery["rows"],
                                     delivery.get("fmt", [])))
    if option and option.get("rows"):
        sections.append("<h3 style='color:#6a1b9a'>Today's Option Picks (CE)</h3>"
                        + html_table(option["headers"], option["rows"],
                                     option.get("fmt", [])))

    if scorecard_data and (scorecard_data["del_rows"] or scorecard_data["opt_rows"]):
        sc = ["<h3 style='color:#00695c'>Live Scorecard (all tracked picks)</h3>"]
        if scorecard_data["del_rows"]:
            sc.append("<h4 style='color:#37474f'>Delivery / Fade (long, 3-5 day)</h4>"
                      + html_table(scorecard_data["del_headers"],
                                   scorecard_data["del_rows"],
                                   ["text"] * 2 + ["num"] * 7))
        if scorecard_data["opt_rows"]:
            sc.append("<h4 style='color:#37474f'>Options (spot vs breakeven)</h4>"
                      + html_table(scorecard_data["opt_headers"],
                                   scorecard_data["opt_rows"],
                                   ["text"] * 2 + ["num"] * 8))
        sc.append(
            "<p style='font-size:11px;color:#555'>STATUS: delivery "
            "<b>T1/T2 HIT</b>=target hit, <b>STOPPED OUT</b>=thesis broke, "
            "<b>OPEN</b>=still running. options <b>PROFIT</b>=spot past breakeven, "
            "<b>LOSS</b>=spot on wrong side, <b>OPEN</b>=undecided. "
            "TO_BE% = how much more the stock must move to reach breakeven.</p>")
        sections.append("".join(sc))

    sections.append(
        "<p style='font-size:11px;color:#888;border-top:1px solid #eee;padding-top:8px'>"
        "Honesty reminder: the 3-month backtest found ~1 in 5 fade picks reaches "
        "+3% in 5 days. Expect misses - the scorecard keeps them honest. "
        "Re-run <code>backtest</code> and <code>factors</code> monthly.</p>")

    return head_html + "".join(sections)


def render(subject, delivery_blocks, option_blocks, scorecard, extra=None):
    """Assemble the plain-text report body."""
    lines = []
    lines.append(subject)
    lines.append("=" * 70)
    lines.append(f"Generated: {datetime.datetime.now().strftime('%d %b %Y %H:%M')}")
    lines.append("")
    if delivery_blocks:
        lines.append("TODAY'S DELIVERY PICKS (entry/stop/targets)")
        lines.append(delivery_blocks)
        lines.append("")
    if option_blocks:
        lines.append("TODAY'S OPTION PICKS (strike / premium / breakeven)")
        lines.append(option_blocks)
        lines.append("")
    lines.append("LIVE SCORECARD (all previously tracked picks)")
    lines.append(scorecard)
    lines.append("")
    lines.append("HONESTY REMINDER: the 3-month backtest said ~1 in 5 fade picks")
    lines.append("reaches +3% in 5 days. Expect misses - the scorecard keeps")
    lines.append("them honest. Re-run `backtest` and `factors` monthly.")
    return "\n".join(lines)


def save_report(body, subject):
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    path = os.path.join(REPORT_DIR, f"report_{stamp}.txt")
    with open(path, "w") as fh:
        fh.write(body)
    return path


def send_email(subject, body, cfg, html_body=None):
    import smtplib
    from email.message import EmailMessage

    from_addr = cfg.get("from", "")
    to_addr = cfg.get("to", "")
    app_password = cfg.get("app_password", "")
    if not (from_addr and to_addr and app_password):
        raise RuntimeError(
            "Email not configured. Create secrets.yaml (see nse/report.py) "
            "with your Gmail + App Password, or set EMAIL_FROM/EMAIL_TO/"
            "EMAIL_APP_PASSWORD.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=40) as s:
        s.login(from_addr, app_password)
        s.send_message(msg)
    return to_addr


def send_ntfy(subject, body, cfg):
    import urllib.request

    topic = cfg.get("topic", "")
    if not topic:
        raise RuntimeError("ntfy topic not configured (secrets.yaml -> ntfy.topic).")
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode(),
        headers={"Title": subject, "Priority": "default"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status
