import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./theme.css";
import "./app.css";
import { App } from "./App";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("找不到挂载节点 #root");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
