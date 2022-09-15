from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from smart_file_organizer.config import Config
from smart_file_organizer.gui import OrganizerApp


class FakeVariable:
    def __init__(self, value: Any = None, **_kwargs: Any) -> None:
        self.value = value

    def get(self) -> Any:
        return self.value

    def set(self, value: Any) -> None:
        self.value = value


class FakeWidget:
    def __init__(self, *_args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.values: dict[str, Any] = {}
        self.rows: list[dict[str, Any]] = []
        self.selected = -1
        self.text = ""

    def theme_use(self, *_args: Any) -> None:
        pass

    def pack(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def grid(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def bind(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def configure(self, *_args: Any, **kwargs: Any) -> None:
        self.values.update(kwargs)

    config = configure

    def heading(self, column: str, **kwargs: Any) -> None:
        self.values[f"heading:{column}"] = kwargs

    def column(self, column: str, **kwargs: Any) -> None:
        self.values[f"column:{column}"] = kwargs

    def add(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def rowconfigure(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def columnconfigure(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def start(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def stop(self) -> None:
        pass

    def set(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def yview(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def xview(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def delete(self, *_args: Any, **_kwargs: Any) -> None:
        self.rows.clear()
        self.text = ""

    def insert(self, *args: Any, **kwargs: Any) -> str:
        if "text" in kwargs or "values" in kwargs:
            self.rows.append({"args": args, **kwargs})
        elif len(args) >= 2:
            self.text += str(args[-1])
        return str(len(self.rows))

    def get_children(self) -> tuple[()]:
        return ()

    def current(self, index: int | None = None) -> int:
        if index is not None:
            self.selected = index
        return self.selected

    def __setitem__(self, key: str, value: Any) -> None:
        self.values[key] = value

    def __getitem__(self, key: str) -> Any:
        return self.values.get(key)


class FakeRoot(FakeWidget):
    def title(self, *_args: Any) -> None:
        pass

    def geometry(self, *_args: Any) -> None:
        pass

    def minsize(self, *_args: Any) -> None:
        pass

    def after(self, *_args: Any) -> None:
        pass


def _install_fake_tk(monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter
    from tkinter import ttk

    monkeypatch.setattr(tkinter, "StringVar", FakeVariable)
    monkeypatch.setattr(tkinter, "BooleanVar", FakeVariable)
    monkeypatch.setattr(tkinter, "Text", FakeWidget)
    for name in (
        "Style",
        "Frame",
        "Label",
        "Entry",
        "Button",
        "Checkbutton",
        "Progressbar",
        "LabelFrame",
        "Treeview",
        "Scrollbar",
        "Panedwindow",
        "Combobox",
    ):
        monkeypatch.setattr(ttk, name, FakeWidget)


def test_gui_scan_apply_undo_export_and_preferences_callbacks(
    sample_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from tkinter import filedialog, messagebox

    _install_fake_tk(monkeypatch)

    class InlineThread:
        def __init__(self, *, target: Any, **_kwargs: Any) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(threading, "Thread", InlineThread)
    monkeypatch.setattr(messagebox, "askyesno", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(messagebox, "showinfo", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *_args, **_kwargs: None)
    exported = tmp_path / "gui-report.html"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **_kwargs: str(exported))
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kwargs: str(sample_tree))

    config = Config(storage_dir=str(tmp_path / "state"), config_file=tmp_path / "prefs.json")
    app = OrganizerApp(FakeRoot(), config)
    app.folder_var.set(str(sample_tree))
    app.destination_var.set(str(sample_tree / "Organized"))
    app._start_scan()
    app._poll_events()
    assert app.scan_result is not None
    assert app.plan is not None
    assert app.apply_button.values["state"] == "normal"
    assert app.file_tree.rows
    assert "group(s)" in app.duplicates_text.text.lower()

    app._export_report()
    assert exported.exists()
    assert "Scan report" in exported.read_text(encoding="utf-8")

    app._confirm_apply()
    app._poll_events()
    assert app.status_var.get().startswith("Operation ")
    assert any(path.is_file() for path in (sample_tree / "Organized").rglob("*"))

    app._confirm_undo()
    app._poll_events()
    assert (sample_tree / "report.pdf").exists()
    assert app.status_var.get().startswith("Undo status:")

    app.destination_var.set(str(sample_tree / "Organized"))
    app._save_preferences()
    assert config.config_file.exists()
    assert app.config.destination_name == "Organized"
    app.folder_var.set("")
    app._browse()
    assert app.folder_var.get() == str(sample_tree)

    # A worker exception is delivered to the main-thread queue and clears the busy state.
    app.events.put(("error", "simulated GUI callback error"))
    app._set_busy(True)
    app._poll_events()
    assert not app.busy
    assert "simulated GUI callback error" in app.status_var.get()
