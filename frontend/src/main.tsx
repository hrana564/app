import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { getToken, goToLogin, rememberReturnTo } from "./dataProvider";

/**
 * No session, so the portal has nothing to show yet: put the app on the login
 * route before the first render.
 *
 * This is the same rule the entity scope applies when the session goes away
 * while the app is open, and it is applied here as well because it has to
 * hold before the first render - by then react-admin's protected branch is
 * already mounting, and it bounces an anonymous visitor off the app in a way
 * that loops. See `goToLogin` for the whole of it.
 */
if (!getToken()) {
  rememberReturnTo(window.location.hash);
  goToLogin();
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
