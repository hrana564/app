import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { getToken, goToLogin, rememberReturnTo } from "./dataProvider";
import "./print.css";

if (!getToken()) {
  rememberReturnTo(window.location.hash);
  goToLogin();
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
