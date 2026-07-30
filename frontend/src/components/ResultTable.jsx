/**
 * Result rows. Numeric columns are right-aligned with tabular figures because in
 * meteorological tables the vertical alignment of digits is what makes a column
 * scannable.
 */
const MAX_ROWS = 40;

function isNumeric(rows, column) {

    const sample = rows.slice(0, 10).map(row => row[column]);

    const numeric = sample.filter(value =>
        typeof value === "number" ||
        (typeof value === "string" && /^-?[\d,]+(\.\d+)?/.test(value))
    );

    return numeric.length >= Math.max(1, sample.length - 1);
}

function ResultTable({ columns, rows }) {

    if (!columns?.length || !rows?.length) return null;

    const visible = rows.slice(0, MAX_ROWS);
    const numericColumns = new Set(columns.filter(column => isNumeric(rows, column)));

    return (

        <div className="result-table-wrap">

            <table className="result-table">

                <thead>
                    <tr>
                        {
                            columns.map(column => (
                                <th
                                    key={column}
                                    className={numericColumns.has(column) ? "num" : undefined}
                                >
                                    {column.replace(/_/g, " ")}
                                </th>
                            ))
                        }
                    </tr>
                </thead>

                <tbody>
                    {
                        visible.map((row, index) => (
                            <tr key={index}>
                                {
                                    columns.map(column => (
                                        <td
                                            key={column}
                                            className={
                                                numericColumns.has(column) ? "num" : undefined
                                            }
                                        >
                                            {
                                                row[column] === null ||
                                                row[column] === undefined ||
                                                row[column] === ""
                                                ?
                                                "—"
                                                :
                                                String(row[column])
                                            }
                                        </td>
                                    ))
                                }
                            </tr>
                        ))
                    }
                </tbody>

            </table>

            {
                rows.length > MAX_ROWS &&
                <p className="result-more">
                    Showing {MAX_ROWS} of {rows.length} rows. Narrow the question to see the rest.
                </p>
            }

        </div>

    );

}

export default ResultTable;
