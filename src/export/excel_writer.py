"""Write staging template.xlsx through an owned Windows Excel COM instance."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
from typing import Any

from src.domain.models import TemplateRow
from src.domain.template_schema import SHEET_LAYOUTS, SHEET_ORDER, SheetLayout, expected_validation_formula
from src.utils.file_utils import split_attachment_names


XL_VALIDATE_LIST = 3
EXCEL_OPERATION_TIMEOUT_SECONDS = 120
EXCEL_BUSY_RETRY_COUNT = 3


class ExcelAutomationUnavailable(RuntimeError):
    """Raised when pywin32 or a local Microsoft Excel automation server is unavailable."""


class TemplateIntegrityError(RuntimeError):
    """Raised when a staging workbook no longer matches the fixed template contract."""


@dataclass(frozen=True)
class WorkbookInspection:
    sheet_names: tuple[str, ...]
    referenced_assets: tuple[str, ...]


@dataclass(frozen=True)
class WorkbookWriteResult:
    workbook_path: Path
    inspection: WorkbookInspection


class ExcelTemplateWriter:
    """Serialize rows into a protected template without rebuilding any workbook structure."""

    def __init__(self, workbook_name: str = "template.xlsx") -> None:
        self._workbook_name = workbook_name

    def write(self, template_dir: Path, rows: Iterable[TemplateRow]) -> WorkbookWriteResult:
        template_dir = Path(template_dir).resolve()
        workbook_path = template_dir / self._workbook_name
        if not workbook_path.is_file():
            raise FileNotFoundError(f"Staging workbook is missing: {workbook_path}")
        rows_by_sheet = self._group_rows(rows)
        self._run_with_excel("write", workbook_path, rows_by_sheet)
        inspection = self.inspect(workbook_path)
        expected_assets = {name for template_row in rows_by_sheet.values() for row in template_row for name in row.all_asset_names()}
        if set(inspection.referenced_assets) != expected_assets:
            raise TemplateIntegrityError("Saved workbook asset references do not match the written template rows.")
        return WorkbookWriteResult(workbook_path, inspection)

    def inspect(self, workbook_path: Path) -> WorkbookInspection:
        workbook_path = Path(workbook_path).resolve()
        return self._run_with_excel("inspect", workbook_path)

    def validate_contract(self, workbook_path: Path) -> tuple[str, ...]:
        """Read and validate a workbook without changing its data or assets."""

        workbook_path = Path(workbook_path).resolve()
        if not workbook_path.is_file():
            raise FileNotFoundError(f"Workbook is missing: {workbook_path}")
        return self._run_with_excel("validate", workbook_path)

    def _write_in_excel(self, app: Any, workbook_path: Path, rows_by_sheet: dict[str, list[TemplateRow]]) -> None:
        workbook = self._open_workbook(app, workbook_path, read_only=False)
        try:
            self._verify_template_contract(workbook)
            for layout in SHEET_LAYOUTS.values():
                sheet = workbook.Worksheets(layout.name)
                sheet_rows = rows_by_sheet.get(layout.name, [])
                clear_last_row = self._ensure_sheet_capacity(
                    sheet,
                    layout,
                    len(sheet_rows),
                )
                self._clear_data_rows(sheet, layout, clear_last_row)
            for sheet_name, rows in rows_by_sheet.items():
                layout = SHEET_LAYOUTS[sheet_name]
                self._write_sheet_rows(workbook.Worksheets(sheet_name), layout, rows)
            workbook.Save()
        finally:
            workbook.Close(False)

    def _inspect_in_excel(self, app: Any, workbook_path: Path) -> WorkbookInspection:
        workbook = self._open_workbook(app, workbook_path, read_only=True)
        try:
            self._verify_template_contract(
                workbook,
                allow_dynamic_unprotected=True,
            )
            asset_names: set[str] = set()
            for layout in SHEET_LAYOUTS.values():
                asset_names.update(self._read_sheet_assets(workbook.Worksheets(layout.name), layout))
            return WorkbookInspection(tuple(sheet.Name for sheet in workbook.Worksheets), tuple(sorted(asset_names)))
        finally:
            workbook.Close(False)

    def _validate_contract_in_excel(self, app: Any, workbook_path: Path) -> tuple[str, ...]:
        workbook = self._open_workbook(app, workbook_path, read_only=True)
        try:
            self._verify_template_contract(workbook)
            return tuple(sheet.Name for sheet in workbook.Worksheets)
        finally:
            workbook.Close(False)

    @staticmethod
    def _open_workbook(app: Any, workbook_path: Path, read_only: bool) -> Any:
        return app.Workbooks.Open(str(workbook_path), 0, read_only)

    @staticmethod
    def _group_rows(rows: Iterable[TemplateRow]) -> dict[str, list[TemplateRow]]:
        grouped: dict[str, list[TemplateRow]] = defaultdict(list)
        for row in rows:
            if row.sheet_name not in SHEET_LAYOUTS:
                raise TemplateIntegrityError(f"Unsupported worksheet: {row.sheet_name}")
            grouped[row.sheet_name].append(row)
        for sheet_name, sheet_rows in grouped.items():
            layout = SHEET_LAYOUTS[sheet_name]
            sheet_rows.sort(key=lambda row: row.evidence_id)
            for row in sheet_rows:
                ExcelTemplateWriter._validate_row(layout, row)
        return grouped

    @staticmethod
    def _verify_template_contract(
        workbook: Any,
        *,
        allow_dynamic_unprotected: bool = False,
    ) -> None:
        actual_order = tuple(sheet.Name for sheet in workbook.Worksheets)
        if actual_order != SHEET_ORDER:
            raise TemplateIntegrityError(f"Worksheet order changed: {actual_order}")
        for layout in SHEET_LAYOUTS.values():
            sheet = workbook.Worksheets(layout.name)
            used_last_row = (
                int(sheet.UsedRange.Row)
                + int(sheet.UsedRange.Rows.Count)
                - 1
            )
            dynamically_extended = used_last_row > layout.formatted_last_row
            if (
                not bool(sheet.ProtectContents)
                and not (
                    allow_dynamic_unprotected
                    and dynamically_extended
                )
            ):
                raise TemplateIntegrityError(f"Worksheet protection is missing: {layout.name}")
            actual_headers = tuple(ExcelTemplateWriter._cell_text(sheet, 1, column) for column in range(1, layout.column_count + 1))
            if actual_headers != layout.headers:
                raise TemplateIntegrityError(f"Header contract changed: {layout.name}")
            for column, allowed_values in layout.validation_values.items():
                cell = sheet.Cells(layout.data_start_row, layout.column_number(column))
                try:
                    validation_type = int(cell.Validation.Type)
                    formula = str(cell.Validation.Formula1).strip().lstrip("=")
                except Exception as error:
                    raise TemplateIntegrityError(f"Data validation is missing: {layout.name}!{column}") from error
                if validation_type != XL_VALIDATE_LIST or formula != expected_validation_formula(allowed_values):
                    raise TemplateIntegrityError(f"Data validation changed: {layout.name}!{column}")

    @staticmethod
    def _ensure_sheet_capacity(
        sheet: Any,
        layout: SheetLayout,
        row_count: int,
    ) -> int:
        """Extend a short protected template section by cloning its last input row."""

        required_last_row = max(
            layout.formatted_last_row,
            layout.data_start_row + max(0, row_count) - 1,
        )
        if required_last_row <= layout.formatted_last_row:
            return layout.formatted_last_row

        try:
            # Cells beyond the source template's preformatted area are locked.
            # Unprotect only a dynamically extended sheet so the new rows can
            # be written now and manually completed later. Existing sheets
            # that fit their reserved rows remain protected.
            sheet.Unprotect()
        except Exception as error:
            raise TemplateIntegrityError(
                f"Unable to extend writable business rows in {layout.name}: {error}"
            ) from error
        return required_last_row

    @staticmethod
    def _clear_data_rows(
        sheet: Any,
        layout: SheetLayout,
        last_row: int | None = None,
    ) -> None:
        start = sheet.Cells(layout.data_start_row, 1)
        end = sheet.Cells(last_row or layout.formatted_last_row, layout.column_count)
        try:
            sheet.Range(start, end).ClearContents()
        except Exception as error:
            raise TemplateIntegrityError(f"Unable to clear business rows in {layout.name}.") from error

    @staticmethod
    def _write_sheet_rows(sheet: Any, layout: SheetLayout, rows: list[TemplateRow]) -> None:
        for offset, row in enumerate(rows):
            target_row = layout.data_start_row + offset
            for column, value in row.values_by_column.items():
                cell = sheet.Cells(target_row, layout.column_number(column))
                if bool(sheet.ProtectContents) and bool(cell.Locked):
                    raise TemplateIntegrityError(f"Template input cell is locked: {layout.name}!{column}{target_row}")
                cell.Value = value

    @staticmethod
    def _read_sheet_assets(sheet: Any, layout: SheetLayout) -> set[str]:
        assets: set[str] = set()
        used_last_row = max(
            layout.formatted_last_row,
            int(sheet.UsedRange.Row) + int(sheet.UsedRange.Rows.Count) - 1,
        )
        for row_number in range(layout.data_start_row, used_last_row + 1):
            if layout.primary_screenshot_column:
                screenshot = ExcelTemplateWriter._cell_text(sheet, row_number, layout.column_number(layout.primary_screenshot_column))
                if screenshot:
                    assets.add(screenshot)
            if layout.attachment_column:
                attachments = ExcelTemplateWriter._cell_text(sheet, row_number, layout.column_number(layout.attachment_column))
                assets.update(split_attachment_names(attachments))
        return assets

    @staticmethod
    def _validate_row(layout: SheetLayout, row: TemplateRow) -> None:
        valid_columns = {chr(ord("A") + offset) for offset in range(layout.column_count)}
        unknown_columns = sorted(set(row.values_by_column) - valid_columns)
        if unknown_columns:
            raise TemplateIntegrityError(f"Unknown columns for {layout.name}: {', '.join(unknown_columns)}")
        if (
            row.values_by_column.get(layout.primary_screenshot_column)
            != row.primary_screenshot_name
        ):
            raise TemplateIntegrityError(f"Primary screenshot column does not match row assets: {layout.name}")
        for column, allowed_values in layout.validation_values.items():
            value = row.values_by_column.get(column)
            if value is not None and value not in allowed_values:
                raise TemplateIntegrityError(f"Invalid value for {layout.name}!{column}: {value!r}")

    @staticmethod
    def _cell_text(sheet: Any, row: int, column: int) -> str:
        value = sheet.Cells(row, column).Value
        return "" if value is None else str(value).strip()

    @staticmethod
    def _run_with_excel(
        operation: str,
        workbook_path: Path,
        rows_by_sheet: dict[str, list[TemplateRow]] | None = None,
    ) -> Any:
        for attempt in range(EXCEL_BUSY_RETRY_COUNT):
            try:
                return ExcelTemplateWriter._run_excel_once(
                    operation,
                    workbook_path,
                    rows_by_sheet,
                )
            except TemplateIntegrityError as error:
                if attempt + 1 >= EXCEL_BUSY_RETRY_COUNT or not _is_excel_busy_error(error):
                    raise
                time.sleep(0.75 * (attempt + 1))
        raise AssertionError("unreachable")

    @staticmethod
    def _run_excel_once(
        operation: str,
        workbook_path: Path,
        rows_by_sheet: dict[str, list[TemplateRow]] | None = None,
    ) -> Any:
        context = mp.get_context("spawn")
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_excel_worker,
            args=(send_connection, operation, str(workbook_path), rows_by_sheet),
        )
        try:
            process.start()
            send_connection.close()
            process.join(EXCEL_OPERATION_TIMEOUT_SECONDS)
            if process.is_alive():
                process.terminate()
                process.join()
                raise ExcelAutomationUnavailable("Excel template operation timed out and was terminated.")
            if not receive_connection.poll():
                raise ExcelAutomationUnavailable("Excel worker exited without returning a result.")
            try:
                succeeded, payload = receive_connection.recv()
            except EOFError as error:
                raise ExcelAutomationUnavailable("Excel worker stopped before returning a result.") from error
            if succeeded:
                return payload
            error_type, message = payload
            if error_type == ExcelAutomationUnavailable.__name__:
                raise ExcelAutomationUnavailable(message)
            if error_type == TemplateIntegrityError.__name__:
                raise TemplateIntegrityError(message)
            raise TemplateIntegrityError(f"Excel template operation failed: {message}")
        finally:
            send_connection.close()
            receive_connection.close()
            if process.is_alive():
                process.terminate()
                process.join()


def _is_excel_busy_error(error: Exception) -> bool:
    """Recognize transient RPC failures raised while Excel is briefly busy."""

    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "-2147418111",  # RPC_E_CALL_REJECTED
            "-2147417846",  # RPC_E_SERVERCALL_RETRYLATER
            "被呼叫方拒绝接收呼叫",
            "call was rejected by callee",
            "retry later",
        )
    )


def _excel_worker(
    connection: Any,
    operation: str,
    workbook_path_text: str,
    rows_by_sheet: dict[str, list[TemplateRow]] | None,
) -> None:
    """Run COM in a short-lived process so its teardown cannot affect the UI process."""

    app = None
    pythoncom: Any = None
    succeeded = False
    payload: Any
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        app = win32com.client.DispatchEx("Excel.Application")
        app.Visible = False
        app.DisplayAlerts = False
        app.AskToUpdateLinks = False
        writer = ExcelTemplateWriter(Path(workbook_path_text).name)
        workbook_path = Path(workbook_path_text)
        if operation == "write":
            writer._write_in_excel(app, workbook_path, rows_by_sheet or {})
            payload = None
        elif operation == "inspect":
            payload = writer._inspect_in_excel(app, workbook_path)
        elif operation == "validate":
            payload = writer._validate_contract_in_excel(app, workbook_path)
        else:
            raise ValueError(f"Unsupported Excel operation: {operation}")
        succeeded = True
    except ImportError:
        payload = (ExcelAutomationUnavailable.__name__, "pywin32 is required for fixed-template export.")
    except ExcelAutomationUnavailable as error:
        payload = (type(error).__name__, str(error))
    except TemplateIntegrityError as error:
        payload = (type(error).__name__, str(error))
    except Exception as error:
        payload = (type(error).__name__, str(error))
    try:
        connection.send((succeeded, payload))
    finally:
        connection.close()
        # pywin32 312 on Python 3.14 logs RPC_E_DISCONNECTED while releasing
        # an already-exited Excel server. Keep this runtime-only noise inside
        # the disposable worker while still releasing its COM apartment.
        with open(os.devnull, "w", encoding="utf-8") as discarded_errors:
            original_stderr = sys.stderr
            sys.stderr = discarded_errors
            try:
                if app is not None:
                    try:
                        app.Quit()
                    except Exception:
                        pass
                app = None
                if pythoncom is not None:
                    pythoncom.CoUninitialize()
            finally:
                sys.stderr = original_stderr
