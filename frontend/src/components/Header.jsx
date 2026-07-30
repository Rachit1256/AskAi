import { useEffect, useState } from "react";

/**
 * Unchanged except for the status readout, which now reports something real:
 * how many rows are loaded and that the query path makes no model calls.
 */
function Header({ health }) {

    const [time, setTime] = useState("");

    useEffect(() => {

        const update = () => {

            const now = new Date();

            setTime(
                now.toLocaleString("en-IN", {
                    day: "2-digit",
                    month: "short",
                    year: "numeric",
                    hour: "2-digit",
                    minute: "2-digit"
                })
            );

        };

        update();

        const timer = setInterval(update, 30000);

        return () => clearInterval(timer);

    }, []);

    const online = Boolean(health);

    return (

        <header className="header">

            <div className="header-left">

                <div className="logo-circle">

                    📊

                </div>

                <div>

                    <h1>AskAI Analytics</h1>

                </div>

            </div>

            <div className="header-right">

                <div className="status">

                    <span
                        className="status-dot"
                        style={online ? undefined : { background: "#dc2626" }}
                    ></span>

                    {
                        online
                        ?
                        `${health.total_rows.toLocaleString("en-IN")} rows • ${health.engine}`
                        :
                        "Backend offline"
                    }

                </div>

                <div className="current-time">

                    {time}

                </div>

            </div>

        </header>

    );

}

export default Header;
