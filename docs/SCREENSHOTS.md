# Screenshots and visual verification

All product images in `docs/images/` were generated from **actually running** this
application on **Windows 11 (Python 3.11)**, using synthetic demo files created by
`smart-organizer demo` (see `demo.py`). No mockups, hand-drawn illustrations, or
invented UI were used. The workflow diagram is rendered programmatically from the
actual module layout and accurately mirrors the implementation.

| File | What it shows | How it was captured |
|---|---|---|
| `cli-scan.png` | Real `scan` output on the demo folder | Terminal-style render of captured stdout |
| `cli-preview.png` | Real `preview` (dry-run) move table | Terminal-style render of captured stdout |
| `duplicates.png` | Real SHA-256 duplicate groups + reclaimable estimate | Terminal-style render of captured stdout |
| `gui-dashboard.png` | Real Tkinter window after a live scan of the demo folder | On-screen window capture (GDI BitBlt) from the running GUI |
| `organization-report.png` | Real operation report built from a persisted manifest | Rendered from `smart-organizer report <run_id> --format json` |
| `workflow.png` | Architecture / workflow diagram | Generated programmatically to match the real pipeline |

## How to reproduce the captures

```powershell
# Create disposable demo data (refuses non-empty directories)
smart-organizer demo C:\sfo-demo

# Real scans / previews / duplicate detection
smart-organizer scan C:\sfo-demo --recursive
smart-organizer preview C:\sfo-demo
smart-organizer duplicates C:\sfo-demo

# Real apply, then a real report
smart-organizer organize C:\sfo-demo --apply
smart-organizer history
smart-organizer report <RUN_ID> --format json
```

The GUI image can be reproduced on any desktop session by running `smart-organizer gui`,
choosing the demo folder, pressing **Scan & preview**, and capturing the window.

## Notes and honesty

- Screenshots use only the disposable demo folder and the synthetic sample data the tool
  itself generates. They contain no personal filenames or private documents.
- The `gui-dashboard.png` was captured on a real Windows desktop session; the browser-based
  HTML report could **not** be screenshotted here because the connected browser window was
  not visible, so `organization-report.png` is a faithful render of the actual report data
  instead of a browser capture. The HTML report itself is generated correctly by
  `smart-organizer report <RUN_ID> --format html` and is verified by tests.
- Nothing in this document claims CI success, coverage numbers, or cross-platform execution
  that did not actually occur.