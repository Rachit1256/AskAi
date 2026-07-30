import {
    ResponsiveContainer,
    BarChart,
    Bar,
    LineChart,
    Line,
    CartesianGrid,
    XAxis,
    YAxis,
    Tooltip
} from "recharts";

/**
 * Unchanged contract: this still takes { type, title, data, category, value }.
 * The pie branch is gone because the backend does not propose pies, and a "kpi"
 * branch is added for single-figure cards. Everything else is the same chart
 * component you had -- `toRendererChart` in services/api.js adapts the backend's
 * chart specification into this shape so nothing here had to change.
 */
const ACCENT = "#2563eb";

function formatNumber(value) {

    if (value === null || value === undefined) return "—";
    if (typeof value !== "number") return String(value);

    return value.toLocaleString("en-IN", { maximumFractionDigits: 1 });
}

function ChartRenderer({ chart }) {

    if (!chart) {
        return (
            <div className="chart-empty">
                No chart data available.
            </div>
        );
    }

    if (chart.type === "kpi") {

        const stats = chart.stats || {};

        return (
            <div className="chart-container">

                <div className="chart-header">
                    <h4>{chart.title}</h4>
                    <span>TOTAL</span>
                </div>

                <div className="kpi-grid">

                    <div className="kpi">
                        <span>Sum</span>
                        <strong>
                            {formatNumber(stats.sum)}
                            {chart.unit ? ` ${chart.unit}` : ""}
                        </strong>
                    </div>

                    <div className="kpi">
                        <span>Average</span>
                        <strong>{formatNumber(stats.average)}</strong>
                    </div>

                    <div className="kpi">
                        <span>Minimum</span>
                        <strong>{formatNumber(stats.minimum)}</strong>
                    </div>

                    <div className="kpi">
                        <span>Maximum</span>
                        <strong>{formatNumber(stats.maximum)}</strong>
                    </div>

                </div>

                {
                    chart.caption &&
                    <p className="result-more">{chart.caption}</p>
                }

            </div>
        );
    }

    if (!chart.data || chart.data.length === 0) {
        return (
            <div className="chart-empty">
                No chart data available.
            </div>
        );
    }

    const isLine = chart.type === "line";

    return (

        <div className="chart-container">

            <div className="chart-header">

                <h4>{chart.title}</h4>

                <span>
                    {(chart.type || "bar").toUpperCase()}
                </span>

            </div>

            <div className="chart-body">

                <ResponsiveContainer width="100%" height={230}>

                    {
                        isLine
                        ?
                        <LineChart data={chart.data}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} />
                            <XAxis
                                dataKey={chart.category}
                                tick={{ fontSize: 11 }}
                                interval="preserveStartEnd"
                            />
                            <YAxis
                                tick={{ fontSize: 11 }}
                                tickFormatter={formatNumber}
                                width={52}
                            />
                            <Tooltip formatter={formatNumber} />
                            <Line
                                type="monotone"
                                dataKey={chart.value}
                                stroke={ACCENT}
                                strokeWidth={2}
                                dot={{ r: 2 }}
                            />
                        </LineChart>
                        :
                        <BarChart data={chart.data}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} />
                            <XAxis
                                dataKey={chart.category}
                                tick={{ fontSize: 11 }}
                                interval="preserveStartEnd"
                            />
                            <YAxis
                                tick={{ fontSize: 11 }}
                                tickFormatter={formatNumber}
                                width={52}
                            />
                            <Tooltip formatter={formatNumber} />
                            <Bar
                                dataKey={chart.value}
                                fill={ACCENT}
                                radius={[6, 6, 0, 0]}
                                maxBarSize={34}
                            />
                        </BarChart>
                    }

                </ResponsiveContainer>

            </div>

            {
                chart.caption &&
                <p className="result-more">{chart.caption}</p>
            }

        </div>

    );

}

export default ChartRenderer;
