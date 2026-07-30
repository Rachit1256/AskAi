import { useState } from "react";

function ChatInput({ onAsk, loading, disabled }) {

    const [question, setQuestion] = useState("");

    function submit() {

        const trimmed = question.trim();

        if (!trimmed || loading || disabled) return;

        onAsk(trimmed);
        setQuestion("");
    }

    return (

        <div className="chat-input">

            <input
                value={question}
                disabled={disabled}
                placeholder={
                    disabled
                    ?
                    "Upload a workbook first"
                    :
                    "Ask about the data — e.g. monsoon rainfall by station"
                }
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                    if (e.key === "Enter") {
                        submit();
                    }
                }}
            />

            <button
                onClick={submit}
                disabled={loading || disabled}
            >
                {
                    loading
                    ?
                    "Working..."
                    :
                    "Ask"
                }
            </button>

        </div>

    );

}

export default ChatInput;
