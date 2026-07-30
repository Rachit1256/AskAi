import axios from "axios";

/**
 * Backend client.
 *
 * Two things changed from the previous version. Charts no longer arrive as PNG
 * filenames under a static mount, so `chartUrl` is gone. And the backend now
 * returns a typed error envelope -- { code, message, remedy, context } -- with a
 * real HTTP status, so `readError` gives every caller the same shape instead of
 * each one digging through `err.response.data.detail`.
 */

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

const client = axios.create({ baseURL: API_BASE, timeout: 120000 });

/** Normalise anything axios throws into { code, message, remedy, candidates }. */
export function readError(error) {
    const payload = error?.response?.data;

    if (payload && typeof payload === "object" && payload.code) {
        return {
            code: payload.code,
            message: payload.message || "Request failed.",
            remedy: payload.remedy || null,
            candidates: payload.context?.candidates || [],
            problems: payload.context?.problems || [],
        };
    }

    if (error?.response) {
        return {
            code: "request_failed",
            message: payload?.detail || `Request failed (${error.response.status}).`,
            remedy: null,
            candidates: [],
            problems: [],
        };
    }

    return {
        code: "unreachable",
        message: "The backend is not responding.",
        remedy: "Check that the service is running, then try again.",
        candidates: [],
        problems: [],
    };
}

/**
 * Turns the backend's Vega-Lite specification into the { type, title, data,
 * category, value } shape ChartRenderer already understands, so the chart
 * component did not have to change.
 */
export function toRendererChart(chart) {
    if (!chart) return null;

    if (chart.kind === "kpi") {
        const spec = chart.vega_lite || {};
        return {
            type: "kpi",
            title: chart.title,
            caption: chart.caption,
            unit: spec.unit,
            stats: {
                sum: spec.value,
                average: spec.mean,
                minimum: spec.min,
                maximum: spec.max,
                count: spec.count,
            },
        };
    }

    return {
        type: chart.kind === "line" ? "line" : "bar",
        title: chart.title,
        caption: chart.caption,
        category: "category",
        value: "value",
        data: chart.vega_lite?.data?.values || [],
    };
}

export const endpoints = {
    health: () => client.get("/health"),
    tables: () => client.get("/catalog/tables"),
    datasets: () => client.get("/catalog/datasets"),
    profile: (tableId) => client.get(`/catalog/tables/${tableId}/profile`),
    preview: (tableId, limit = 50) =>
        client.get(`/catalog/tables/${tableId}/preview`, { params: { limit } }),
    dashboard: (params = {}) => client.get("/dashboard", { params }),
    suggestions: (tableId) =>
        client.get("/query/suggestions", { params: tableId ? { table_id: tableId } : {} }),
    ask: (question, tableId) =>
        client.post("/query/ask", { question, table_id: tableId || null }),
    resolve: (question) => client.post("/query/resolve", { question }),
    retire: (datasetId) => client.delete(`/catalog/datasets/${datasetId}`),
    clear: () => client.delete("/catalog"),

    ingest(file, { asOf, dryRun = false } = {}) {
        const form = new FormData();
        form.append("file", file);
        if (asOf) form.append("as_of", asOf);
        return client.post(dryRun ? "/ingest/analyse" : "/ingest", form);
    },

    report(body) {
        return client.post("/report", body, { responseType: "text" });
    },
};

export default client;
