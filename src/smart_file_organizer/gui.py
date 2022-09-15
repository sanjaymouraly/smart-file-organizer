"""Optional Tkinter desktop interface backed by the same core engine as the CLI."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any

from .config import Config, default_config_path, resolve_storage_dir
from .duplicates import find_duplicates
from .history import ManifestStore, undo_operation
from .organizer import apply_plan, build_organization_plan, default_destination
from .reports import scan_report_data, write_report
from .scanner import scan_directory
from .utils import format_bytes


class OrganizerApp:
    """Small responsive GUI shell; all filesystem work runs on worker threads."""

    def __init__(self, root: Any, config: Config) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.config = config
        self.storage_dir = resolve_storage_dir(config)
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.scan_result = None
        self.duplicate_analysis = None
        self.plan = None
        self.busy = False
        self.folder_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.include_hidden_var = tk.BooleanVar(value=config.include_hidden)
        self.recursive_var = tk.BooleanVar(value=True)
        self.by_month_var = tk.BooleanVar(value=False)
        self.by_extension_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Choose a folder, then select Scan & preview.")
        self.operation_var = tk.StringVar()
        self._build_ui()
        self._poll_events()
        self._reload_history()

    def _build_ui(self) -> None:
        tk, ttk, root = self.tk, self.ttk, self.root
        root.title("Smart File Organizer & Duplicate Detector")
        root.geometry("1120x780")
        root.minsize(820, 580)

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Header.TLabel", font=("TkDefaultFont", 16, "bold"), foreground="#172a4b")
        style.configure("Hint.TLabel", foreground="#51627d")
        style.configure("Treeview", rowheight=25)

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Smart File Organizer", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Scan safely, preview every move, and keep exact duplicates reversible.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        folder_row = ttk.Frame(outer)
        folder_row.pack(fill="x", pady=(0, 6))
        ttk.Label(folder_row, text="Folder:").pack(side="left")
        self.folder_entry = ttk.Entry(folder_row, textvariable=self.folder_var)
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.browse_button = ttk.Button(folder_row, text="Browse…", command=self._browse)
        self.browse_button.pack(side="left")

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=(2, 7))
        ttk.Checkbutton(options, text="Include subfolders", variable=self.recursive_var).pack(
            side="left"
        )
        ttk.Checkbutton(
            options, text="Include hidden files", variable=self.include_hidden_var
        ).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(options, text="Group by year/month", variable=self.by_month_var).pack(
            side="left", padx=(12, 0)
        )
        ttk.Checkbutton(options, text="Group by extension", variable=self.by_extension_var).pack(
            side="left", padx=(12, 0)
        )

        destination_row = ttk.Frame(outer)
        destination_row.pack(fill="x", pady=(0, 8))
        ttk.Label(destination_row, text="Destination:").pack(side="left")
        self.destination_entry = ttk.Entry(destination_row, textvariable=self.destination_var)
        self.destination_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(
            destination_row, text="(blank = selected folder / Organized)", style="Hint.TLabel"
        ).pack(side="left")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 7))
        self.scan_button = ttk.Button(buttons, text="Scan & preview", command=self._start_scan)
        self.scan_button.pack(side="left")
        self.apply_button = ttk.Button(
            buttons, text="Apply moves…", command=self._confirm_apply, state="disabled"
        )
        self.apply_button.pack(side="left", padx=(8, 0))
        self.export_button = ttk.Button(
            buttons, text="Export report…", command=self._export_report, state="disabled"
        )
        self.export_button.pack(side="left", padx=(8, 0))
        self.save_button = ttk.Button(
            buttons, text="Save preferences", command=self._save_preferences
        )
        self.save_button.pack(side="right")

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 6))
        ttk.Label(outer, textvariable=self.status_var, wraplength=1040).pack(
            anchor="w", pady=(0, 7)
        )

        panes = ttk.Panedwindow(outer, orient="vertical")
        panes.pack(fill="both", expand=True)
        upper = ttk.Frame(panes)
        lower = ttk.Frame(panes)
        panes.add(upper, weight=4)
        panes.add(lower, weight=2)

        summary_frame = ttk.LabelFrame(upper, text="Category summary", padding=6)
        summary_frame.pack(fill="x", pady=(0, 6))
        self.category_tree = ttk.Treeview(
            summary_frame, columns=("files", "size"), show="headings", height=4
        )
        self.category_tree.heading("files", text="Files")
        self.category_tree.heading("size", text="Total size")
        self.category_tree.column("files", width=90, anchor="e")
        self.category_tree.column("size", width=120, anchor="e")
        self.category_tree.pack(fill="x")
        self.category_tree.bind("<<TreeviewSelect>>", lambda _event: None)
        self.category_tree.heading("#0", text="Category")
        self.category_tree["show"] = ("tree", "headings")
        self.category_tree.column("#0", width=250, anchor="w")

        files_frame = ttk.LabelFrame(upper, text="Files and proposed destinations", padding=6)
        files_frame.pack(fill="both", expand=True)
        columns = ("size", "category", "destination")
        self.file_tree = ttk.Treeview(files_frame, columns=columns, show="tree headings", height=12)
        self.file_tree.heading("#0", text="Path")
        self.file_tree.heading("size", text="Size")
        self.file_tree.heading("category", text="Category")
        self.file_tree.heading("destination", text="Proposed destination")
        self.file_tree.column("#0", width=350, minwidth=170)
        self.file_tree.column("size", width=90, anchor="e")
        self.file_tree.column("category", width=120)
        self.file_tree.column("destination", width=420, minwidth=180)
        yscroll = ttk.Scrollbar(files_frame, orient="vertical", command=self.file_tree.yview)
        xscroll = ttk.Scrollbar(files_frame, orient="horizontal", command=self.file_tree.xview)
        self.file_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.file_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        files_frame.rowconfigure(0, weight=1)
        files_frame.columnconfigure(0, weight=1)

        duplicates_frame = ttk.LabelFrame(lower, text="Exact duplicates (SHA-256)", padding=6)
        duplicates_frame.pack(fill="both", expand=True)
        self.duplicates_text = tk.Text(duplicates_frame, height=7, wrap="none", state="disabled")
        duplicate_scroll = ttk.Scrollbar(
            duplicates_frame, orient="vertical", command=self.duplicates_text.yview
        )
        self.duplicates_text.configure(yscrollcommand=duplicate_scroll.set)
        self.duplicates_text.pack(side="left", fill="both", expand=True)
        duplicate_scroll.pack(side="right", fill="y")

        history_row = ttk.Frame(outer)
        history_row.pack(fill="x", pady=(8, 0))
        ttk.Label(history_row, text="Recent operation:").pack(side="left")
        self.history_box = ttk.Combobox(
            history_row, textvariable=self.operation_var, state="readonly", width=54
        )
        self.history_box.pack(side="left", padx=8)
        self.undo_button = ttk.Button(
            history_row, text="Undo selected…", command=self._confirm_undo, state="disabled"
        )
        self.undo_button.pack(side="left")

    def _browse(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askdirectory(title="Select a folder to scan")
        if selected:
            self.folder_var.set(selected)
            if not self.destination_var.get().strip():
                self.destination_var.set(str(Path(selected) / self.config.destination_name))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.scan_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.apply_button.configure(
            state="disabled" if busy or not self.plan or not self.plan.items else "normal"
        )
        self.export_button.configure(state="disabled" if busy or not self.scan_result else "normal")
        self.progress.start(12) if busy else self.progress.stop()

    def _start_scan(self) -> None:
        if self.busy:
            return
        folder = self.folder_var.get().strip()
        if not folder:
            from tkinter import messagebox

            messagebox.showwarning(
                "Choose a folder", "Select a directory before scanning.", parent=self.root
            )
            return
        root_path = Path(folder).expanduser().resolve(strict=False)
        destination_text = self.destination_var.get().strip()
        destination = (
            Path(destination_text).expanduser().resolve(strict=False)
            if destination_text
            else default_destination(root_path, self.config)
        )
        recursive = self.recursive_var.get()
        include_hidden = self.include_hidden_var.get()
        by_month = self.by_month_var.get()
        by_extension = self.by_extension_var.get()
        self.plan = None
        self._set_busy(True)
        self.status_var.set("Scanning files and calculating safety fingerprints…")

        def worker() -> None:
            try:
                scan = scan_directory(
                    root_path,
                    recursive=recursive,
                    include_hidden=include_hidden,
                    config=self.config,
                    excluded_dirs=self.config.excluded_dirs,
                    excluded_patterns=self.config.excluded_patterns,
                    excluded_paths=tuple(
                        path
                        for path in (self.storage_dir, destination, self.config.config_file)
                        if path is not None
                    ),
                    progress=lambda state: self.events.put(
                        (
                            "progress",
                            f"Scanned {state.files_seen} files in {state.directories_seen} folders…",
                        )
                    ),
                )
                analysis = find_duplicates(
                    scan.files,
                    config=self.config,
                    progress=lambda done, total, _path: self.events.put(
                        ("progress", f"Checking duplicate candidates: {done}/{total}…")
                    ),
                )
                plan = build_organization_plan(
                    scan,
                    destination,
                    config=self.config,
                    by_month=by_month,
                    by_extension=by_extension,
                    protected_paths=(self.storage_dir,),
                    progress=lambda done, total, _path: self.events.put(
                        ("progress", f"Fingerprinting organization preview: {done}/{total}…")
                    ),
                )
                self.events.put(("scan_done", scan, analysis, plan))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, name="smart-organizer-scan", daemon=True).start()

    def _confirm_apply(self) -> None:
        if not self.plan or not self.plan.items or self.busy:
            return
        from tkinter import messagebox

        answer = messagebox.askyesno(
            "Apply organization plan?",
            f"Move {len(self.plan.items)} file(s) to:\n{self.plan.destination_root}\n\n"
            "Existing files will never be overwritten. You can undo completed moves from history.",
            parent=self.root,
        )
        if not answer:
            return
        plan = self.plan
        store = ManifestStore(self.storage_dir)
        self._set_busy(True)
        self.status_var.set("Applying safe, per-file verified moves…")

        def worker() -> None:
            try:
                manifest = apply_plan(
                    plan,
                    store,
                    kind="organize",
                    chunk_size=self.config.hash_chunk_size,
                    progress=lambda done, total, _path, status: self.events.put(
                        ("progress", f"Moving files: {done}/{total} ({status})…")
                    ),
                )
                self.events.put(("apply_done", manifest))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, name="smart-organizer-apply", daemon=True).start()

    def _confirm_undo(self) -> None:
        if self.busy:
            return
        selected = self.history_box.current()
        manifests, _errors = ManifestStore(self.storage_dir).list_manifests()
        if selected < 0 or selected >= len(manifests):
            from tkinter import messagebox

            messagebox.showinfo(
                "No operation selected", "Choose an operation from history first.", parent=self.root
            )
            return
        manifest = manifests[selected]
        from tkinter import messagebox

        if not messagebox.askyesno(
            "Undo operation?",
            f"Restore files from operation {manifest.run_id}?\n\n"
            "Modified destinations and occupied original paths will be skipped safely.",
            parent=self.root,
        ):
            return
        self._set_busy(True)
        self.status_var.set(f"Undoing operation {manifest.run_id}…")

        def worker() -> None:
            try:
                result = undo_operation(
                    ManifestStore(self.storage_dir),
                    manifest.run_id,
                    chunk_size=self.config.hash_chunk_size,
                )
                self.events.put(("undo_done", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, name="smart-organizer-undo", daemon=True).start()

    def _export_report(self) -> None:
        if not self.scan_result or self.busy:
            return
        from tkinter import filedialog, messagebox

        destination = filedialog.asksaveasfilename(
            title="Export scan and duplicate report",
            defaultextension=".html",
            filetypes=(
                ("HTML report", "*.html"),
                ("JSON report", "*.json"),
                ("CSV report", "*.csv"),
            ),
            parent=self.root,
        )
        if not destination:
            return
        try:
            written = write_report(
                scan_report_data(self.scan_result, self.duplicate_analysis), destination
            )
            messagebox.showinfo("Report exported", f"Report saved to:\n{written}", parent=self.root)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.root)

    def _save_preferences(self) -> None:
        from tkinter import messagebox

        self.config.include_hidden = self.include_hidden_var.get()
        destination_text = self.destination_var.get().strip()
        if destination_text:
            # Saving a selected destination updates the default folder name only when it
            # is a single component; arbitrary per-scan destinations are not persisted.
            selected = Path(destination_text).expanduser()
            if selected.name:
                self.config.destination_name = selected.name
        path = self.config.config_file or default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(
                json.dumps(self.config.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror("Could not save preferences", str(exc), parent=self.root)
            return
        messagebox.showinfo("Preferences saved", f"Saved to:\n{path}", parent=self.root)

    def _reload_history(self) -> None:
        manifests, _errors = ManifestStore(self.storage_dir).list_manifests()
        self._history_manifests = manifests[:30]
        labels = [
            f"{item.run_id} — {item.kind} — {item.status}" for item in self._history_manifests
        ]
        self.history_box.configure(values=labels)
        if labels:
            self.history_box.current(0)
            self.undo_button.configure(
                state="normal"
                if any(item.status == "moved" for item in self._history_manifests[0].items)
                else "disabled"
            )
        else:
            self.operation_var.set("")
            self.undo_button.configure(state="disabled")

    def _display_results(self, scan: Any, analysis: Any, plan: Any) -> None:
        self.scan_result, self.duplicate_analysis, self.plan = scan, analysis, plan
        self.category_tree.delete(*self.category_tree.get_children())
        summary: dict[str, list[int]] = {}
        for record in scan.files:
            counts = summary.setdefault(record.category, [0, 0])
            counts[0] += 1
            counts[1] += record.size
        for category in sorted(summary, key=str.casefold):
            self.category_tree.insert(
                "",
                "end",
                text=category,
                values=(summary[category][0], format_bytes(summary[category][1])),
            )
        self.file_tree.delete(*self.file_tree.get_children())
        destination_by_source = {str(item.source): str(item.destination) for item in plan.items}
        display_records = scan.files[:1500]
        for record in display_records:
            self.file_tree.insert(
                "",
                "end",
                text=str(record.path),
                values=(
                    format_bytes(record.size),
                    record.category,
                    destination_by_source.get(str(record.path), "— already organized / skipped"),
                ),
            )
        if len(scan.files) > len(display_records):
            self.status_var.set(
                f"Scanned {len(scan.files)} files; showing first {len(display_records)} in the table. "
                f"Preview has {len(plan.items)} planned move(s)."
            )
        duplicate_lines = [
            f"{len(analysis.groups)} group(s); estimated reclaimable: {format_bytes(analysis.reclaimable_bytes)}. "
            "Files are only reported here."
        ]
        if not analysis.groups:
            duplicate_lines.append("No exact duplicates found.")
        for index, group in enumerate(analysis.groups, start=1):
            duplicate_lines.append(
                f"\nGroup {index}: {len(group.files)} copies × {format_bytes(group.size)}; "
                f"estimated recoverable: {format_bytes(group.reclaimable_bytes)}"
            )
            duplicate_lines.append(f"  Keep by default: {group.default_keep}")
            duplicate_lines.extend(f"  {item.path}" for item in group.files)
        self.duplicates_text.configure(state="normal")
        self.duplicates_text.delete("1.0", "end")
        self.duplicates_text.insert("1.0", "\n".join(duplicate_lines))
        self.duplicates_text.configure(state="disabled")
        self.status_var.set(
            f"Scan complete: {len(scan.files)} files, {len(plan.items)} proposed move(s), "
            f"{len(analysis.groups)} duplicate group(s), {len(scan.issues) + len(analysis.issues)} warning(s). "
            "No files were changed."
        )
        self.apply_button.configure(state="normal" if plan.items else "disabled")
        self.export_button.configure(state="normal")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                event_type = event[0]
                if event_type == "progress":
                    self.status_var.set(event[1])
                elif event_type == "scan_done":
                    self._set_busy(False)
                    self._display_results(event[1], event[2], event[3])
                elif event_type == "apply_done":
                    self._set_busy(False)
                    manifest = event[1]
                    self.status_var.set(
                        f"Operation {manifest.run_id}: {manifest.status}; "
                        f"{sum(item.status == 'moved' for item in manifest.items)} file(s) moved."
                    )
                    self._reload_history()
                elif event_type == "undo_done":
                    self._set_busy(False)
                    manifest = event[1]
                    self.status_var.set(
                        f"Undo status: {manifest.status}. Review history for skipped files."
                    )
                    self._reload_history()
                elif event_type == "error":
                    self._set_busy(False)
                    self.status_var.set(f"Operation failed safely: {event[1]}")
                    from tkinter import messagebox

                    messagebox.showerror("Operation failed", event[1], parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)


def launch_gui(config: Config | None = None) -> None:
    """Start the optional GUI or raise an actionable error in headless environments."""
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError(
            "Tkinter is not installed. The CLI remains available; on Debian/Ubuntu install "
            "python3-tk, then run 'smart-organizer gui' again."
        ) from exc
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError(
            "Tkinter is installed but no graphical display is available. Use the CLI, or run "
            "the GUI in a desktop session."
        ) from exc
    OrganizerApp(root, config or Config())
    root.mainloop()
