from __future__ import annotations

import builtins
from typing import Any

import pytest

from smart_file_organizer import gui
from smart_file_organizer.config import Config


def test_launch_gui_reports_missing_tkinter(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def without_tkinter(name: str, *args: Any, **kwargs: Any):
        if name == "tkinter":
            raise ImportError("simulated Tkinter missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_tkinter)
    with pytest.raises(RuntimeError, match="Tkinter is not installed"):
        gui.launch_gui()


def test_launch_gui_translates_no_display_to_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkinter

    def fail_root() -> None:
        raise tkinter.TclError("no display")

    monkeypatch.setattr(tkinter, "Tk", fail_root)
    with pytest.raises(RuntimeError, match="no graphical display"):
        gui.launch_gui()


def test_launch_gui_builds_app_and_runs_mainloop(monkeypatch: pytest.MonkeyPatch) -> None:
    import tkinter

    class FakeRoot:
        def __init__(self) -> None:
            self.loop_started = False

        def mainloop(self) -> None:
            self.loop_started = True

    fake_root = FakeRoot()
    constructed: list[tuple[Any, Any]] = []
    monkeypatch.setattr(tkinter, "Tk", lambda: fake_root)
    monkeypatch.setattr(
        gui,
        "OrganizerApp",
        lambda root, config: constructed.append((root, config)),
    )
    gui.launch_gui()
    assert len(constructed) == 1
    assert constructed[0][0] is fake_root
    assert isinstance(constructed[0][1], Config)
    assert fake_root.loop_started
