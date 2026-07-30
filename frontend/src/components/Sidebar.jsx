import { useRef, useState } from "react";

import { endpoints, readError } from "../services/api";

/**
 * Same sidebar: upload card, refresh and clear, then the file list.
 *
 * Two additions, both driven by the backend. A workbook can now contain several
 * sub-tables, so an open file lists its sheets and you pick one. And "Check
 * layout" runs the dry-run endpoint, which reports the blocks it found and how
 * confident it is without writing a single row -- worth doing once on a new file
 * format before trusting the numbers.
 */
function Sidebar({
    files,
    selectedFile,
    selectedTable,
    onRefresh,
    onUploaded,
    onOpenTable,
    onClear
}) {

    const inputRef = useRef(null);
    const mode = useRef({ dryRun: false });

    const [busy, setBusy] = useState(false);
    const [status, setStatus] = useState(null);
    const [asOf, setAsOf] = useState(
        () => new Date().toISOString().slice(0, 10)
    );

    async function handleFile(event) {

        const file = event.target.files[0];
        event.target.value = "";

        if (!file) return;

        const dryRun = mode.current.dryRun;

        setBusy(true);
        setStatus(null);

        try {
            const { data } = await endpoints.ingest(file, { asOf, dryRun });

            if (dryRun) {
                const blocks = data.sheets.flatMap(sheet =>
                    sheet.blocks.map(block => ({
                        sheet: sheet.sheet,
                        kind: block.kind,
                        row: block.header_row,
                        confidence: block.confidence
                    }))
                );

                setStatus({
                    ok: true,
                    title: `${blocks.length} block(s) detected — nothing written`,
                    lines: blocks.map(block =>
                        `${block.sheet}: ${block.kind} at row ${block.row} ` +
                        `(${Math.round(block.confidence * 100)}% confidence)`
                    )
                });
            }
            else {
                const warnings = [
                    ...data.warnings,
                    ...data.tables.flatMap(table => table.warnings)
                ];

                setStatus({
                    ok: warnings.length === 0,
                    title: data.already_ingested
                        ? "Already ingested — no rows written"
                        : `${data.total_rows.toLocaleString("en-IN")} rows across ` +
                          `${data.tables.length} table(s)`,
                    lines: warnings
                });

                onUploaded(data);
            }
        }
        catch (error) {
            const problem = readError(error);

            setStatus({
                ok: false,
                title: problem.message,
                lines: problem.remedy ? [problem.remedy] : []
            });
        }

        setBusy(false);
    }

    function pick(dryRun) {
        mode.current = { dryRun };
        inputRef.current?.click();
    }

    return (

        <div className="sidebar">

            <div className="sidebar-header">

                <h2>📁 My Files</h2>

                <p>
                    Upload and manage workbooks
                </p>

            </div>

            <label className="upload-card">

                <div className="upload-icon">

                    ⬆️

                </div>

                <strong>
                    {
                        busy
                        ?
                        "Reading workbook..."
                        :
                        "Upload Workbook"
                    }
                </strong>

                <span>
                    Excel .xlsx • .xlsm
                </span>

                <input
                    ref={inputRef}
                    type="file"
                    accept=".xlsx,.xlsm,.xltx"
                    onChange={handleFile}
                    hidden
                />

            </label>

            <div className="sidebar-actions">

                <button
                    className="refresh-btn"
                    onClick={() => pick(true)}
                    disabled={busy}
                >
                    Check layout
                </button>

                <button
                    className="clear-btn"
                    onClick={onClear}
                    disabled={busy}
                >
                    Clear
                </button>

            </div>

            {
                status &&
                <div className={status.ok ? "upload-status ok" : "upload-status bad"}>

                    <strong>{status.title}</strong>

                    {
                        status.lines.slice(0, 6).map((line, index) => (
                            <div key={index}>{line}</div>
                        ))
                    }

                </div>
            }

            <div className="file-section">

                <h3>
                    Ingested Files
                </h3>

                <div className="sidebar-actions" style={{ padding: 0, marginBottom: 12 }}>

                    <input
                        type="date"
                        value={asOf}
                        onChange={(event) => setAsOf(event.target.value)}
                        title="Observation date recorded against the next upload"
                        style={{
                            flex: 1,
                            padding: "8px 10px",
                            border: "1px solid #e5e7eb",
                            borderRadius: 10,
                            fontSize: 12
                        }}
                    />

                    <button className="refresh-btn" onClick={onRefresh} disabled={busy}>
                        Refresh
                    </button>

                </div>

                <div className="file-list">

                    {
                        files.length === 0
                        ?
                        <div className="empty-files">
                            No workbooks ingested.
                        </div>
                        :
                        files.map(file => (

                            <div
                                key={file.filename}
                                className={
                                    selectedFile === file.filename
                                    ?
                                    "file-card active"
                                    :
                                    "file-card"
                                }
                                onClick={() => {
                                    // Always re-open. Guarding on "is it already
                                    // selected" meant that if the first load
                                    // failed, clicking the card did nothing at
                                    // all and there was no way back.
                                    const largest = [...file.tables]
                                        .sort((a, b) => b.rows - a.rows)[0];
                                    onOpenTable(largest);
                                }}
                            >

                                <div className="file-name">
                                    📄 {file.filename}
                                </div>

                                <div className="file-meta">
                                    {file.rows.toLocaleString("en-IN")} rows
                                    {" • "}
                                    {file.tables.length} table(s)
                                    {" • "}
                                    as of {file.as_of_date}
                                </div>

                                {
                                    selectedFile === file.filename &&
                                    file.tables.length > 1 &&
                                    <div className="file-sheets">
                                        {
                                            file.tables.map(table => (
                                                <button
                                                    key={table.table_id}
                                                    className={
                                                        selectedTable === table.table_id
                                                        ?
                                                        "file-sheet active"
                                                        :
                                                        "file-sheet"
                                                    }
                                                    onClick={(event) => {
                                                        event.stopPropagation();
                                                        onOpenTable(table);
                                                    }}
                                                >
                                                    <span>{table.sheet}</span>
                                                    <span>{table.rows}</span>
                                                </button>
                                            ))
                                        }
                                    </div>
                                }

                            </div>

                        ))
                    }

                </div>

            </div>

        </div>

    );

}

export default Sidebar;
