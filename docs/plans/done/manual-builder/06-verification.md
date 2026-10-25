# 06 Hardening and end-to-end checks

**Goal:** show that the builder works in a real browser and that no edit can silently lose a model.

**Build:**
- Playwright checks in the same style as the viewer's browser test (`app/web/viewer-deps/test/viewer.browser.mjs`): open a model, select a part, move it, undo, publish. Take a screenshot of each step and look at it.
- A round-trip test: hand-edit a plan, rebuild, reload, and check the MPD is identical to a rebuild from the saved plan.
- A fault test: kill the server during a save and confirm the last good plan is intact.
- A load check: a 2,000-part model stays responsive, with no edit request per frame and a documented rebuild time.
- Record in `docs/plans/done/manual-builder/RESULTS.md` what passed, with timings and the screenshot file names.

**Acceptance:** all checks pass in the container. Report any failure with its output, not a summary.
