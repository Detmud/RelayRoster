// Sets the colour scheme before the first render (no white flash in dark mode).
// Follows the system setting and reacts to changes.
(function () {
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function apply() {
    document.documentElement.setAttribute("data-bs-theme", mq && mq.matches ? "dark" : "light");
  }
  apply();
  if (mq && mq.addEventListener) mq.addEventListener("change", apply);
})();
