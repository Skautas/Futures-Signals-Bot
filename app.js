(function () {
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.querySelector(".nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });

    nav.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        nav.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  var root = document.documentElement;

  function fsState() {
    return {
      active:
        document.fullscreenElement ||
        document.webkitFullscreenElement ||
        document.msFullscreenElement ||
        null,
      enter:
        root.requestFullscreen ||
        root.webkitRequestFullscreen ||
        root.msRequestFullscreen,
      leave:
        document.exitFullscreen ||
        document.webkitExitFullscreen ||
        document.msExitFullscreen,
    };
  }

  var initial = fsState();
  if (!initial.enter) return;

  var btn = document.createElement("button");
  btn.id = "vrm-fullscreen-btn";
  btn.type = "button";
  btn.className = "vrm-fullscreen-btn";

  function sync() {
    var on = !!fsState().active;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.textContent = on ? "Išeiti" : "Visas ekranas";
    btn.setAttribute(
      "aria-label",
      on
        ? "Išeiti iš viso ekrano režimo"
        : "Įjungti viso ekrano režimą patogesniam skaitymui"
    );
    btn.title = on
      ? "Išeiti iš viso ekrano (dažnai veikia ir Esc)"
      : "Visas ekranas — daugiau vietos tekstui ir fonui";
  }

  btn.addEventListener("click", function () {
    var s = fsState();
    if (s.active) {
      if (s.leave) s.leave.call(document);
    } else if (s.enter) {
      s.enter.call(root).catch(function () {});
    }
  });

  document.addEventListener("fullscreenchange", sync);
  document.addEventListener("webkitfullscreenchange", sync);
  document.addEventListener("MSFullscreenChange", sync);

  document.body.appendChild(btn);
  sync();
})();
