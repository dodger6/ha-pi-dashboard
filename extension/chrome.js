const HIDE_CHROME = true; // ha-dash-hide-chrome
// Best-effort hide of the Home Assistant header and sidebar. HA draws those
// inside shadow roots, so a page stylesheet cannot reach them. ?kiosk on the
// dashboard URL is the reliable switch when the kiosk-mode integration (or a
// compatible frontend) is installed; this script covers the cases it can see.
(function () {
  if (!HIDE_CHROME) return;
  var host = location.hostname;
  if (host === "127.0.0.1" || host === "localhost") return;

  var CSS = [
    "ha-sidebar, ha-menu-button, ha-header-bar, app-header, app-toolbar, .header {",
    "  display: none !important;",
    "}",
    ":host {",
    "  --header-height: 0px !important;",
    "  --mdc-drawer-width: 0px !important;",
    "}"
  ].join("\n");

  function paint(el) {
    if (!el || el.nodeType !== 1) return;
    var tag = el.localName;
    if (tag === "ha-sidebar" || tag === "ha-menu-button" || tag === "ha-header-bar" || tag === "app-header") {
      el.style.setProperty("display", "none", "important");
    }
    if (tag === "ha-drawer") {
      el.style.setProperty("--mdc-drawer-width", "0px");
      try { el.opened = false; } catch (e) {}
    }
    if (tag === "hui-root") {
      el.style.setProperty("--header-height", "0px");
    }
    var root = el.shadowRoot;
    if (root && !root.querySelector(":scope > style[data-ha-dash]")) {
      var style = document.createElement("style");
      style.dataset.haDash = "1";
      style.textContent = CSS;
      root.appendChild(style);
    }
  }

  function walk(node, depth) {
    if (!node || depth > 14) return;
    var kids = node.children || [];
    for (var i = 0; i < kids.length; i++) {
      paint(kids[i]);
      if (kids[i].shadowRoot) walk(kids[i].shadowRoot, depth + 1);
      walk(kids[i], depth + 1);
    }
  }

  function pass() {
    var top = document.querySelector("home-assistant");
    if (top) {
      paint(top);
      if (top.shadowRoot) walk(top.shadowRoot, 0);
    }
  }

  pass();
  document.addEventListener("DOMContentLoaded", pass);
  var n = 0;
  var timer = setInterval(function () {
    pass();
    n += 1;
    if (n > 30) clearInterval(timer);
  }, 2000);
  try {
    new MutationObserver(pass).observe(document.documentElement, { childList: true, subtree: true });
  } catch (e) {}
})();
