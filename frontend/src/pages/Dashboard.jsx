import { useCallback, useEffect, useState } from "react";

import Header from "../components/Header";
import Sidebar from "../components/Sidebar";
import ChatWindow from "../components/ChatWindow";
import InsightPanel from "../components/InsightPanel";

import { endpoints, readError, toRendererChart } from "../services/api";

import "../styles/Dashboard.css";

/**
 * Same three-column layout as before. What changed is where the panels get
 * their data: one profile call and one dashboard call per selected table,
 * instead of six parallel requests per file.
 */
function Dashboard() {

    const [health, setHealth] = useState(null);

    const [files, setFiles] = useState([]);
    const [selectedFile, setSelectedFile] = useState(null);
    const [selectedTable, setSelectedTable] = useState(null);

    const [messages, setMessages] = useState([
        {
            role: "assistant",
            content: "Upload a workbook to begin. Questions are answered from the ingested data, with the source and the query shown alongside every figure."
        }
    ]);

    const [summary, setSummary] = useState(null);
    const [insights, setInsights] = useState(null);
    const [businessInsights, setBusinessInsights] = useState(null);
    const [executive, setExecutive] = useState(null);
    const [charts, setCharts] = useState(null);
    const [visualizations, setVisualizations] = useState(null);
    const [checksums, setChecksums] = useState(null);

    const [suggestions, setSuggestions] = useState([]);
    const [loadingPanel, setLoadingPanel] = useState(false);
    const [panelError, setPanelError] = useState(null);
    const [fatal, setFatal] = useState(null);

    function resetPanel() {
        setSummary(null);
        setInsights(null);
        setBusinessInsights(null);
        setExecutive(null);
        setCharts(null);
        setVisualizations(null);
        setChecksums(null);
    }

    /**
     * Group the catalog's tables by source workbook, which is what the sidebar
     * lists. Health and tables are fetched independently: a failing health check
     * used to blank the file list too, which left nothing on screen to click.
     */
    const loadFiles = useCallback(async () => {

        endpoints.health()
            .then(({ data }) => setHealth(data))
            .catch(() => setHealth(null));

        try {
            const { data: tables } = await endpoints.tables();

            setFatal(null);

            const grouped = new Map();

            tables.forEach(table => {
                const entry = grouped.get(table.filename) || {
                    filename: table.filename,
                    dataset_id: table.dataset_id,
                    as_of_date: table.as_of_date,
                    rows: 0,
                    tables: []
                };
                entry.rows += table.rows;
                entry.tables.push(table);
                grouped.set(table.filename, entry);
            });

            setFiles([...grouped.values()]);
            return [...grouped.values()];
        }
        catch (error) {
            setFatal(readError(error));
            return [];
        }
    }, []);

    useEffect(() => {
        loadFiles();
    }, [loadFiles]);

    /** One profile call and one dashboard call fill every panel on the right. */
    const openTable = useCallback(async (table) => {
        setSelectedFile(table.filename);
        setSelectedTable(table.table_id);
        setLoadingPanel(true);
        setPanelError(null);

        try {
            const [{ data: profile }, { data: board }, { data: hints }] = await Promise.all([
                endpoints.profile(table.table_id),
                endpoints.dashboard({ table_id: table.table_id }),
                endpoints.suggestions(table.table_id)
            ]);

            setSummary({
                rows: profile.rows,
                columns: profile.columns,
                uploaded_at: profile.as_of_date,
                sheet: profile.sheet,
                kind: profile.kind,
                context: profile.context
            });
            setInsights({ statistics: profile.statistics });
            setExecutive(profile.summary);
            setBusinessInsights(profile.observations);
            setChecksums(profile.checksums);
            setCharts(board.charts.map(toRendererChart).filter(Boolean));
            setVisualizations(board.suggested);
            setSuggestions(hints.suggestions || []);
        }
        catch (error) {
            // Kept local to the panel. A failed profile load should not wipe the
            // sidebar -- the file is ingested either way, and the user needs a
            // way to try again.
            setPanelError({ ...readError(error), table });
            resetPanel();
        }

        setLoadingPanel(false);
    }, []);

    /** After an ingest, select the largest new table so the panels are never empty. */
    const afterUpload = useCallback(async (report) => {
        const grouped = await loadFiles();
        const file = grouped.find(item => item.filename === report.filename);
        if (!file || file.tables.length === 0) return;

        const largest = [...file.tables].sort((a, b) => b.rows - a.rows)[0];
        openTable(largest);
    }, [loadFiles, openTable]);

    const clearAll = useCallback(async () => {
        try {
            await endpoints.clear();
        }
        catch (error) {
            setFatal(readError(error));
            return;
        }

        setFiles([]);
        setSelectedFile(null);
        setSelectedTable(null);
        setSuggestions([]);
        resetPanel();
    }, []);

    return (

        <div className="dashboard">

            {/* LEFT SIDEBAR */}

            <aside className="dashboard-sidebar">

                <Sidebar
                    files={files}
                    selectedFile={selectedFile}
                    selectedTable={selectedTable}
                    onRefresh={loadFiles}
                    onUploaded={afterUpload}
                    onOpenTable={openTable}
                    onClear={clearAll}
                />

            </aside>

            {/* CENTER */}

            <main className="dashboard-main">

                <Header health={health} />

                <ChatWindow
                    messages={messages}
                    setMessages={setMessages}
                    suggestions={suggestions}
                    hasData={files.length > 0}
                    fatal={fatal}
                />

            </main>

            {/* RIGHT */}

            <aside className="dashboard-right">

                <InsightPanel
                    error={panelError}
                    onRetry={openTable}
                    selectedFile={selectedFile}
                    selectedTable={selectedTable}
                    loading={loadingPanel}
                    summary={summary}
                    insights={insights}
                    businessInsights={businessInsights}
                    executive={executive}
                    charts={charts}
                    visualizations={visualizations}
                    checksums={checksums}
                    messages={messages}
                />

            </aside>

        </div>

    );

}

export default Dashboard;
