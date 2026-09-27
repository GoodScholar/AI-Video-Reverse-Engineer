/// <reference types="vite/client" />
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);
// Temporary design probe, reachable only in the development server.
if (import.meta.env.DEV && new URLSearchParams(location.search).get("prototype") === "studio-redesign") {
  void import("./WholeSiteStudioPrototype").then(({ WholeSiteStudioPrototype }) => {
    root.render(<StrictMode><WholeSiteStudioPrototype /></StrictMode>);
  });
} else if (import.meta.env.DEV && new URLSearchParams(location.search).get("prototype") === "content-studio") {
  void import("./ContentStudioPrototype").then(({ ContentStudioPrototype }) => {
    root.render(<StrictMode><ContentStudioPrototype /></StrictMode>);
  });
} else {
  root.render(<StrictMode><App /></StrictMode>);
}
