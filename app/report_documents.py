"""One presentation model for HTML, CSV and Excel activity reports."""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime

from app.business_time import BUSINESS_ZONE

SUMMARY_LABELS = {
    "gross_sales": "Original fulfilled sales", "sales_refunds": "Completed refunds against reported sales",
    "retained_sales": "Retained sales", "orders_handled": "Orders personally handled",
    "fulfilled_orders": "Fulfilled orders", "retained_orders": "Orders with retained sales",
    "returns_handled": "Return cases personally handled", "return_credit": "Approved return credit",
    "refunds_handled": "Related refund records", "completed_refunds": "Completed related refunds",
    "pending_refunds": "Unsettled related refunds", "cancellation_refunds": "Completed cancellation refunds",
}
MONEY_KEYS = {"gross_sales", "sales_refunds", "retained_sales", "return_credit", "completed_refunds", "pending_refunds", "cancellation_refunds"}


def local_time(value) -> str:
    return datetime.fromisoformat(str(value)).astimezone(BUSINESS_ZONE).strftime("%Y-%m-%d %H:%M") if value else "—"


def safe_text(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def section_table(payload: dict, section: str) -> dict:
    """Columns are (key, label, type); rows contain scalar, exportable values."""
    if section == "summary":
        columns = [("measure", "Measure", "text"), ("value", "Value", "number"), ("unit", "Unit", "text")]
        rows = [{"measure": label, "value": payload["summary"][key], "unit": "PHP" if key in MONEY_KEYS else "count"}
                for key, label in SUMMARY_LABELS.items()]
    else:
        columns = [("order_id", "Order #", "number"), ("customer", "Customer", "text")]
        if section in {"sales", "orders"}:
            columns += [("order_date", "Order placed (PHT)", "text"), ("status", "Status", "text"), ("products", "Products", "text")]
            columns += ([("gross_sales", "Original sales (PHP)", "money"), ("completed_refunds", "Completed refunds (PHP)", "money"),
                         ("retained_sales", "Retained sales (PHP)", "money")] if section == "sales" else [("total_amount", "Order value (PHP)", "money")])
        elif section == "returns":
            columns = [("return_id", "Return #", "number")] + columns
            columns += [("status", "Status", "text"), ("resolution", "Resolution", "text"), ("products", "Returned products", "text"),
                        ("credit", "Approved credit (PHP)", "money")]
        elif section == "refunds":
            columns = [("order_refund_id", "Refund #", "number")] + columns
            columns += [("return_id", "Return #", "number"), ("refund_type", "Type", "text"), ("status", "Status", "text"),
                        ("amount", "Amount (PHP)", "money"), ("requested_by", "Requested by", "text"),
                        ("processor", "Transfer processor", "text"), ("completed_at", "Completed (PHT)", "text")]
        else:
            raise ValueError("Unknown report section.")
        columns += [("actions", "Your recorded actions (PHT)", "text")]
        rows = []
        for record in payload[section]:
            row = dict(record)
            for key in ("order_date", "completed_at"):
                if key in row:
                    row[key] = local_time(row[key])
            row["status"] = row["status"].replace("_", " ").capitalize()
            row["products"] = "; ".join(f"{p['product']} × {p['quantity']:g} {p.get('unit', 'pack')}"
                + (f" (received {p['received_quantity']:g})" if "received_quantity" in p else "") for p in record.get("products", []))
            row["actions"] = "; ".join(f"{a['action'].replace('_', ' ').capitalize()} · {local_time(a['at'])}"
                + (f" · {a['remarks']}" if a.get("remarks") else "") for a in record.get("actions", []))
            if section == "refunds" and not row["actions"]:
                row["actions"] = "Linked to your return case" if record.get("return_id") else "Personally requested"
            rows.append(row)
    return {"key": section, "title": section.title(), "columns": columns, "rows": rows}


def document_context(payload: dict) -> dict:
    return {"document": payload, "tables": [section_table(payload, s) for s in ("summary", "sales", "orders", "returns", "refunds")],
            "captured_label": local_time(payload["captured_at"]), "submitted_label": local_time(payload.get("submitted_at"))}


def csv_document(payload: dict, section: str) -> str:
    table = section_table(payload, section)
    # Identity columns travel with every row, even when the file is opened independently.
    metadata = {"report": payload.get("report_id", "Preview"), "author": payload["author"]["name"],
                "period_start": payload["period_start"], "period_end": payload["period_end"],
                "captured_at_pht": local_time(payload["captured_at"]), "report_notes": payload.get("notes", ""),
                "section_notes": payload.get("section_notes", {}).get(section, "")}
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([*metadata, *(col[1] for col in table["columns"])])
    for row in table["rows"] or [{}]:
        values = [*metadata.values(), *(row.get(col[0], "") for col in table["columns"])]
        writer.writerow([v if isinstance(v, (int, float)) else safe_text(v) for v in values])
    return "\ufeff" + output.getvalue()


def excel_document(payload: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    workbook.remove(workbook.active)
    for section in ("summary", "sales", "orders", "returns", "refunds"):
        table = section_table(payload, section)
        sheet = workbook.create_sheet(table["title"])
        metadata = [f"MEATTRACK · Sales Activity Report · {payload.get('report_id', 'PREVIEW')}",
                    f"Prepared by {payload['author']['name']}", f"Original order dates: {payload['period_start']} to {payload['period_end']}",
                    f"Captured {local_time(payload['captured_at'])} PHT · Submitted {local_time(payload.get('submitted_at'))}",
                    payload.get("notes", ""), payload.get("section_notes", {}).get(section, "")]
        for line in metadata:
            sheet.append([safe_text(line)])
        for index, line in enumerate(metadata, 1):
            sheet.merge_cells(start_row=index, start_column=1, end_row=index, end_column=len(table["columns"]))
            sheet.row_dimensions[index].height = 28 if index == 1 else max(16, 16 * math.ceil(len(line) / 110))
        sheet.append([c[1] for c in table["columns"]])
        for row in table["rows"]:
            sheet.append([row.get(key) if kind in {"number", "money"} else safe_text(row.get(key)) for key, _, kind in table["columns"]])
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if cell.data_type == "s":
                    cell.data_type = "s"  # User-controlled strings must never become Excel formulas.
        sheet["A1"].font = Font(name="Calibri", size=16, bold=True, color="171511")
        for cell in sheet[7]:
            cell.font = Font(name="Calibri", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="171511")
        for index, (key, _, kind) in enumerate(table["columns"], 1):
            sheet.column_dimensions[get_column_letter(index)].width = 44 if key in {"actions", "products", "measure"} else 23
            for row_index in range(8, sheet.max_row + 1):
                cell = sheet.cell(row_index, index)
                if kind == "money" or (section == "summary" and key == "value" and sheet.cell(row_index, 3).value == "PHP"):
                    cell.number_format = '"PHP "#,##0.00'
        for row_index in range(8, sheet.max_row + 1):
            lines = max(math.ceil(len(str(sheet.cell(row_index, i).value or "")) /
                (42 if key in {"actions", "products", "measure"} else 21)) for i, (key, _, _) in enumerate(table["columns"], 1))
            sheet.row_dimensions[row_index].height = min(409, max(30, lines * 16))
        sheet.freeze_panes = "C8"
        sheet.auto_filter.ref = f"A7:{get_column_letter(len(table['columns']))}{max(7, sheet.max_row)}"
        sheet.print_title_rows = "1:7"
        sheet.print_area = f"A1:{get_column_letter(len(table['columns']))}{sheet.max_row}"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.oddFooter.center.text = "Page &P of &N"
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
