# Monthly reconciliation workflow

Transactions are the primary working surface. The dashboard applies the selected
light design to the existing reconciliation workflow; Documents is a supporting
browser and the detailed review screen is an inspection step.

| Step | Previous UI | Svelte UI | Verification |
| --- | --- | --- | --- |
| 1. Choose a month | Export Folder dropdown | Shared Month selector; default to the latest export month | October and September remain separate |
| 2. Scan all banks | One table per bank statement, account and period | Transactions opens by default; grouped tables for all bank accounts | Two banks, three accounts; same transaction description stays distinguishable |
| 3. Read transaction evidence | Row, date, description, signed amount, rule, status, confidence, files | Dates, descriptions, signed amounts, document counts and status in flat tables; rule/confidence/row reference under Match details; bank/account filter and search | Deposit/payment signs, rules, warnings and confidence retained |
| 4. Inspect source files | Click any linked filename, page through preview, inspect Raw JSON | Click a transaction or linked document; choose invoice or statement evidence; preview and Document options for original/download/Raw JSON/fullscreen | PDF, spreadsheet, original-file routes and sidecars work |
| 5. Reconcile pairings | Approve current pairing; remove approval; inspect changed evidence | Inline Approve/Approved; detailed Review transaction for compare, approve, reject, clear and undo | Decisions retain existing groundtruth schema and bank ownership |
| 6. Resolve unmatched documents | Unmatched Files list; mark expected; remove expected decision | Documents without a transaction list beneath monthly tables; Mark expected / Expected · undo | Expected-unreconciled decisions persist without changing transaction approvals |
| 7. Export month | Export ZIP | Visible Export month ZIP action for an available export month | Existing scoped ZIP endpoint verified |

Matched, incomplete and unmatched rows stay visible in the monthly overview.
Needs review is an explicit filter. Reconciliation status and manual approval are
separate: linked evidence does not imply a human approved it. Rule, method, confidence, row reference and reasoning remain available in the
Match details disclosure when inspecting a transaction.

Statements without reconciliation stay in their bank/account section with their
reported transaction count and a clear explanation. Their originals can still be
previewed. Run the existing CLI reconciliation workflow to generate matches; this
redesign does not change the reconciliation engine or add manual file assignment.
The previous review UI also approved existing pairings rather than editing them.

The light theme, navy text, teal selection/actions, flat dividers, existing brand
asset and Phosphor icons remain. Dense transaction tables can scroll horizontally
inside their own region on a phone; persistent page controls fit the screen.

Browser verification used fictional data and changed no real profile approvals.
The January–April golden reconciliation baselines were not modified. Screenshot
and visual comparison evidence is recorded in `design-qa.md` outside private data
paths in Git.
