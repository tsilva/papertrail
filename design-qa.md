# Papertrail visual verification

final result: passed

**Source visual truth**

- `/Users/tsilva/repos/tsilva/papertrail/frontend/design/documents.png`: selected Documents option 1, 1487 × 1058 pixels.
- `/Users/tsilva/repos/tsilva/papertrail/frontend/design/review.png`: selected Review option 2, 1487 × 1058 pixels.
- `/Users/tsilva/repos/tsilva/papertrail/frontend/design/combined.png`: revised shared shell, 2103 × 748 pixels; one month selector and no duplicate transaction amount/date section.

**Browser evidence**

Verified in the Codex in-app browser at `http://127.0.0.1:62670/`.
All preview data and screenshots are synthetic, stored outside the repository.

Evidence root: `/Users/tsilva/.codex/generated_images/01a13a1f-801d-71f3-a581-15ab95cb8819/papertrail-qa/`.

- `documents-final.jpg`: selected Northline Studio invoice, Documents active.
- `review-final.jpg`: selected Northline Studio transaction, pending review.
- `review-tablet.jpg`: 768 × 1024 CSS viewport, queue beside preview and inspector below.
- `review-mobile-top.jpg`: 390 × 844 CSS viewport, horizontal queue and full source page.
- `review-mobile-decision.jpg`: mobile comparison and reachable review actions.
- `documents-mobile.jpg`: mobile filters, document rows, and selected document heading.

Desktop CSS viewport: 1440 × 1024, browser density 1. Review capture is 1440 × 1024 pixels. The Documents browser capture is 1425 × 1013 pixels, uniformly scaled by the capture runtime; normalize it proportionally to the CSS viewport. Normalize each original 1487 × 1058 source to the same desktop frame. Compare the two screens independently; the combined sheet is a shared-shell reference, not a single app viewport.

Source and final captures were opened together for comparison. The same invoice, date, currency, selection, and active tab were used. Full-view evidence confirms the flat Documents table/preview and three-column Review queue/preview/inspector. Typography, separators, controls, and comparison labels are readable at these capture sizes, so an additional cropped comparison was not needed. The mobile decision capture supplies a closer view of comparison typography and action alignment.

**Findings and comparison history**

1. [P2, fixed] Oversized PDF previews hid extracted details and cropped source evidence. Evidence: `documents-initial.jpg`, `review-initial.jpg`. Fixed flex sizing and constrained images to fit their preview viewport; zoom remains available in Review. Final captures show the complete page, document metadata, and all desktop actions.
2. [P2, fixed] Review proportions and small table text drifted from the selected design. Expanded the inspector to 390px and queue to 260px at the target viewport; increased Documents labels to 14–15px and the amount to 54px. Final captures preserve readable information hierarchy.
3. [P2, fixed] White primary-action labels needed stronger contrast. Darkened only primary-action backgrounds from the selection accent to `#007f87`, retaining `#00959d` for accents. Final captures show readable action labels with the same teal design language.
4. [P2, fixed] Missing documents could display “Multiple dates.” Now show an absent value and a “Missing document” action heading. Both review actions remain disabled without complete evidence.

No actionable P0/P1/P2 findings remain.

**Required fidelity surfaces**

- Fonts: Arial/Helvetica provides the reference's clean sans-serif treatment; clear 14–16px product labels, bold names and display amounts, tabular currency alignment, and wrapping for long queue names. Font family is an inferred match because generated references do not provide font metadata.
- Spacing: light 70px desktop header, flat row dividers, 53/47 Documents split, 260px queue and 390px inspector, restrained radii, generous preview canvas. No KPI cards or redundant top-level navigation.
- Colors: white app surface, navy `#11284c`, subdued `#536f97`, pale teal selection, cool gray source canvas. Actions use darker teal for label contrast. No dark-mode surfaces.
- Image quality: actual repository brand icon, supplied at appropriate resolution, and real PDF page rendering. Icons come from the Phosphor library, not custom SVG art. No screen-sized mock image substitutes for interactive UI.
- Copy: selected document labels, extracted details, bank transaction, comparison, suggested match, approve, reject, and navigation retain the target intent. Errors, empty states, review status, and undo are coherent product states.

**Accepted product constraints**

Source invoices are rendered from files; their artwork and layout vary with the actual document. The synthetic fixture has simpler invoice typography than the generated reference. No fabricated invoice logo or extraction highlight is added to source pages. The supplied Papertrail icon is used instead of the generated logo reinterpretation. The fixture includes an additional missing-document transaction to verify that state. Bank statements without a total show an absent amount rather than an invented balance. Expected-unmatched-document decisions and deduplication remain available in the supported Gradio tools.

**Interaction and validation evidence**

- In-app browser: Documents/Review navigation, document selection, search, no-results reset, type filter, XLSX preview, month filter and refresh, approve, reject, undo, reload persistence, approved filter, clear decision, missing evidence, mobile action reachability.
- Browser console: no errors or warnings in the verified screens.
- No horizontal page overflow at desktop, 768px tablet, or 390px mobile widths. Smaller screens intentionally scroll vertically to source evidence and decisions.
- Backend tests cover downloads, PDF page bounds, multi-page PDFs, multi-document matches, rejected and approved persistence, stale revisions, changed hashes, missing evidence, tokens, symlink containment, isolated monthly ZIP downloads, and empty export folders.
- 183 Python tests and 24 subtests pass. Required January–April 2026 golden regressions pass (117 approved transactions).
- Svelte check: zero errors/warnings. Two frontend tests pass. Production build passes. Production dependency audit reports no known vulnerabilities.
- Python wheel contains frontend HTML, JavaScript, CSS, and brand image; Node.js is unnecessary at runtime.

**Open questions**

None blocking. Browser verification used fictional documents; no private profile review decisions were changed.

**Implementation checklist**

- [x] Implement selected Documents and Review screens in Svelte.
- [x] Connect profile-scoped sidecars, source previews, and durable decisions.
- [x] Preserve standalone Gradio tools and unrelated repository work.
- [x] Complete browser interaction and visual checks at desktop/tablet/mobile sizes.
- [x] Bundle frontend assets and update existing documentation.

**Follow-up polish**

[P3] A bespoke header-sized Papertrail logo could more closely match the generated mark. The existing repository icon is intentionally reused.

**Document row interaction regression**

The native document-selection button now covers the entire row through its transparent hit area. The row retains table semantics, one keyboard focus target, a row-wide focus indicator, and a hover state. In the Codex in-app browser, clicking the date, issuer, amount, and empty row padding each selected the correct document preview. Enter and Space both activated the focused document button. No browser errors or warnings were recorded. Evidence: `/Users/tsilva/.codex/generated_images/01a13a1f-801d-71f3-a581-15ab95cb8819/papertrail-qa/row-click-fixed.jpg`, using synthetic documents. Svelte checks, two frontend tests, 183 Python tests, and all four golden reconciliation months passed after the fix.


**Dashboard bug audit — 14 October 2026**

Completed hands-on testing in the Codex in-app browser against the bundled
production dashboard at `http://127.0.0.1:59247/`. Used synthetic data containing
two months, an undated invoice, a two-page PDF, a spreadsheet, and missing
evidence. Review decisions touched only this temporary fixture.

| Journey step | Initial finding | Final health |
| --- | --- | --- |
| Browse and filter Documents | Search, type and selected document reset when switching tabs or refreshing | Passed: view state is retained |
| Choose a month | `$UNKNOWN$` became a bogus `$UNKNOW` month and appeared verbatim in dates | Passed: valid calendar months only; undated documents show Unknown date under All months |
| Inspect source evidence | Damaged PDF produced a JSON parsing error | Passed: readable error, retry succeeds after restoring the file; page navigation and XLSX preview work |
| Choose a review status | Selecting Rejected after an approved row silently changed the filter to All | Passed: explicit filters remain selected and empty results have accurate copy |
| Navigate and decide | Clicking a queue button disabled shortcuts; deciding a middle row returned to the first | Passed: j/k and Y/N work after clicks; pending queue advances to the next surviving row |
| Undo | After changing months, U restored the decision but left its transaction hidden | Passed: undo restores the row, decision, and month |
| Dismiss menus | Escape and outside clicks left menus open | Passed: both header and review action menus close |
| Review on tablet/mobile | Controls and source evidence needed verification | Passed: no horizontal page overflow at 768px or 390px; mobile decision controls are reachable |

Eight reproduced bugs were fixed: filter state loss, invalid month/date labels,
unreadable preview errors, overridden review filters, disabled shortcuts,
incorrect decision progression, hidden cross-month undo, and menu dismissal.
The earlier whole-row document selection fix is retained. Missing-evidence
approve/reject actions remain disabled. Direct Documents-to-Review links
explicitly include the linked transaction, independently of the previous filter.

Evidence root (outside Git):
`/Users/tsilva/.codex/generated_images/01a13a1f-801d-71f3-a581-15ab95cb8819/dashboard-bug-audit/`.

Accepted screenshots were reopened from their saved paths for inspection:

- `06-preview-error-fixed.jpg`: readable damaged-source error and retry action.
- `07-review-advance-fixed.jpg`: rejecting Cedar advances to Harbor, second surviving pending row.
- `08-review-filter-fixed.jpg`: Rejected filter retained after switching tabs.
- `09-unknown-date-fixed.jpg`: undated document remains browsable with readable labels.
- `12-review-mobile-actions.jpg`: comparison, decision actions and navigation fit the 390px viewport.
- `13-undo-month-fixed.jpg`: October Cedar restored after undo from September.

Browser console had no errors or warnings in the final checks. Svelte check
reported zero errors/warnings; six frontend tests and the production build passed.
All 189 Python tests and 24 subtests passed, together with the January–April
golden reconciliation checks (117 approved transactions). Regression tests now
cover queue filtering/progression, calendar validity, plain-text server errors,
and unreadable/missing PDF and XLSX sources. No unresolved bugs were found in
these tested flows. This audit does not assert coverage of every possible profile
or malformed source format.


**Transaction-first restoration — 14 October 2026**

final result: passed

Functional authority is the previous `tools/review.py` workflow: month-wide bank
statement tables, account/period context, row/date/description/signed amount,
rule/status/approval/confidence/files, source preview and raw JSON, expected
unmatched documents, and month ZIP. `dashboard-workflow.md` maps each previous
task to the restored Svelte surface. Transactions is the starting screen;
Documents remains a supporting browser and Review is a focused inspection step.

Source visual truth remains `frontend/design/documents.png` and
`frontend/design/review.png`. The user explicitly changed the information
hierarchy to prioritize transactions and restore previous capabilities. The new
monthly table layout is an intentional application of those styles, rather than
a literal copy of the document-first mockup. The focused review retains the
three-column queue/source/inspector composition.

Current-run evidence root, outside Git:
`/Users/tsilva/.codex/generated_images/01a13a1f-801d-71f3-a581-15ab95cb8819/transactions-first/`.

- `02-legacy-monthly.jpg`: actual previous Gradio monthly transaction table, captured at 1280 × 720.
- `03-monthly-transactions.jpg`: default Svelte monthly overview, 1440 × 1024 CSS/pixels.
- `04-detailed-review.jpg`: same Northline invoice selected, with transaction description, bank and account context, 1440 × 1024.
- `05-transactions-mobile.jpg`: 390 × 844; page controls fit and tables scroll within their regions.
- `06-design-comparison.jpg`: selected 1487 × 1058 Review reference alongside the current 1440 × 1024 review capture, each proportionally normalized to a common width of 1050 pixels. Density is 1. Differences in bank descriptions and queue count reflect the multi-bank fixture and requested hierarchy.
- `07-complete-month.jpg`: full 1440 × 1306 page, including both banks, unreconciled account and unmatched-document list.
- `08-raw-sidecar.jpg`: inspected source JSON.
- `09-expected-unmatched.jpg`: persisted expected-unreconciled decision and remove action, with transaction approvals retained.

All accepted captures were reopened from their saved paths. Full-view comparison
confirms the retained light theme and review composition. Text is readable in the
full-resolution review and overview captures, so extra crops were unnecessary.
The first overview capture scrolled past the header and was rejected; the final
capture starts at scroll position zero. The first responsive override affected
the temporary legacy tab; it was rejected and the 390px check repeated on the
actual dashboard tab after closing the legacy tab.

Findings and repairs:

1. [P1, fixed] The dashboard opened Documents and replaced monthly bank tables with a document-oriented review queue. Restored default Transactions, all-bank/account grouping and the original transaction fields. Evidence: legacy capture versus the new monthly overview.
2. [P1, fixed] Bank ownership, expected-unmatched decisions and raw metadata were absent from Svelte. Added explicit bank/account/source identities, existing-schema unmatched decisions with revision/token checks, and scoped metadata previews. Verified identical transaction descriptions at different banks remain distinct and approvals do not cross banks.
3. [P2, fixed] The new sidebar initially cropped PDF pages. Added a fit constraint while retaining zoom; the complete-month capture shows the entire invoice within its canvas. DOM checks confirm image bounds lie inside the preview canvas.
4. [P2, fixed] Document-led labels could hide the actual bank transaction and payment direction. Queue and inspector now use bank transaction descriptions, explicit bank/account context, and signed queue/table amounts.

Fidelity surfaces:

- Typography: retained Arial/Helvetica, navy labels, bold descriptions, readable 12–15px dense-table text and large review amount. Descriptions and filenames wrap.
- Spacing: flat dividers and restrained controls; a 65/35 monthly table/evidence split and the existing three-column detailed review. No KPI cards added.
- Color: existing white, navy, muted blue, teal action/selection, cool-gray canvas and amber attention labels. Theme stays light.
- Assets: existing Papertrail brand icon and Phosphor assets; actual PDF/XLSX sources rendered directly. No fabricated source-document artwork.
- Copy: transaction month/bank/account, reconciliation and approval remain distinct. Unreconciled statements explain their missing data. Expected documents have an explicit reversible decision.

Verification: month and bank filters, transaction search, incomplete/unmatched
filters, signed credits/debits, source and spreadsheet selection, raw JSON,
inline approval/removal, detailed approval/undo, Documents navigation, expected
unmatched decisions, original/download/ZIP API routes, mobile page overflow and
console logs. All decisions used synthetic fixtures. No real profile decisions
were changed. Eight frontend tests, Svelte check/build, Ruff checks, 192 Python
tests plus 24 subtests and all four golden reconciliation months pass.

Limits: the previous workflow approved existing pairings; it did not manually
assign files to transactions. The reconciliation engine is preserved. Statements
without reconciliation remain visible, but their transaction matches still
require the existing CLI run. Dense mobile tables intentionally scroll sideways.
Screenshots and interaction checks do not establish full accessibility compliance.
No unresolved P0/P1/P2 findings remain in the verified flows.


A final read-only check opened the updated live `puzzle` dashboard at
`http://127.0.0.1:64319/#transactions`. September correctly has no bank statements;
August loads its reconciled bank tables. Profile loading takes several seconds
because this profile contains many years of documents. The final live console
had no errors/warnings. No live decisions were made. `10-live-profile-shell.jpg`
records only the header and month/filter context outside Git; fixture captures
above remain the shareable design evidence.


**UI simplification — 15 October 2026**

final result: passed

The user requested less redundancy while preserving monthly, all-bank
reconciliation. The current-run functional audit and before/after report is
`/Users/tsilva/.codex/generated_images/01a13a1f-801d-71f3-a581-15ab95cb8819/simplify-ui/report.md`.
All published captures use synthetic data and live previews. The initial stale
preview-server error was rejected and recaptured against a new running fixture.

Source visual truth for this iteration: `02-before-overview.jpg` and
`03-before-review.jpg` in that evidence directory. Implementation:
`04-after-overview.jpg` and `05-after-review.jpg`. CSS viewport 1440 × 1024;
stored captures 1425 × 1013 pixels due to the browser capture runtime's uniform
scaling. Both sides use the same scale. The combined inputs
`08-overview-comparison.jpg` and `09-review-comparison.jpg` place each pair side by
side at a common 1000px width, preserving aspect ratio. These were reopened for
visual inspection. Mobile captures use a 390 × 844 CSS viewport.

Findings and repairs:

1. [P2, fixed] Monthly and per-bank count summaries repeated the same information. Keep one transaction/review summary and bank periods/counts. Remove per-bank linked/approved/zero-unmatched counters.
2. [P2, fixed] Row metadata and normal match classifications dominated the transaction list. Five columns replace six; row reference/rule/method/confidence/reason remain in Match details. Documents use compact counts with the full source picker preserved. Exceptional states remain visible, including incomplete states on approved rows.
3. [P2, fixed] Evidence titles appeared three times and repeated the selected transaction summary. Keep one selector/title and warnings; move original/download/raw JSON/fullscreen to Document options. Preserve zoom and page controls.
4. [P2, fixed] Focused review repeated amount and date above the comparison. Keep comparison values once, with signed bank amounts, and retain decision/undo/navigation actions. Technical details use a native disclosure.
5. [P2, fixed] A one-option global menu and duplicate ZIP action added indirection. Use direct refresh and a single month export action. Extracted document fields are available through a native disclosure.

Required fidelity surfaces: Arial/Helvetica and existing weights remain; dense
row typography is 13–14px and comparison amounts 17px. Flat separators and the
65/35 overview split remain; removed blocks reduce the sidebar's height. White,
navy, teal and cool-gray tokens are unchanged. Source PDFs/XLSX remain real
renders and the added refresh icon is from the existing Phosphor dependency.
Copy now distinguishes the primary list, review state, Match details and
Document options. The reduced display amount is an intentional hierarchy change
requested by this task, rather than drift from the earlier ImageGen mock.

Browser verification: monthly filters, selection, keyboard approve/undo, Match
details, extracted details, source menus, raw JSON and return, download href,
Escape/outside dismissal, multi-page evidence, multiple matched documents and
combined comparison amount, expected unmatched decisions, refresh, and mobile
page overflow. No console errors/warnings appeared. The live puzzle profile was
checked read-only, with 32 August transactions across two bank statements.
No real review decisions changed. Svelte check/build, eight frontend tests,
192 Python tests plus 24 subtests, and all four golden months pass. Dense mobile
tables continue to scroll horizontally within their own region. Full
accessibility compliance is not claimed. No actionable P0/P1/P2 issues remain.


The first mobile overview raster was rejected because capture happened during
viewport resize. A fresh tab was loaded at 390 × 844 CSS pixels and recaptured;
the accepted `06-after-mobile.jpg` is 375 × 812 pixels and was reopened for
inspection. Its visible table scrollbar confirms the intentional table-region
horizontal overflow; page width remains within the viewport. The accepted mobile
decision capture uses the same scale.


**Screenshot annotations — 15 October 2026**

final result: passed

Implemented the three requested annotations: valid dates use YYYY-MM-DD across
the dashboard, the Documents column displays numeric counts (including 0) with
accessible labels, and the desktop evidence panel fills the remaining viewport
height. The panel recalculates after resize, header/layout changes, and scrolling;
the existing stacked mobile preview remains. Original document content is unchanged.

Native in-app browser checks used synthetic monthly data at 1920 × 1200,
1440 × 900, and 390 × 844 CSS pixels. At the larger desktop size, the evidence
source was 872px high and the panel bottom met the 1200px viewport edge; at
1440 × 900 it grew from 572px to 805px as the page scrolled. Numeric counts,
ISO dates, source controls, mobile table overflow, and absence of console
errors/warnings were verified. Captures were reopened for visual inspection and
are outside Git at:
`/Users/tsilva/.codex/generated_images/01a13a1f-801d-71f3-a581-15ab95cb8819/annotation-fixes/`
(`desktop.png`, `desktop-scrolled.png`, `mobile.png`).

Svelte check reports zero warnings/errors; all eight frontend tests, the
production build, 192 Python tests with 24 subtests, and golden regression
months January–April pass. No real review decisions were changed.


**Consistent unmatched-document rows — 15 October 2026**

final result: passed

Documents without a transaction now reuse the transaction table, shared column
header/width definition, bold navy title buttons, row padding, separators, hover,
focus and selected states. Date, title, amount, document count and status align
with the bank tables; expected/undo remains independently clickable. The former
orphan grid styles were removed. All tables share a 700px minimum width so small
screens scroll inside the table region.

Native in-app browser verification used synthetic data at 1600 × 1100 and
390 × 844 CSS pixels. All three rendered tables had identical column widths
(157/392/137/137/157px), 14px Arial titles, weight 700, navy color and 14px/8px
cell padding. Clicking the orphan date selected its preview and highlighted
its row; Mark expected and Expected · undo persisted and restored fixture state.
Mobile selection worked without page overflow; all three table containers
retained the same horizontal scrolling behavior. No console errors/warnings.
Screenshots were saved and visually inspected outside Git at:
`/Users/tsilva/.codex/generated_images/01a13a1f-801d-71f3-a581-15ab95cb8819/matching-row-styles/`
(`desktop.png`, `mobile.png`). No real review decisions changed.

Svelte check/build, all eight frontend tests, 192 Python tests plus 24 subtests,
and the January–April golden regressions pass.
