import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { applyPrefs, deviceClass, readPrefs } from "./lib/displayPrefs";

try {
  applyPrefs(readPrefs(deviceClass()));
} catch {
}

const el = document.getElementById("root");
if (!el) throw new Error("no #root element");

createRoot(el).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
