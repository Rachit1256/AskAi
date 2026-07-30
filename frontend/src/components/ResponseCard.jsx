import { useState } from "react";

import ResultTable from "./ResultTable";

/**
 * Same bubble layout. Charts are gone from the chat -- the backend no longer
 * renders images -- and in their place the answer carries its result table, the
 * assumptions behind it, its provenance, and the query on request.
 */
function Bubble({ message, onAsk }) {

    const [showSql, setShowSql] = useState(false);

    return (

        <div className="bubble">

            {
                message.content &&
                <p>{message.content}</p>
            }

            {
                message.departure &&
                <div className="message-notes">
                    Departure {message.departure.departure_pct > 0 ? "+" : ""}
                    {message.departure.departure_pct}% from the{" "}
                    {message.departure.period} normal — {message.departure.category}.
                </div>
            }

            {
                message.table &&
                <ResultTable
                    columns={message.table.columns}
                    rows={message.table.rows}
                />
            }

            {
                message.notes?.length > 0 &&
                <div className="message-notes">
                    {message.notes.join(" ")}
                </div>
            }

            {
                message.warning &&
                <div className="message-warning">
                    <strong>What to do</strong>
                    {message.warning}
                </div>
            }

            {
                message.candidates?.length > 0 &&
                <div className="message-suggestions">
                    {
                        message.candidates.map((candidate, index) => (
                            <button
                                key={index}
                                className="suggestion-chip"
                                onClick={() => onAsk?.(message.question, candidate.tableId)}
                            >
                                {candidate.label}
                            </button>
                        ))
                    }
                </div>
            }

            {
                message.source &&
                <div className="message-source">
                    {message.source}
                </div>
            }

            {
                message.sql &&
                <>
                    <button
                        className="sql-toggle"
                        aria-expanded={showSql}
                        onClick={() => setShowSql(open => !open)}
                    >
                        {showSql ? "Hide query" : "Show query"}
                    </button>

                    {
                        showSql &&
                        <pre className="sql-block">{message.sql}</pre>
                    }
                </>
            }

            {
                message.role === "assistant" &&
                message.suggestions?.length > 0 &&
                <div className="message-suggestions">
                    {
                        message.suggestions.slice(0, 5).map((item, index) => (
                            <button
                                key={index}
                                className="suggestion-chip"
                                onClick={() => onAsk?.(item)}
                            >
                                {item}
                            </button>
                        ))
                    }
                </div>
            }

        </div>

    );

}

function ResponseCard({ messages, onAsk }) {

    return (

        <>
            {
                messages.map((message, index) => (

                    <div
                        key={index}
                        className={
                            message.role === "user"
                            ?
                            "user-message"
                            :
                            "ai-message"
                        }
                    >

                        <Bubble message={message} onAsk={onAsk} />

                    </div>

                ))
            }
        </>

    );

}

export default ResponseCard;
