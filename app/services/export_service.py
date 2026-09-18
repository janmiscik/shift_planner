"""Export harmonogramu smien do Excelu (.xlsx) a do PDF."""

import io
import os

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from app.services.shift_service import shift_duration_hours

HEADER_FILL = PatternFill(
    start_color="1F2937",
    end_color="1F2937",
    fill_type="solid",
)
HEADER_FONT = Font(color="FFFFFF", bold=True)

COLUMNS = [
    "ID",
    "Zamestnanec",
    "Dátum",
    "Začiatok",
    "Koniec",
    "Hodiny",
    "Typ smeny",
    "Oddelenie",
]


def build_shifts_workbook(shifts, title="Smeny"):
    """Vytvorí XLSX zošit zo zoznamu smien a vráti ho ako bytes."""

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title[:31] or "Smeny"

    for col_index, header in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=col_index, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL

    row_index = 2

    for shift in shifts:
        (
            shift_id,
            first_name,
            last_name,
            shift_date,
            start_time,
            end_time,
            shift_type,
            employee_id,
            department_id,
            department_name,
        ) = shift

        sheet.cell(row=row_index, column=1, value=shift_id)
        sheet.cell(
            row=row_index,
            column=2,
            value=f"{first_name} {last_name}",
        )
        sheet.cell(row=row_index, column=3, value=shift_date)
        sheet.cell(row=row_index, column=4, value=start_time)
        sheet.cell(row=row_index, column=5, value=end_time)
        sheet.cell(
            row=row_index,
            column=6,
            value=round(shift_duration_hours(start_time, end_time), 2),
        )
        sheet.cell(row=row_index, column=7, value=shift_type)
        sheet.cell(row=row_index, column=8, value=department_name or "")

        row_index += 1

    for col_index, header in enumerate(COLUMNS, start=1):
        width = max(len(header) + 2, 14)
        sheet.column_dimensions[get_column_letter(col_index)].width = width

    sheet.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    return buffer


# ---------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "fonts")
_FONTS_REGISTERED = False


def _register_fonts():
    """Zaregistruje DejaVu Sans (podpora slovenskej diakritiky).

    Base14 fonty v reportlabe (Helvetica) nevedia zobraziť znaky ako
    č/š/ž/ľ/ď/ť/ň, preto je do projektu pribalený TTF font, ktorý ich
    podporuje. Registrácia sa vykoná len raz za beh aplikácie.
    """

    global _FONTS_REGISTERED

    if _FONTS_REGISTERED:
        return

    pdfmetrics.registerFont(
        TTFont("DejaVuSans", os.path.join(_FONTS_DIR, "DejaVuSans.ttf"))
    )
    pdfmetrics.registerFont(
        TTFont(
            "DejaVuSans-Bold",
            os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf"),
        )
    )

    _FONTS_REGISTERED = True


def build_shifts_pdf(shifts, title="Harmonogram smien"):
    """Vytvorí PDF s harmonogramom smien a vráti ho ako bytes."""

    _register_fonts()

    buffer = io.BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        title=title,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleSk",
        parent=styles["Title"],
        fontName="DejaVuSans-Bold",
        fontSize=16,
    )

    elements = [
        Paragraph(title, title_style),
        Spacer(1, 8),
    ]

    table_data = [COLUMNS]

    for shift in shifts:
        (
            shift_id,
            first_name,
            last_name,
            shift_date,
            start_time,
            end_time,
            shift_type,
            employee_id,
            department_id,
            department_name,
        ) = shift

        table_data.append(
            [
                str(shift_id),
                f"{first_name} {last_name}",
                shift_date,
                start_time,
                end_time,
                f"{shift_duration_hours(start_time, end_time):.1f}",
                shift_type,
                department_name or "-",
            ]
        )

    if len(table_data) == 1:
        table_data.append(["-", "Žiadne smeny", "", "", "", "", "", ""])

    table = Table(
        table_data,
        colWidths=[
            14 * mm,
            42 * mm,
            26 * mm,
            22 * mm,
            22 * mm,
            18 * mm,
            30 * mm,
            None,
        ],
        repeatRows=1,
    )

    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "DejaVuSans-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "DejaVuSans"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (5, 0), (5, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F3F4F6")],
                ),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    elements.append(table)
    document.build(elements)

    buffer.seek(0)

    return buffer
