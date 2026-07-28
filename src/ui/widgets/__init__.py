"""Reusable desktop workflow widgets."""

from src.ui.widgets.file_selector import FileSelector
from src.ui.widgets.auth_manager import AuthManagerDialog
from src.ui.widgets.log_viewer import LogViewer
from src.ui.widgets.progress_panel import ProgressPanel
from src.ui.widgets.result_table import ResultTable
from src.ui.widgets.task_options import TaskOptionsWidget

__all__ = [
    "FileSelector",
    "AuthManagerDialog",
    "LogViewer",
    "ProgressPanel",
    "ResultTable",
    "TaskOptionsWidget",
]
