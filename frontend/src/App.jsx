import { useState } from "react";
import Dashboard from "./Dashboard";
import ChatWidget from "./ChatWidget";

export default function App() {
  // bumped every time the chat answers a question, so the dashboard
  // refetches — asking "how much has Priya billed today" and seeing the
  // leaderboard reflect it is part of the demo.
  const [refreshSignal, setRefreshSignal] = useState(0);

  return (
    <>
      <Dashboard refreshSignal={refreshSignal} />
      <ChatWidget onAnswered={() => setRefreshSignal((n) => n + 1)} />
    </>
  );
}
