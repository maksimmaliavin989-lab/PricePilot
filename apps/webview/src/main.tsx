import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@fontsource/onest/cyrillic-400.css";
import "@fontsource/onest/cyrillic-500.css";
import "@fontsource/onest/cyrillic-600.css";
import "@fontsource/onest/cyrillic-700.css";
import "@fontsource/onest/latin-400.css";
import "@fontsource/onest/latin-500.css";
import "@fontsource/onest/latin-600.css";
import "@fontsource/onest/latin-700.css";

import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root element is missing");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
