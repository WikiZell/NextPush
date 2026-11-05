"use strict";
// Applies the saved light/dark choice before the page paints (no flash). Kept as its own file: the page allows no inline scripts.
(function () {
  try {
    const saved = localStorage.getItem("nextpush-theme");
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch (error) { /* storage blocked: follow the system setting */ }
})();
