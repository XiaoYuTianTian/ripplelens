import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/base.css";
import App from "./App";
import { useAiReview } from "./ai/useAiReview";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App useAi={useAiReview} />
  </StrictMode>,
);
