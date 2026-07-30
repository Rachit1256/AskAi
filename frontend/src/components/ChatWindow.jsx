import { useEffect, useRef, useState } from "react";

import ResponseCard from "./ResponseCard";
import ChatInput from "./ChatInput";

import { endpoints, readError } from "../services/api";

/**
 * Same chat surface. The message shape gained a few fields because the backend
 * now returns structured answers instead of prose plus a PNG:
 *
 *   table        the result rows, rendered as a table rather than described
 *   notes        assumptions the resolver made, and the conventions applied
 *   source       provenance -- file, sheet, row count, observation date
 *   sql          the query that produced the figure
 *   candidates   shown when a question is ambiguous, so the user picks
 */
function ChatWindow({ messages, setMessages, suggestions, hasData, fatal }) {

    const messagesEndRef = useRef(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages]);

    /**
     * Shared by the input and the suggestion chips. `tableId` is passed back
     * after an ambiguous question so the same wording resolves against the table
     * the user chose.
     */
    async function sendQuestion(question, tableId) {

        const asked = (question ?? "").trim();

        if (!asked || loading) return;

        setMessages(prev => [
            ...prev,
            { role: "user", content: asked }
        ]);

        setLoading(true);

        try {
            const { data } = await endpoints.ask(asked, tableId);
            const answer = data.answer;

            setMessages(prev => [
                ...prev,
                {
                    role: "assistant",
                    content: answer.headline,
                    table: answer.rows?.length > 1
                        ? { columns: answer.columns, rows: answer.rows }
                        : null,
                    notes: [...(answer.assumptions || []), ...(answer.notes || [])],
                    source: answer.provenance,
                    sql: answer.sql,
                    departure: answer.departure,
                    suggestions
                }
            ]);
        }
        catch (error) {
            const problem = readError(error);

            setMessages(prev => [
                ...prev,
                {
                    role: "assistant",
                    content: problem.message,
                    warning: problem.remedy,
                    // A 409 carries the referents it could not choose between.
                    candidates: problem.candidates.map(candidate => ({
                        label: candidate.label,
                        tableId: candidate.table_id
                    })),
                    question: asked,
                    suggestions: problem.code === "unanswerable" ? suggestions : []
                }
            ]);
        }

        setLoading(false);
    }

    return (

        <main className="chat">

            <div className="chat-header">

                <div>

                    <p>
                        Ask questions about the ingested workbooks
                    </p>

                </div>

                <div className="chat-count">
                    {messages.length} Messages
                </div>

            </div>

            <div className="messages">

                {
                    fatal &&
                    <div className="ai-message">
                        <div className="bubble">
                            <p>{fatal.message}</p>
                            {
                                fatal.remedy &&
                                <div className="message-warning">{fatal.remedy}</div>
                            }
                        </div>
                    </div>
                }

                {
                    messages.length === 0
                    ?
                    <div className="empty-chat">

                        <div className="empty-icon">
                            📊
                        </div>

                        <h2>
                            Nothing asked yet
                        </h2>

                        <p>
                            Upload a workbook, then ask about it. Every answer shows
                            its source and the query that produced it.
                        </p>

                    </div>
                    :
                    <ResponseCard
                        messages={messages}
                        onAsk={sendQuestion}
                    />
                }

                <div ref={messagesEndRef}></div>

            </div>

            <ChatInput
                onAsk={sendQuestion}
                loading={loading}
                disabled={!hasData}
            />

        </main>

    );

}

export default ChatWindow;
