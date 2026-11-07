const IDLE_MS = 3000; // ha-dash-idle-ms
// Show the pointer on movement and hide it after IDLE_MS of stillness.
// Touch hides it immediately so a tap does not leave a cursor on the panel.
(function () {
  if (!IDLE_MS) return;
  var root = document.documentElement;
  var timer = null;
  function hide() {
    root.style.setProperty("cursor", "none", "important");
  }
  function arm() {
    clearTimeout(timer);
    timer = setTimeout(hide, IDLE_MS);
  }
  function show() {
    root.style.removeProperty("cursor");
    arm();
  }
  document.addEventListener("mousemove", show, true);
  document.addEventListener("mousedown", show, true);
  document.addEventListener("keydown", show, true);
  document.addEventListener("touchstart", hide, true);
  arm();
})();
