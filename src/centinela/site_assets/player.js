// Centinela demo: runs the incident simulator and switches language.
// No network access, no cookies, no third parties: it only shows and hides frames that are
// already in the page. Without this file the page still shows every step, one under the other.
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.add("js");

  // ---- language: remembered per browser, otherwise the browser's own language
  function setLang(lang) {
    root.setAttribute("data-lang", lang);
    root.setAttribute("lang", lang);
    document.querySelectorAll("[data-set-lang]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-set-lang") === lang));
    });
    try { localStorage.setItem("centinela-lang", lang); } catch (e) { /* private mode */ }
  }
  var saved = null;
  try { saved = localStorage.getItem("centinela-lang"); } catch (e) { saved = null; }
  var browser = (navigator.language || "en").toLowerCase().indexOf("es") === 0 ? "es" : "en";
  setLang(saved === "es" || saved === "en" ? saved : browser);
  document.querySelectorAll("[data-set-lang]").forEach(function (b) {
    b.addEventListener("click", function () { setLang(b.getAttribute("data-set-lang")); });
  });

  // ---- the simulator: each scenario is a small graph of frames
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var STEP_MS = 6000;

  document.querySelectorAll(".player").forEach(function (player) {
    var tabs = Array.prototype.slice.call(player.querySelectorAll('[role="tab"]'));
    var scenes = Array.prototype.slice.call(player.querySelectorAll(".scenario"));
    var active = 0;
    var path = [];   // ids of the frames visited in the active scenario, in order
    var timer = null;

    function scene() { return scenes[active]; }
    function frame(id) { return document.getElementById(id); }
    function here() { return frame(path[path.length - 1]); }

    function render(focus) {
      var f = here();
      scene().querySelectorAll(".frame").forEach(function (x) { x.classList.toggle("on", x === f); });
      var depth = Number(f.getAttribute("data-depth"));
      scene().querySelectorAll(".dot").forEach(function (d) {
        var k = Number(d.getAttribute("data-depth"));
        d.classList.toggle("done", k < depth);
        d.classList.toggle("now", k === depth);
      });
      var waiting = f.querySelector(".choices") !== null;
      var last = !waiting && !f.getAttribute("data-next");
      scene().classList.toggle("waiting", waiting);
      scene().classList.toggle("finished", last);
      scene().querySelector(".prev").disabled = path.length === 1;
      scene().querySelector(".next").disabled = waiting || last;
      if (waiting || last) stop();
      if (focus) {
        var target = f.querySelector(".choices .choice") || f.querySelector("h3");
        if (target === f.querySelector("h3")) target.setAttribute("tabindex", "-1");
        target.focus({ preventScroll: true });
        var top = scene().getBoundingClientRect().top;
        if (top < 0) scene().scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
      }
    }

    function go(id, focus) { if (frame(id)) { path.push(id); render(focus); } }
    function next(focus) {
      var to = here().getAttribute("data-next");
      if (to) go(to, focus);
    }
    function back() { if (path.length > 1) { stop(); path.pop(); render(true); } }
    function restart() { stop(); path = [scene().querySelector(".frame").id]; render(true); }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      scenes.forEach(function (s) { s.classList.remove("playing"); });
    }
    function play() {
      stop();
      scene().classList.add("playing");
      timer = setInterval(function () { next(false); }, STEP_MS);
    }

    function select(n, focusTab) {
      stop();
      active = n;
      tabs.forEach(function (t, k) {
        t.setAttribute("aria-selected", String(k === n));
        t.tabIndex = k === n ? 0 : -1;
      });
      scenes.forEach(function (s, k) { s.hidden = k !== n; });
      path = [scene().querySelector(".frame").id];
      render(false);
      if (focusTab) tabs[n].focus();
    }

    tabs.forEach(function (tab, n) {
      tab.addEventListener("click", function () { select(n, false); });
      tab.addEventListener("keydown", function (e) {
        var to = e.key === "ArrowRight" ? n + 1 : e.key === "ArrowLeft" ? n - 1 : null;
        if (to === null) return;
        select((to + tabs.length) % tabs.length, true);
      });
    });
    scenes.forEach(function (s) {
      s.querySelector(".prev").addEventListener("click", back);
      s.querySelector(".next").addEventListener("click", function () { stop(); next(true); });
      s.querySelector(".again").addEventListener("click", restart);
      s.querySelector(".play").addEventListener("click", function () { if (timer) stop(); else play(); });
      s.addEventListener("click", function (e) {
        var b = e.target.closest ? e.target.closest(".choice") : null;
        if (b && s.contains(b)) { stop(); go(b.getAttribute("data-to"), true); }
      });
    });

    select(0, false);
    // start once when the simulator comes into view, unless the visitor prefers no motion;
    // it pauses by itself whenever the visitor has to decide
    if (!reduce && "IntersectionObserver" in window) {
      var seen = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) { play(); seen.disconnect(); }
      }, { threshold: 0.4 });
      seen.observe(player);
    }
  });
})();
