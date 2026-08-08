"""
services/pdf_report_service.py
==============================
Generates print-ready PDF reports for the AthGad AI Admin Workspace
using ReportLab. All data is fetched live from the database / analytics
engine at generation time, so every download is a fresh realtime snapshot
(no caching, no static content) — a user who subscribes is included in the
very next download.

Supported reports:
  - predicted_calamities   : Predicted calamity + mitigation actions per county + date/time
  - disease_outbreaks      : Predicted disease outbreaks per county + preventive measures
  - subscribed_members     : Subscribed member names + email + subscription date & time
  - unsubscribed_members   : Unsubscribed members + reasons + subscription period
"""

import io
from datetime import datetime
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Circle, Path, Rect, String

from core.admin_reports import (
    get_predicted_calamities,
    get_disease_outbreaks,
    get_subscribed_members,
    get_unsubscribed_members,
    get_report_snapshot,
    get_alert_dispatch_logs,
)


def _eat_now():
    """Current time in East Africa Time (UTC+3) for report timestamps."""
    try:
        return datetime.now(tz=ZoneInfo("Africa/Nairobi"))
    except Exception:
        return datetime.now()


def _fmt_period(date_from, date_to):
    """Human-readable label for the requested report time span."""
    if date_from and date_to:
        return f"{date_from.isoformat()} to {date_to.isoformat()}"
    if date_from:
        return f"from {date_from.isoformat()} onwards"
    if date_to:
        return f"up to {date_to.isoformat()}"
    return "Full history (no date filter)"


# ---- Colour palette (matches the app's dark emerald theme) ----
PRIMARY = colors.HexColor("#0f172a")
ACCENT = colors.HexColor("#10b981")
ACCENT_DARK = colors.HexColor("#059669")
HEADER_BG = colors.HexColor("#0f172a")
ROW_ALT = colors.HexColor("#f0f5fb")
BORDER = colors.HexColor("#cbd5e1")
TEXT_DARK = colors.HexColor("#0f172a")
TEXT_MUTED = colors.HexColor("#64748b")


def _styles():
    s = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "BrandX", parent=s["Normal"], textColor=PRIMARY,
            fontSize=22, fontName="Helvetica-Bold",
            alignment=TA_CENTER, spaceAfter=2, leading=24,
        ),
        "slogan": ParagraphStyle(
            "SloganX", parent=s["Normal"], textColor=ACCENT_DARK,
            fontSize=9.5, fontName="Helvetica",
            alignment=TA_CENTER, spaceAfter=4,
        ),
        "title_pill": ParagraphStyle(
            "TitlePillX", parent=s["Normal"], textColor=colors.white,
            fontSize=11, fontName="Helvetica-Bold",
            alignment=TA_CENTER, backColor=ACCENT, borderPadding=5,
            leading=14, spaceBefore=4, spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "MetaX", parent=s["Normal"], textColor=TEXT_MUTED,
            fontSize=8, alignment=TA_CENTER, spaceAfter=2,
        ),
        "snapshot_head": ParagraphStyle(
            "SnapHeadX", parent=s["Normal"], textColor=ACCENT_DARK,
            fontSize=11, fontName="Helvetica-Bold",
            spaceBefore=8, spaceAfter=5, leading=13,
        ),
        "h2": ParagraphStyle(
            "H2X", parent=s["Heading2"], textColor=PRIMARY,
            fontSize=12.5, fontName="Helvetica-Bold",
            spaceBefore=12, spaceAfter=4, leading=15,
        ),
        "h3": ParagraphStyle(
            "H3X", parent=s["Heading3"], textColor=ACCENT_DARK,
            fontSize=10, fontName="Helvetica-Bold",
            spaceBefore=8, spaceAfter=3, leading=12,
        ),
        "body": ParagraphStyle(
            "BodyX", parent=s["Normal"], fontSize=8.5,
            leading=11.5, textColor=TEXT_DARK,
        ),
        "bullet": ParagraphStyle(
            "BulletX", parent=s["Normal"], fontSize=8.2,
            leading=10.5, leftIndent=12, bulletIndent=2,
            textColor=TEXT_DARK,
        ),
    }


def _page_decorator(canvas, doc, report_title, period=None):
    """Draws a consistent branded header/footer on every PDF page."""
    canvas.saveState()
    page_w = doc.width + doc.leftMargin + doc.rightMargin
    band_bottom = doc.height + 32 * mm

    # Header band
    canvas.setFillColor(HEADER_BG)
    canvas.rect(0, band_bottom, page_w, 8 * mm, fill=1, stroke=0)
    # Emerald accent stripe under the band
    canvas.setFillColor(ACCENT)
    canvas.rect(0, band_bottom - 1.2 * mm, page_w, 1.2 * mm, fill=1, stroke=0)

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(doc.leftMargin, band_bottom + 4.6 * mm, "AthGad AI")
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    canvas.drawString(
        doc.leftMargin, band_bottom + 1.4 * mm,
        "AI-Powered Early Warning System · Eastern Kenya",
    )
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(ACCENT)
    canvas.drawRightString(page_w - doc.rightMargin, band_bottom + 4.6 * mm, report_title)

    # Footer
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, 12 * mm, page_w - doc.rightMargin, 12 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(TEXT_MUTED)
    canvas.drawString(
        doc.leftMargin, 8.5 * mm,
        f"Generated live by AthGad AI on {_eat_now().strftime('%Y-%m-%d %H:%M EAT')}",
    )
    canvas.drawString(
        doc.leftMargin, 5.5 * mm,
        f"Report period: {period or 'Full history'}",
    )
    canvas.drawRightString(page_w - doc.rightMargin, 8.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _table(headers, rows, col_widths=None, header_bg=ACCENT_DARK):
    """Builds a styled Platypus table with an emerald header band."""
    data = [headers] + [list(r) for r in rows]
    tbl = Table(data, colWidths=col_widths, repeatRows=1, hAlign="CENTER")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), TEXT_DARK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("ALIGN", (0, 1), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _snapshot_table(snapshot, period=None):
    """Live system snapshot rendered as a compact metric table."""
    rows = [
        ["Total Users", str(snapshot["total_users"])],
        ["Subscribed Members", str(snapshot["subscribed_count"])],
        ["Unsubscribed Members", str(snapshot["unsubscribed_count"])],
        ["Counties with Live Predictions", f"{snapshot['prediction_counties']} of 8"],
        ["High-Risk Counties", str(snapshot["high_risk_counties"])],
        ["Alert Dispatches Sent", str(snapshot.get("dispatch_count", 0))],
        ["Alert Dispatches Failed", str(snapshot.get("dispatch_failed_count", 0))],
        ["Report Period", period or "Full history"],
        ["Snapshot Taken At", snapshot["generated_at"]],
    ]
    return _table(
        ["Metric", "Live Value"],
        rows,
        col_widths=[100 * mm, 78 * mm],
    )


def _mitigation_actions(actions):
    """Returns bulleted paragraphs for a list of mitigation/preventive actions."""
    fragments = []
    for a in actions:
        fragments.append(Paragraph(f"\u2022 {a}", _styles()["bullet"]))
    if not fragments:
        fragments.append(Paragraph("No actions recorded.", _styles()["body"]))
    return fragments


def _athgad_logo(width=18 * mm, height=18 * mm):
    """
    Builds a ReportLab vector drawing of the AthGad AI "sentinel shield" logo.
    Drawn programmatically so no external image asset is required.
    """
    cx, cy = width / 2.0, height / 2.0
    r_outer = min(width, height) * 0.46
    r_inner = r_outer * 0.70
    d = Drawing(width, height)

    # Outer shield path
    shield = Path()
    shield.moveTo(cx, cy + r_outer)
    shield.curveTo(cx + r_outer * 1.15, cy + r_outer * 0.40, cx + r_outer * 0.80, cy - r_outer * 0.9, cx, cy - r_outer)
    shield.curveTo(cx - r_outer * 0.80, cy - r_outer * 0.9, cx - r_outer * 1.15, cy + r_outer * 0.40, cx, cy + r_outer)
    shield.closePath()
    shield.fillColor = ACCENT
    shield.fillMode = 1
    shield.strokeColor = ACCENT
    shield.strokeWidth = 0
    d.add(shield)

    # Inner telemetry ring
    d.add(Circle(cx, cy, r_inner, fillColor=None, strokeColor=colors.HexColor("#0f172a"), strokeWidth=1.2))

    # Central core
    d.add(Circle(cx, cy, r_inner * 0.55, fillColor=colors.HexColor("#38BDF8"), strokeColor=None))

    # Central dot
    d.add(Circle(cx, cy, r_inner * 0.22, fillColor=colors.white, strokeColor=None))

    # Orbital dotted ring
    orb = Circle(cx, cy, r_outer * 0.98, fillColor=None, strokeColor=colors.HexColor("#2563EB"), strokeWidth=0.9)
    orb.strokeDashArray = (1.2, 2.4)
    d.add(orb)

    return d


def _predicted_calamities_pdf(doc, styles, engine, date_from=None, date_to=None):
    period = _fmt_period(date_from, date_to)
    items = get_predicted_calamities(engine, date_from=date_from, date_to=date_to)
    doc.append(Paragraph("Predicted Calamities & Mitigation Actions", styles["h2"]))
    doc.append(Paragraph(
        "System-predicted calamities for covered counties, the mitigation "
        "actions recommended for each, and the date/time each prediction was produced.",
        styles["body"],
    ))
    doc.append(Paragraph(f"<b>Report period:</b> {period}", styles["body"]))
    doc.append(Spacer(1, 6))

    if not items:
        doc.append(Paragraph(
            "No processed risk data is available for the selected period. Once the "
            "system computes composite risk scores for the covered counties, they "
            "will appear here.",
            styles["body"],
        ))
        doc.append(Spacer(1, 6))
        return

    for item in items:
        block = [
            Paragraph(f"{item['county']} County", styles["h3"]),
            Paragraph(
                f"<b>Predicted Calamity:</b> {item['calamity_type']} &nbsp;|&nbsp; "
                f"<b>Risk Level:</b> {item['risk_level']} "
                f"&nbsp;|&nbsp; <b>Score:</b> {item['score_pct']}%",
                styles["body"],
            ),
            Paragraph(f"<b>Predicted At:</b> {item['predicted_at']}", styles["body"]),
            Paragraph("<b>Mitigation Actions:</b>", styles["bullet"]),
        ]
        for act in _mitigation_actions(item["mitigation_actions"]):
            block.append(act)
        block.append(Spacer(1, 6))
        doc.append(KeepTogether(block))


def _disease_outbreaks_pdf(doc, styles, engine, date_from=None, date_to=None):
    period = _fmt_period(date_from, date_to)
    items = get_disease_outbreaks(engine, date_from=date_from, date_to=date_to)
    doc.append(Paragraph("Predicted Disease Outbreaks & Preventive Measures", styles["h2"]))
    doc.append(Paragraph(
        "Predicted disease outbreaks for covered counties together with the "
        "recommended preventive (mitigation) measures.",
        styles["body"],
    ))
    doc.append(Paragraph(f"<b>Report period:</b> {period}", styles["body"]))
    doc.append(Spacer(1, 6))

    rows = []
    for item in items:
        diseases = item["diseases"]
        if not diseases:
            rows.append([
                item["county"],
                Paragraph("No reported cases", styles["body"]),
                "—", "—", "—",
            ])
            continue
        for d in diseases:
            cases = d.get("reported_cases")
            cases_text = f"{cases:,.0f}" if cases is not None else "—"
            rate = d.get("reporting_rate")
            rate_text = f"{rate:.1f}%" if rate is not None else "—"
            rows.append([
                item["county"],
                Paragraph(d["disease_type"], styles["body"]),
                cases_text,
                rate_text,
                d["timestamp"],
            ])

    doc.append(_table(
        [
            "County",
            "Predicted Disease Outbreak",
            "Reported Cases",
            "Reporting Rate",
            "Last Reported",
        ],
        rows,
        col_widths=[28 * mm, 56 * mm, 28 * mm, 26 * mm, 40 * mm],
    ))
    doc.append(Spacer(1, 8))

    for item in items:
        block = [
            Paragraph(f"{item['county']} County — Preventive Measures", styles["h3"]),
        ]
        for act in _mitigation_actions(item["preventive_measures"]):
            block.append(act)
        block.append(Spacer(1, 6))
        doc.append(KeepTogether(block))


def _subscribed_members_pdf(doc, styles, engine, date_from=None, date_to=None):
    period = _fmt_period(date_from, date_to)
    members = get_subscribed_members(engine, date_from=date_from, date_to=date_to)
    doc.append(Paragraph("Subscribed Members", styles["h2"]))
    doc.append(Paragraph(
        "Currently subscribed members with their full names, email addresses, "
        "and subscription date/time. The list is pulled live on every download, "
        "so anyone who subscribes is automatically included.",
        styles["body"],
    ))
    doc.append(Paragraph(f"<b>Report period:</b> {period}", styles["body"]))
    doc.append(Spacer(1, 6))

    rows = [
        [
            i + 1,
            Paragraph(m["full_name"], styles["body"]),
            Paragraph(m["email"], styles["body"]),
            m["subscribed_at"],
        ]
        for i, m in enumerate(members)
    ]
    if not rows:
        rows = [[Paragraph("1", styles["body"]),
                 Paragraph("No subscribed members", styles["body"]),
                 Paragraph("—", styles["body"]), Paragraph("—", styles["body"])]]

    doc.append(_table(
        ["No.", "Full Name", "Email", "Subscribed At"],
        rows,
        col_widths=[12 * mm, 43 * mm, 60 * mm, 63 * mm],
    ))
    doc.append(Spacer(1, 6))
    doc.append(Paragraph(
        f"<b>Total Subscribed Members:</b> {len(members)} — updated automatically "
        f"on every download.",
        styles["body"],
    ))


def _unsubscribed_members_pdf(doc, styles, engine, date_from=None, date_to=None):
    period = _fmt_period(date_from, date_to)
    members = get_unsubscribed_members(engine, date_from=date_from, date_to=date_to)
    doc.append(Paragraph("Unsubscribed Members & Unsubscription Reasons", styles["h2"]))
    doc.append(Paragraph(
        "Members who unsubscribed with their reasons, unsubscription date/time, and "
        "the period they were subscribed (in days, weeks, months, or years).",
        styles["body"],
    ))
    doc.append(Paragraph(f"<b>Report period:</b> {period}", styles["body"]))
    doc.append(Spacer(1, 6))

    rows = [
        [
            i + 1,
            Paragraph(m["full_name"], styles["body"]),
            m["subscription_period"],
            m["unsubscribed_at"],
            m["channel"],
            # Paragraph (not a plain string) so long reasons wrap onto new lines
            # inside the column instead of spilling outside the table.
            Paragraph(m["reason"], styles["body"]),
        ]
        for i, m in enumerate(members)
    ]
    if not rows:
        rows = [[Paragraph("1", styles["body"]),
                 Paragraph("No unsubscribed members", styles["body"]),
                 Paragraph("—", styles["body"]), Paragraph("—", styles["body"]),
                 Paragraph("—", styles["body"]), Paragraph("—", styles["body"])]]

    doc.append(_table(
        ["No.", "Full Name", "Period", "Unsubscribed At", "Channel", "Reason"],
        rows,
        col_widths=[10 * mm, 36 * mm, 26 * mm, 32 * mm, 16 * mm, 58 * mm],
    ))
    doc.append(Spacer(1, 6))
    doc.append(Paragraph(
        f"<b>Total Unsubscribed Members:</b> {len(members)} — updated automatically "
        f"on every download.",
        styles["body"],
    ))


def _alert_dispatch_logs_pdf(doc, styles, engine, date_from=None, date_to=None):
    period = _fmt_period(date_from, date_to)
    logs = get_alert_dispatch_logs(engine, limit=1000,
                                   date_from=date_from, date_to=date_to)
    doc.append(Paragraph("Alert Dispatch Tracking Logs", styles["h2"]))
    doc.append(Paragraph(
        "Every tracked SMS/email dispatch with the recipient identifier (phone "
        "number for SMS, email address for email), the exact message that was "
        "sent, and the recipient's subscription status at dispatch time.",
        styles["body"],
    ))
    doc.append(Paragraph(f"<b>Report period:</b> {period}", styles["body"]))
    doc.append(Spacer(1, 6))

    rows = [
        [
            i + 1,
            m["dispatched_at"],
            m["channel"].upper(),
            Paragraph(m["recipient"], styles["body"]),
            m["subscription_status"],
            m["status"],
            # Paragraph (not a plain string) so long messages wrap inside the
            # column instead of spilling outside the table.
            Paragraph(m["message"], styles["body"]),
        ]
        for i, m in enumerate(logs)
    ]
    if not rows:
        rows = [[Paragraph("1", styles["body"]),
                 Paragraph("—", styles["body"]), Paragraph("—", styles["body"]),
                 Paragraph("No dispatches tracked yet", styles["body"]),
                 Paragraph("—", styles["body"]), Paragraph("—", styles["body"]),
                 Paragraph("—", styles["body"])]]

    doc.append(_table(
        ["No.", "Dispatched At", "Channel", "Recipient", "Subscription",
         "Status", "Message"],
        rows,
        col_widths=[10 * mm, 30 * mm, 14 * mm, 40 * mm, 22 * mm, 18 * mm, 44 * mm],
    ))
    doc.append(Spacer(1, 6))

    success = sum(1 for m in logs if m["status"] == "SUCCESS")
    failed = sum(1 for m in logs if m["status"] == "FAILED")
    simulated = sum(1 for m in logs if m["status"] == "SIMULATED")
    doc.append(Paragraph(
        f"<b>Total Tracked Dispatches:</b> {len(logs)} &nbsp;|&nbsp; "
        f"<b>Delivered:</b> {success} &nbsp;|&nbsp; "
        f"<b>Failed:</b> {failed} &nbsp;|&nbsp; "
        f"<b>Simulated:</b> {simulated} — pulled live on every download.",
        styles["body"],
    ))


_BUILDERS = {
    "predicted_calamities": _predicted_calamities_pdf,
    "disease_outbreaks": _disease_outbreaks_pdf,
    "subscribed_members": _subscribed_members_pdf,
    "unsubscribed_members": _unsubscribed_members_pdf,
    "alert_dispatch_logs": _alert_dispatch_logs_pdf,
}

_TITLES = {
    "predicted_calamities": "Predicted Calamities Report",
    "disease_outbreaks": "Disease Outbreaks Report",
    "subscribed_members": "Subscribed Members Report",
    "unsubscribed_members": "Unsubscribed Members Report",
    "alert_dispatch_logs": "Alert Dispatch Logs Report",
}


def build_admin_report(engine, report_type: str, date_from=None, date_to=None) -> bytes:
    """
    Builds the requested admin PDF report and returns it as bytes.

    Every report embeds a LIVE system snapshot and pulls its section data
    straight from the database at build time, so it is never static.
    `date_from`/`date_to` (datetime.date objects) restrict every report section
    to records inside that inclusive time span.

    Args:
        engine: SQLAlchemy engine (passed through to the data-access layer).
        report_type: one of the keys in _BUILDERS.
        date_from: optional start date (inclusive).
        date_to: optional end date (inclusive).

    Returns:
        PDF file contents as bytes.
    """
    if report_type not in _BUILDERS:
        raise ValueError(f"Unknown report type: {report_type}")

    title = _TITLES[report_type]
    period = _fmt_period(date_from, date_to)
    snapshot = get_report_snapshot(engine)
    now = _eat_now()
    buf = io.BytesIO()

    # A4 with consistent margins; generous top margin for the branded band.
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=44 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="AthGad AI",
    )

    styles = _styles()
    story = []

    # ── Branded cover header: logo + name + slogan + title pill ──
    logo = _athgad_logo(24 * mm, 24 * mm)
    logo.hAlign = "CENTER"
    story.append(logo)
    story.append(Spacer(1, 4))
    story.append(Paragraph("AthGad AI", styles["brand"]))
    story.append(Paragraph(
        "AI-Powered Early Warning System for Eastern Kenya",
        styles["slogan"],
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"ADMIN REPORT — {title.upper()}", styles["title_pill"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Eastern Kenya Early Warning System | Generated live: "
        f"{now.strftime('%Y-%m-%d %H:%M EAT')}",
        styles["meta"],
    ))
    story.append(Paragraph(
        f"Report period: {period} | Data is pulled at download time",
        styles["meta"],
    ))
    story.append(Spacer(1, 8))

    # ── Live system snapshot ──
    story.append(Paragraph("Live System Snapshot", styles["snapshot_head"]))
    story.append(_snapshot_table(snapshot, period))
    story.append(Spacer(1, 10))

    # ── Dispatch to the correct report builder ──
    _BUILDERS[report_type](story, styles, engine, date_from, date_to)

    doc.build(
        story,
        onFirstPage=lambda c, d: _page_decorator(c, d, title, period),
        onLaterPages=lambda c, d: _page_decorator(c, d, title, period),
    )

    return buf.getvalue()
