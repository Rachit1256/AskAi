import ChartRenderer from "./ChartRenderer";
import ReportButton from "./ReportButton";

/**
 * Same panel, same sections, same order. What changed is the source: one profile
 * call fills Executive Summary, Dataset Overview, Columns, Statistics and
 * Observations, and one dashboard call fills Charts and Suggested
 * Visualizations. Two additions: the report controls sit in the header, and a
 * checksum section reports whether the parsed rows agree with the sheet's own
 * totals row.
 */
function formatStat(value) {

    if (value === null || value === undefined) return "—";
    if (typeof value !== "number") return String(value);

    return value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function InsightPanel({
    error,
    onRetry,
    summary,
    insights,
    businessInsights,
    executive,
    charts,
    visualizations,
    checksums,
    selectedFile,
    selectedTable,
    loading,
    messages
}) {

    // Questions that actually produced an answer are what the report is built from.
    const answeredQuestions = (messages || [])
        .map((message, index) =>
            message.role === "assistant" && message.source
                ? messages[index - 1]?.content
                : null
        )
        .filter(Boolean);

    if (error) {

        return (

            <aside className="insight">

                <div className="empty-dashboard">

                    <div className="dashboard-icon">
                        ⚠️
                    </div>

                    <h2>
                        Could not read this table
                    </h2>

                    <p>
                        {error.message}
                    </p>

                    {
                        error.remedy &&
                        <p>{error.remedy}</p>
                    }

                    <div className="panel-actions">
                        <button
                            className="panel-btn"
                            onClick={() => onRetry?.(error.table)}
                        >
                            Try again
                        </button>
                    </div>

                </div>

            </aside>

        );

    }

    if (!summary) {

        return (

            <aside className="insight">

                <div className="empty-dashboard">

                    <div className="dashboard-icon">
                        📊
                    </div>

                    <h2>
                        Analytics Dashboard
                    </h2>

                    <p>
                        {
                            loading
                            ?
                            "Reading the table..."
                            :
                            "Upload a workbook or select a sheet to see its statistics, charts and observations."
                        }
                    </p>

                </div>

            </aside>

        );

    }

    return (

        <aside className="insight">

            {/* ============================= */}
            {/* Dashboard Header */}
            {/* ============================= */}

            <div className="dashboard-header">

                <div>

                    <h2>
                        Analytics Dashboard
                    </h2>

                    <p>
                        {selectedFile}
                        {summary.sheet ? ` — ${summary.sheet}` : ""}
                    </p>

                </div>

                <div className="dataset-pill">
                    {summary.rows.toLocaleString("en-IN")} Rows
                </div>

            </div>

            <ReportButton
                tableId={selectedTable}
                questions={answeredQuestions}
            />

            {/* ============================= */}
            {/* Executive Summary */}
            {/* ============================= */}

            <section className="panel-card executive-card">

                <div className="section-title">
                    <h2>Executive Summary</h2>
                </div>

                {
                    !executive || executive.length === 0
                    ?
                    <p>
                        No summary available for this table.
                    </p>
                    :
                    <ul className="executive-list">
                        {
                            executive.map((item, index) => (
                                <li key={index}>
                                    {item}
                                </li>
                            ))
                        }
                    </ul>
                }

            </section>

            {/* ============================= */}
            {/* Dataset Overview */}
            {/* ============================= */}

            <section className="panel-card">

                <div className="section-title">
                    <h2>📁 Dataset Overview</h2>
                </div>

                <div className="dataset-grid">

                    <div className="dataset-item">
                        <span>Rows</span>
                        <strong>{summary.rows.toLocaleString("en-IN")}</strong>
                    </div>

                    <div className="dataset-item">
                        <span>Columns</span>
                        <strong>{(summary.columns || []).length}</strong>
                    </div>

                    <div className="dataset-item">
                        <span>Observation date</span>
                        <strong>{summary.uploaded_at}</strong>
                    </div>

                    <div className="dataset-item">
                        <span>Block type</span>
                        <strong>{summary.kind}</strong>
                    </div>

                </div>

            </section>

            {/* ============================= */}
            {/* Dataset Columns */}
            {/* ============================= */}

            <section className="panel-card">

                <div className="section-title">
                    <h2>🏷 Dataset Columns</h2>
                </div>

                <div className="tags">
                    {
                        (summary.columns || []).map(col => (
                            <span key={col} className="tag">
                                {col}
                            </span>
                        ))
                    }
                </div>

            </section>

            {/* ============================= */}
            {/* Statistics */}
            {/* ============================= */}

            <section className="panel-card">

                <div className="section-title">
                    <h2>📊 Statistical Overview</h2>
                </div>

                {
                    insights?.statistics &&
                    Object.keys(insights.statistics).length > 0
                    ?
                    <div className="stats-grid">
                        {
                            Object.entries(insights.statistics).map(([name, stat]) => (

                                <div key={name} className="stat-card">

                                    <h4>
                                        {name}
                                        {stat.unit ? ` (${stat.unit})` : ""}
                                    </h4>

                                    <div className="stat-row">
                                        <span>Average</span>
                                        <strong>{formatStat(stat.average)}</strong>
                                    </div>

                                    <div className="stat-row">
                                        <span>Median</span>
                                        <strong>{formatStat(stat.median)}</strong>
                                    </div>

                                    <div className="stat-row">
                                        <span>Minimum</span>
                                        <strong>{formatStat(stat.minimum)}</strong>
                                    </div>

                                    <div className="stat-row">
                                        <span>Maximum</span>
                                        <strong>{formatStat(stat.maximum)}</strong>
                                    </div>

                                    <div className="stat-row">
                                        <span>Sum</span>
                                        <strong>{formatStat(stat.sum)}</strong>
                                    </div>

                                    <div className="stat-row">
                                        <span>Observations</span>
                                        <strong>
                                            {stat.count}
                                            {
                                                stat.missing
                                                ?
                                                ` (${stat.missing} missing)`
                                                :
                                                ""
                                            }
                                        </strong>
                                    </div>

                                </div>

                            ))
                        }
                    </div>
                    :
                    <div className="empty-card">
                        No numeric columns in this table.
                    </div>
                }

            </section>

            {/* ============================= */}
            {/* Observations */}
            {/* ============================= */}

            <section className="panel-card">

                <div className="section-title">
                    <h2>💼 Observations</h2>
                </div>

                {
                    (businessInsights || []).length === 0
                    ?
                    <div className="empty-card">
                        Nothing notable to report.
                    </div>
                    :
                    <div className="timeline">
                        {
                            businessInsights.map((item, index) => (

                                <div key={index} className="timeline-item">

                                    <div className="timeline-dot">
                                        💡
                                    </div>

                                    <div className="timeline-content">
                                        {item}
                                    </div>

                                </div>

                            ))
                        }
                    </div>
                }

            </section>

            {/* ============================= */}
            {/* Data checks */}
            {/* ============================= */}

            {
                (checksums || []).length > 0 &&
                <section className="panel-card">

                    <div className="section-title">
                        <h2>✅ Data Checks</h2>
                    </div>

                    <div className="stats-grid">
                        {
                            checksums.map((check, index) => (

                                <div key={index} className="stat-card">

                                    <h4>{check.column_slug}</h4>

                                    <div className="stat-row">
                                        <span>Stated in sheet</span>
                                        <strong>{formatStat(check.stated_total)}</strong>
                                    </div>

                                    <div className="stat-row">
                                        <span>Parsed rows sum</span>
                                        <strong>{formatStat(check.parsed_total)}</strong>
                                    </div>

                                    <div className="stat-row">
                                        <span>Agrees</span>
                                        <strong>
                                            {check.agrees ? "Yes" : "No — review this block"}
                                        </strong>
                                    </div>

                                </div>

                            ))
                        }
                    </div>

                </section>
            }

            {/* ============================= */}
            {/* Interactive Charts */}
            {/* ============================= */}

            <section className="panel-card">

                <div className="section-title">
                    <h2>📈 Interactive Charts</h2>
                </div>

                {
                    (charts || []).length === 0
                    ?
                    <div className="empty-card">
                        No chart data available.
                    </div>
                    :
                    <div className="chart-grid">
                        {
                            charts.map((chart, index) => (

                                <div key={index} className="chart-card">

                                    <ChartRenderer chart={chart} />

                                </div>

                            ))
                        }
                    </div>
                }

            </section>

            {/* ============================= */}
            {/* Suggested Visualizations */}
            {/* ============================= */}

            <section className="panel-card">

                <div className="section-title">
                    <h2>🎨 Suggested Visualizations</h2>
                </div>

                {
                    (visualizations || []).length === 0
                    ?
                    <div className="empty-card">
                        No visualization recommendations.
                    </div>
                    :
                    <div className="visual-grid">
                        {
                            visualizations.map((chart, index) => (

                                <div key={index} className="visual-card">

                                    <div className="visual-icon">
                                        {
                                            chart.type === "bar"
                                            ? "📊"
                                            : chart.type === "line"
                                            ? "📈"
                                            : chart.type === "pie"
                                            ? "🥧"
                                            : chart.type === "scatter"
                                            ? "🔵"
                                            : "📉"
                                        }
                                    </div>

                                    <div className="visual-info">

                                        <strong>
                                            {chart.type.toUpperCase()}
                                        </strong>

                                        <p>
                                            {chart.title}
                                        </p>

                                        {
                                            chart.reason &&
                                            <p style={{ opacity: .75, fontSize: 12 }}>
                                                {chart.reason}
                                            </p>
                                        }

                                    </div>

                                </div>

                            ))
                        }
                    </div>
                }

            </section>

        </aside>

    );

}

export default InsightPanel;
