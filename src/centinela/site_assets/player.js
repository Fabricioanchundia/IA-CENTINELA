// Centinela demo: shows one frame of the incident player at a time and switches language.
// No network access, no cookies, no third parties. Without this file the page still shows
// every step, one under the other.
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

  // ---- the player
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var STEP_MS = 6000;

  document.querySelectorAll(".player").forEach(function (player) {
    var tabs = Array.prototype.slice.call(player.querySelectorAll('[role="tab"]'));
    var scenes = Array.prototype.slice.call(player.querySelectorAll(".scenario"));
    var timer = null;
    var active = 0;

    function scene() { return scenes[active]; }
    function frames() { return scene().querySelectorAll(".frame"); }
    function current() {
      var f = frames();
      for (var i = 0; i < f.length; i++) if (f[i].classList.contains("on")) return i;
      return 0;
    }
    function playButton() { return scene().querySelector(".play"); }

    function show(i) {
      var f = frames();
      i = Math.max(0, Math.min(f.length - 1, i));
      for (var k = 0; k < f.length; k++) f[k].classList.toggle("on", k === i);
      var dots = scene().querySelectorAll(".dot");
      for (var d = 0; d < dots.length; d++) {
        dots[d].classList.toggle("done", d < i);
        dots[d].setAttribute("aria-current", d === i ? "step" : "false");
      }
      scene().querySelector(".prev").disabled = i === 0;
      scene().querySelector(".next").disabled = i === f.length - 1;
      if (i === f.length - 1) stop();
    }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      scenes.forEach(function (s) { s.classList.remove("playing"); });
    }
    function play() {
      stop();
      if (current() === frames().length - 1) show(0);
      scene().classList.add("playing");
      timer = setInterval(function () { show(current() + 1); }, STEP_MS);
    }

    function select(n) {
      stop();
      active = n;
      tabs.forEach(function (t, k) {
        t.setAttribute("aria-selected", String(k === n));
        t.tabIndex = k === n ? 0 : -1;
      });
      scenes.forEach(function (s, k) { s.hidden = k !== n; });
      show(0);
    }

    tabs.forEach(function (tab, n) {
      tab.addEventListener("click", function () { select(n); });
      tab.addEventListener("keydown", function (e) {
        var to = e.key === "ArrowRight" ? n + 1 : e.key === "ArrowLeft" ? n - 1 : null;
        if (to === null) return;
        to = (to + tabs.length) % tabs.length;
        select(to);
        tabs[to].focus();
      });
    });
    scenes.forEach(function (s) {
      s.querySelector(".prev").addEventListener("click", function () { stop(); show(current() - 1); });
      s.querySelector(".next").addEventListener("click", function () { stop(); show(current() + 1); });
      s.querySelector(".play").addEventListener("click", function () {
        if (timer) stop(); else play();
      });
      s.querySelectorAll(".dot").forEach(function (dot) {
        dot.addEventListener("click", function () { stop(); show(Number(dot.getAttribute("data-go"))); });
      });
    });
    player.addEventListener("keydown", function (e) {
      if (e.target && e.target.getAttribute && e.target.getAttribute("role") === "tab") return;
      if (e.key === "ArrowRight") { stop(); show(current() + 1); }
      if (e.key === "ArrowLeft") { stop(); show(current() - 1); }
    });

    select(0);
    // start once when the player comes into view, unless the visitor prefers no motion
    if (!reduce && "IntersectionObserver" in window) {
      var seen = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) { play(); seen.disconnect(); }
      }, { threshold: 0.5 });
      seen.observe(player);
    }
    if (playButton()) playButton().setAttribute("aria-live", "off");
  });
})();
