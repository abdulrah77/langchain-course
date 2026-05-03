"""
services/invoice_pdf_service.py

Generates a professional PDF invoice/bill using fpdf2.
Returns raw bytes that can be uploaded directly to WhatsApp.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Dict, List, Optional

from fpdf import FPDF, XPos, YPos


class InvoicePDF(FPDF):
    """Custom FPDF subclass for invoice styling."""

    def __init__(self, business_name: str, store_name: str):
        super().__init__()
        self.business_name = business_name
        self.store_name = store_name

    def header(self):
        # Brand block
        self.set_fill_color(30, 30, 30)
        self.rect(0, 0, 210, 28, "F")
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 16)
        self.set_xy(10, 7)
        self.cell(130, 8, self.business_name, new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_font("Helvetica", "", 9)
        self.set_xy(10, 17)
        self.cell(130, 5, self.store_name)
        # INVOICE label
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(255, 255, 255)
        self.set_xy(140, 6)
        self.cell(60, 14, "INVOICE", align="R")
        self.ln(20)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()} | Generated {datetime.now().strftime('%d %b %Y %H:%M')}", align="C")


def _currency(value: float) -> str:
    return f"{value:,.2f}"


def generate_invoice_pdf(
    *,
    invoice_id: str,
    business_name: str,
    store_name: str,
    customer_name: Optional[str],
    items: List[Dict[str, Any]],
    subtotal: float,
    discount: float,
    tax: float,
    total: float,
    paid_amount: float,
    due_amount: float,
    payment_status: str,
    invoice_date: Optional[str] = None,
) -> bytes:
    """
    Build a PDF invoice and return it as raw bytes.

    items: list of dicts with keys: product_name, qty, unit_price, line_total
    """
    pdf = InvoicePDF(business_name=business_name, store_name=store_name)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_text_color(30, 30, 30)

    date_str = invoice_date or datetime.now().strftime("%d %b %Y")

    # ── Invoice meta block ────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(10, 35)
    pdf.set_fill_color(245, 245, 245)
    pdf.rect(10, 34, 190, 22, "F")

    pdf.set_xy(12, 37)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(30, 5, "Invoice No:", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(42, 37)
    pdf.cell(60, 5, str(invoice_id)[:16])

    pdf.set_xy(12, 44)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(30, 5, "Date:", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(42, 44)
    pdf.cell(60, 5, date_str)

    # Customer block (right side)
    pdf.set_xy(120, 37)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(25, 5, "Bill To:")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(145, 37)
    pdf.cell(55, 5, customer_name or "Walk-in Customer")

    pdf.set_xy(120, 44)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(25, 5, "Status:")
    pdf.set_xy(145, 44)
    status_color = (34, 139, 34) if payment_status == "paid" else (220, 120, 0) if payment_status == "partial" else (200, 0, 0)
    pdf.set_text_color(*status_color)
    pdf.cell(55, 5, payment_status.upper())
    pdf.set_text_color(30, 30, 30)

    # ── Items table header ────────────────────────────────────────────────────
    pdf.set_xy(10, 62)
    pdf.set_fill_color(30, 30, 30)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    col_widths = [80, 25, 40, 40]  # Product, Qty, Unit Price, Total
    headers = ["Product", "Qty", "Unit Price", "Total"]
    for header, w in zip(headers, col_widths):
        pdf.cell(w, 8, header, border=0, align="C" if header != "Product" else "L", fill=True)
    pdf.ln()

    # ── Items rows ────────────────────────────────────────────────────────────
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "", 9)
    row_fill = False
    for item in items:
        pdf.set_fill_color(248, 248, 248) if row_fill else pdf.set_fill_color(255, 255, 255)
        name = str(item.get("product_name", "Item"))[:38]
        qty = item.get("qty", 0)
        unit = item.get("unit_price") or 0
        line_total = item.get("line_total") or (qty * unit)
        pdf.cell(col_widths[0], 7, name, border=0, fill=True)
        pdf.cell(col_widths[1], 7, str(qty), border=0, align="C", fill=True)
        pdf.cell(col_widths[2], 7, _currency(unit), border=0, align="R", fill=True)
        pdf.cell(col_widths[3], 7, _currency(line_total), border=0, align="R", fill=True)
        pdf.ln()
        row_fill = not row_fill

    # ── Separator ─────────────────────────────────────────────────────────────
    pdf.set_draw_color(200, 200, 200)
    pdf.set_line_width(0.3)
    y = pdf.get_y() + 2
    pdf.line(10, y, 200, y)
    pdf.ln(4)

    # ── Totals block (right-aligned) ──────────────────────────────────────────
    def _total_row(label: str, value: float, bold: bool = False, color=(30, 30, 30)):
        pdf.set_text_color(*color)
        pdf.set_font("Helvetica", "B" if bold else "", 9)
        pdf.set_x(120)
        pdf.cell(45, 6, label, align="R")
        pdf.cell(35, 6, _currency(value), align="R")
        pdf.ln()
        pdf.set_text_color(30, 30, 30)

    _total_row("Subtotal:", subtotal)
    if discount > 0:
        _total_row("Discount:", -discount, color=(180, 0, 0))
    if tax > 0:
        _total_row("Tax:", tax)

    # Bold total divider
    pdf.set_x(120)
    pdf.set_draw_color(30, 30, 30)
    pdf.set_line_width(0.5)
    yt = pdf.get_y()
    pdf.line(120, yt, 200, yt)
    pdf.ln(2)
    _total_row("TOTAL:", total, bold=True)
    _total_row("Paid:", paid_amount, color=(34, 139, 34))
    if due_amount > 0:
        _total_row("Balance Due:", due_amount, bold=True, color=(200, 0, 0))

    # ── Footer note ───────────────────────────────────────────────────────────
    pdf.ln(8)
    pdf.set_x(10)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 5, "Thank you for your business!", align="C")

    # ── Export to bytes ───────────────────────────────────────────────────────
    buf = io.BytesIO()
    pdf_bytes = pdf.output()
    return bytes(pdf_bytes)
