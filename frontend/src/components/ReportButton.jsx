import { useState } from "react";

import { endpoints, readError } from "../services/api";

/**
 * Report generation.
 *
 * Previously this opened three GET URLs and hoped a file came back. Now the
 * report is built from the questions actually asked in this session, so the
 * document contains figures that were computed rather than described. "View"
 * opens it; "Download" saves it.
 */
function ReportButton({ tableId, questions }) {

    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);

    async function build(download) {

        setBusy(true);
        setError(null);

        try {
            const { data } = await endpoints.report({
                title: "SATMET data review",
                questions,
                include_dashboard: true,
                table_id: tableId || null,
                download
            });

            const blob = new Blob([data], { type: "text/html;charset=utf-8" });
            const url = URL.createObjectURL(blob);

            if (download) {
                const link = document.createElement("a");
                link.href = url;
                link.download = `satmet-report-${new Date().toISOString().slice(0, 10)}.html`;
                link.click();
            }
            else {
                window.open(url, "_blank", "noopener");
            }

            setTimeout(() => URL.revokeObjectURL(url), 60000);
        }
        catch (exception) {
            setError(readError(exception).message);
        }

        setBusy(false);
    }

    const ready = questions.length > 0;

    return (

        <div className="panel-actions">

            <button
                className="panel-btn"
                onClick={() => build(false)}
                disabled={busy || !ready}
                title={
                    ready
                    ?
                    `Build a report from ${questions.length} answered question(s)`
                    :
                    "Ask a question first — the report is built from answers"
                }
            >
                {busy ? "Building..." : "View report"}
            </button>

            <button
                className="panel-btn"
                onClick={() => build(true)}
                disabled={busy || !ready}
            >
                Download
            </button>

            {
                error &&
                <span className="file-warning">{error}</span>
            }

        </div>

    );

}

export default ReportButton;
