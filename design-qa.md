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
