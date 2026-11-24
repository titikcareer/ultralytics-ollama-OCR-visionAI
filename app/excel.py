import json
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path

import openpyxl
import xlrd
import xlwt
from xlutils.copy import copy as xl_copy

from app.config import get_settings
from app.schemas import TemplateMapping

settings = get_settings()

EXCEL_EPOCH = date(1899, 12, 30)
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def column_index(letter: str) -> int:
    value = 0
    for char in letter.strip().upper():
        if not char.isalpha():
            raise ValueError(f"Kolom tidak valid: {letter}")
        value = value * 26 + (ord(char) - 64)
    return value - 1


def safe_text(value: str | None) -> str:
    text = "".join(char for char in (value or "") if char.isprintable() or char in "\t\n\r").strip()
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def excel_serial(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return (parsed - EXCEL_EPOCH).days


class BaseExporter(ABC):
    def __init__(self, template_path: Path, mapping: TemplateMapping):
        self.template_path = template_path
        self.mapping = mapping

    @abstractmethod
    def write_rows(self, rows: list[dict]) -> None: ...

    @abstractmethod
    def save(self, destination: Path) -> None: ...

    def opening_balance(self) -> int:
        return int(self.mapping.opening_balance or 0)

    def _cell_position(self, ref: str) -> tuple[int, int]:
        letters = "".join(ch for ch in ref if ch.isalpha())
        digits = "".join(ch for ch in ref if ch.isdigit())
        return int(digits) - 1, column_index(letters)

    def _column_span(self) -> tuple[int, int]:
        indices = [
            column_index(getattr(self.mapping.columns, name))
            for name in ("no", "date", "description", "debit", "credit", "balance")
        ]
        return min(indices), max(indices)


class XlsExporter(BaseExporter):
    suffix = ".xls"

    def __init__(self, template_path: Path, mapping: TemplateMapping):
        super().__init__(template_path, mapping)
        self.source = xlrd.open_workbook(str(template_path), formatting_info=True)
        self.workbook = xl_copy(self.source)
        self.sheet_index = self.source.sheet_names().index(mapping.sheet_name)
        self.worksheet = self.workbook.get_sheet(self.sheet_index)
        self.source_sheet = self.source.sheet_by_name(mapping.sheet_name)

    def opening_balance(self) -> int:
        if self.mapping.opening_balance is not None:
            return int(self.mapping.opening_balance)
        row, col = self._cell_position(self.mapping.opening_balance_cell)
        try:
            return int(self.source_sheet.cell_value(row, col))
        except (IndexError, ValueError):
            return 0

    def _style_from(self, cell_ref: str):
        match = "".join(ch for ch in cell_ref if ch.isalpha())
        row_part = "".join(ch for ch in cell_ref if ch.isdigit())
        if not match or not row_part:
            return None
        try:
            row = int(row_part) - 1
            col = column_index(match)
            xf = self.source.xf_list[self.source_sheet.cell_xf_index(row, col)]
        except (IndexError, ValueError, xlrd.XLRDError):
            return None

        style = xlwt.XFStyle()
        source_font = self.source.font_list[xf.font_index]
        font = xlwt.Font()
        font.name = source_font.name
        font.height = source_font.height
        font.bold = bool(source_font.bold or source_font.weight >= 700)
        font.italic = bool(source_font.italic)
        font.underline = source_font.underlined
        font.colour_index = source_font.colour_index
        style.font = font

        style.num_format_str = self.source.format_map[xf.format_key].format_str

        alignment = xlwt.Alignment()
        alignment.horz = xf.alignment.hor_align
        alignment.vert = xf.alignment.vert_align
        alignment.wrap = xf.alignment.text_wrapped
        style.alignment = alignment

        borders = xlwt.Borders()
        borders.left = xf.border.left_line_style
        borders.right = xf.border.right_line_style
        borders.top = xf.border.top_line_style
        borders.bottom = xf.border.bottom_line_style
        borders.left_colour = xf.border.left_colour_index
        borders.right_colour = xf.border.right_colour_index
        borders.top_colour = xf.border.top_colour_index
        borders.bottom_colour = xf.border.bottom_colour_index
        style.borders = borders

        pattern = xlwt.Pattern()
        pattern.pattern = xf.background.fill_pattern
        pattern.pattern_fore_colour = xf.background.pattern_colour_index
        pattern.pattern_back_colour = xf.background.background_colour_index
        style.pattern = pattern
        return style

    def write_rows(self, rows: list[dict]) -> None:
        cols = self.mapping.columns
        date_style = self._style_from(self.mapping.date_style_cell)
        general_style = self._style_from(f"{cols.description}{self.mapping.first_data_row}")
        opening = self.opening_balance()
        balance = opening
        start = self.mapping.first_data_row - 1
        min_col, max_col = self._column_span()
        last = self.mapping.last_data_row or self.source_sheet.nrows
        for r in range(start, max(start, last)):
            for c in range(min_col, max_col + 1):
                self.worksheet.write(r, c, "", general_style)

        for offset, row in enumerate(rows):
            r = start + offset
            amount = int(row["amount"])
            if self.mapping.transaction_direction == "income":
                debit, credit = amount, None
                balance += amount
            else:
                debit, credit = None, amount
                balance -= amount

            self.worksheet.write(r, column_index(cols.no), int(row["no"]), general_style)
            serial = excel_serial(row.get("date"))
            if serial is None:
                self.worksheet.write(r, column_index(cols.date), "", date_style)
            elif date_style is not None:
                self.worksheet.write(r, column_index(cols.date), serial, date_style)
            else:
                self.worksheet.write(r, column_index(cols.date), serial)
            self.worksheet.write(r, column_index(cols.description), safe_text(row["description"]), general_style)
            self.worksheet.write(
                r,
                column_index(cols.debit),
                debit if debit is not None else "",
                general_style,
            )
            self.worksheet.write(
                r,
                column_index(cols.credit),
                credit if credit is not None else "",
                general_style,
            )
            self.worksheet.write(r, column_index(cols.balance), balance, general_style)

    def save(self, destination: Path) -> None:
        self.workbook.save(str(destination))


class XlsxExporter(BaseExporter):
    suffix = ".xlsx"

    def __init__(self, template_path: Path, mapping: TemplateMapping):
        super().__init__(template_path, mapping)
        self.workbook = openpyxl.load_workbook(template_path)
        self.worksheet = self.workbook[mapping.sheet_name]

    def opening_balance(self) -> int:
        if self.mapping.opening_balance is not None:
            return int(self.mapping.opening_balance)
        try:
            return int(self.worksheet[self.mapping.opening_balance_cell].value or 0)
        except (TypeError, ValueError):
            return 0

    def write_rows(self, rows: list[dict]) -> None:
        cols = self.mapping.columns
        balance = self.opening_balance()
        start = self.mapping.first_data_row
        min_col, max_col = self._column_span()
        last = self.mapping.last_data_row or self.worksheet.max_row
        for r in range(start + 1, last + 1):
            for c in range(min_col + 1, max_col + 2):
                self.worksheet.cell(row=r, column=c).value = None

        for offset, row in enumerate(rows):
            r = start + offset
            amount = int(row["amount"])
            if self.mapping.transaction_direction == "income":
                debit, credit = amount, None
                balance += amount
            else:
                debit, credit = None, amount
                balance -= amount

            serial = excel_serial(row.get("date"))
            self.worksheet[f"{cols.no}{r}"] = int(row["no"])
            self.worksheet[f"{cols.date}{r}"] = datetime.strptime(row["date"], "%Y-%m-%d").date() if serial else None
            self.worksheet[f"{cols.description}{r}"] = safe_text(row["description"])
            self.worksheet[f"{cols.debit}{r}"] = debit
            self.worksheet[f"{cols.credit}{r}"] = credit
            self.worksheet[f"{cols.balance}{r}"] = balance

    def save(self, destination: Path) -> None:
        self.workbook.save(destination)


def load_mapping() -> TemplateMapping:
    path = settings.template_mapping_file
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    mapping = TemplateMapping.model_validate(data)
    template = settings.base_dir / mapping.template_path
    if not template.is_file():
        raise FileNotFoundError(f"Template tidak ditemukan: {template}")
    return mapping


def build_rows(scans: list, mapping: TemplateMapping) -> list[dict]:
    rows = []
    for index, scan in enumerate(scans, start=1):
        merchant = (scan.merchant or "").strip()
        description = (scan.description or "").strip()
        text = mapping.description_format.format(merchant=merchant, description=description)
        text = " - ".join(part for part in (merchant, description) if part) if text.strip() == " - " else text
        rows.append(
            {
                "no": index,
                "date": scan.transaction_date,
                "description": text.strip(" -")[:500],
                "direction": mapping.transaction_direction,
                "amount": int(scan.total),
            }
        )
    return rows


def export_scans(scans: list, filename: str) -> tuple[Path, str]:
    mapping = load_mapping()
    template = settings.base_dir / mapping.template_path
    rows = build_rows(scans, mapping)

    if template.suffix.lower() == ".xls":
        exporter: BaseExporter = XlsExporter(template, mapping)
        media_type = "application/vnd.ms-excel"
    elif template.suffix.lower() == ".xlsx":
        exporter = XlsxExporter(template, mapping)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise ValueError("Format template tidak didukung")

    exporter.write_rows(rows)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.export_dir / filename
    exporter.save(destination)
    return destination, media_type
