# Frontend

React + Vite. Same three-column layout as before — sidebar, chat, analytics
panel — rewired to the new backend.

```bash
npm install
cp .env.example .env
npm run dev            # http://localhost:5173, proxying /api to the backend
```

## What stayed the same

`src/styles/Dashboard.css` is your original stylesheet, unmodified, with a new
section appended at the end. The `.dashboard` grid, `.sidebar`, `.chat`,
`.insight`, `.panel-card`, `.file-card`, `.bubble` and every other class keep
their existing rules, so the layout renders as it did.

Component names and DOM structure are unchanged too: `Dashboard` composes
`Header`, `Sidebar`, `ChatWindow` and `InsightPanel`; `ChatWindow` composes
`ResponseCard` and `ChatInput`; `InsightPanel` composes `ChartRenderer`.

## What changed, and why

**26 classes your JSX used were never defined in the CSS** — `stats-grid`,
`stat-card`, `stat-row`, `timeline`, `timeline-dot`, `chart-grid`, `chart-card`,
`visual-grid`, `dataset-item`, `dataset-pill`, `executive-list`, `section-title`,
`empty-card`, `empty-dashboard`, `chat-count` and others. That is why the right
panel rendered largely unstyled. They are now defined at the bottom of
`Dashboard.css`, in the same visual language as the cards above them: white
surfaces, 18px radius, `#f8fafc` insets, `#e5e7eb` hairlines, `#2563eb` accent.

**Endpoints.** `/files` → `/catalog/tables`, `/upload` → `/ingest`,
`/chat` → `/query/ask`. The six per-file calls (`/insights`,
`/business-insights`, `/kpis`, `/analytics`, `/visualizations`, `/executive`) are
replaced by two: `/catalog/tables/{id}/profile` and `/dashboard`.

**Charts.** No more `<img src={chartUrl(...)}>` pointing at server-rendered PNGs.
`toRendererChart()` in `services/api.js` adapts the backend's chart
specification into the `{ type, title, data, category, value }` shape
`ChartRenderer` already took, so the chart component's contract did not change.
The pie branch is gone (the backend does not propose pies) and a `kpi` branch was
added for single-figure cards.

**Errors.** `readError()` normalises everything axios throws into
`{ code, message, remedy, candidates }`. The backend returns that envelope for
every failure — typed errors, validation failures, and unhandled exceptions
alike — so no caller has to dig through `err.response.data.detail`, and a
remedy is always shown rather than a bare "Upload failed".

**Sidebar.** A workbook can hold several sub-tables, so an open file lists its
sheets and you pick one. "Refresh" moved next to the date field and its old slot
is now **Check layout**, which calls the dry-run endpoint: it reports the blocks
detected and the confidence for each without writing a row. Worth running once on
any new file format.

**Chat answers.** The message shape gained `table`, `notes`, `source`, `sql`,
`departure` and `candidates`. Result rows render as a table instead of being
described in prose; `Show query` reveals the SQL that produced the figure; and an
ambiguous question renders its candidates as chips, so picking one re-asks the
same question against the table you chose rather than the backend guessing.

**Reports.** `ReportButton` now sits in the analytics panel header and builds the
report from the questions actually answered in the session, so every figure in it
was computed. View opens it, Download saves it.

## Removed

`Layout.jsx`, `FileList.jsx`, `AnalyticsCards.jsx`, `AnalyticsDashboard.jsx` and
`UploadBox.jsx` were not imported by anything. Dependencies that nothing imported
are gone too: MUI, emotion, framer-motion, react-router, react-markdown,
react-syntax-highlighter, react-dropzone, react-icons, react-resizable-panels —
and `"package.json": "^2.0.1"`, which was in your dependency list by accident.

What remains: react, react-dom, axios, recharts, `@fontsource/inter`.
