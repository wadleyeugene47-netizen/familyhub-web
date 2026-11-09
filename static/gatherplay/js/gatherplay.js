/* GatherPlay interactions: navigation, scroll reveals, 3D tilt, toasts, form feedback. */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  /* Header: solid background after scrolling, mobile menu toggle. */
  var header = document.querySelector("[data-header]");
  if (header) {
    var toggle = header.querySelector("[data-nav-toggle]");
    var onScroll = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    var setOpen = function (open) {
      header.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      document.body.style.overflow = open ? "hidden" : "";
    };
    if (toggle) {
      toggle.addEventListener("click", function () {
        setOpen(!header.classList.contains("is-open"));
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && header.classList.contains("is-open")) {
          setOpen(false);
          toggle.focus();
        }
      });
      window.matchMedia("(min-width: 901px)").addEventListener("change", function (event) {
        if (event.matches) setOpen(false);
      });
    }
  }

  /* Account dropdown: close on outside click or Escape. */
  document.querySelectorAll("details.account-menu").forEach(function (menu) {
    document.addEventListener("click", function (event) {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    menu.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        menu.querySelector("summary").focus();
      }
    });
  });

  /* Scroll reveal. */
  var revealItems = document.querySelectorAll("[data-reveal]");
  if (revealItems.length) {
    if (reduceMotion || !("IntersectionObserver" in window)) {
      revealItems.forEach(function (item) { item.classList.add("is-visible"); });
    } else {
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
      revealItems.forEach(function (item) { observer.observe(item); });
    }
  }

  /* Subtle perspective tilt on cards. */
  if (finePointer && !reduceMotion) {
    var maxTilt = 5;
    document.querySelectorAll("[data-tilt]").forEach(function (card) {
      var frame = null;
      card.addEventListener("pointermove", function (event) {
        var rect = card.getBoundingClientRect();
        var x = (event.clientX - rect.left) / rect.width - 0.5;
        var y = (event.clientY - rect.top) / rect.height - 0.5;
        if (frame) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(function () {
          card.style.setProperty("--ry", (x * maxTilt * 2).toFixed(2) + "deg");
          card.style.setProperty("--rx", (y * -maxTilt * 2).toFixed(2) + "deg");
        });
      });
      card.addEventListener("pointerleave", function () {
        if (frame) cancelAnimationFrame(frame);
        card.style.setProperty("--rx", "0deg");
        card.style.setProperty("--ry", "0deg");
      });
    });

    /* Hero stage follows the pointer for a parallax feel. */
    var hero = document.querySelector("[data-parallax-root]");
    var stage = document.querySelector("[data-parallax]");
    if (hero && stage) {
      var stageFrame = null;
      hero.addEventListener("pointermove", function (event) {
        var rect = hero.getBoundingClientRect();
        var px = (event.clientX - rect.left) / rect.width - 0.5;
        var py = (event.clientY - rect.top) / rect.height - 0.5;
        if (stageFrame) cancelAnimationFrame(stageFrame);
        stageFrame = requestAnimationFrame(function () {
          stage.style.setProperty("--px", px.toFixed(3));
          stage.style.setProperty("--py", py.toFixed(3));
        });
      });
      hero.addEventListener("pointerleave", function () {
        stage.style.setProperty("--px", "0");
        stage.style.setProperty("--py", "0");
      });
    }
  }

  /* Toast messages: dismissible, success/info fade out on their own. */
  var dismiss = function (alert) {
    if (!alert || alert.classList.contains("is-leaving")) return;
    alert.classList.add("is-leaving");
    window.setTimeout(function () { alert.remove(); }, reduceMotion ? 0 : 300);
  };
  document.querySelectorAll("[data-alert]").forEach(function (alert) {
    var close = alert.querySelector("[data-alert-close]");
    if (close) close.addEventListener("click", function () { dismiss(alert); });
    if (alert.matches(".alert-success, .alert-info")) {
      window.setTimeout(function () { dismiss(alert); }, 7000);
    }
  });

  /* Show a loading state on submit and block accidental double submits. */
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      var button = event.submitter || form.querySelector("button[type='submit'], button:not([type])");
      if (button && form.method.toLowerCase() === "post") {
        button.classList.add("is-loading");
        button.setAttribute("aria-busy", "true");
      }
      /* Allow resubmitting if the browser restores the page from cache. */
      window.addEventListener("pageshow", function () {
        form.dataset.submitting = "false";
        if (button) {
          button.classList.remove("is-loading");
          button.removeAttribute("aria-busy");
        }
      }, { once: true });
    });
  });

  /* Championship tabs: without JavaScript every section stays visible. */
  document.querySelectorAll("[data-tabs]").forEach(function (nav) {
    var tabs = Array.prototype.slice.call(nav.querySelectorAll("[data-tab]"));
    var panels = document.querySelector(".cup-panels");
    if (!tabs.length || !panels) return;
    var panelFor = function (tab) { return panels.querySelector("[data-panel='" + tab.dataset.tab + "']"); };
    var select = function (tab, focus) {
      tabs.forEach(function (other) {
        var active = other === tab;
        other.setAttribute("aria-selected", String(active));
        other.setAttribute("tabindex", active ? "0" : "-1");
        var panel = panelFor(other);
        if (panel) {
          panel.hidden = !active;
          panel.classList.toggle("is-entering", active && !reduceMotion);
          if (active) {
            panel.querySelectorAll("[data-reveal]").forEach(function (item) { item.classList.add("is-visible"); });
          }
        }
      });
      if (focus) tab.focus();
      tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    };
    tabs.forEach(function (tab, index) {
      var panel = panelFor(tab);
      tab.id = "tab-" + tab.dataset.tab;
      tab.setAttribute("aria-controls", tab.dataset.tab);
      if (panel) panel.setAttribute("aria-labelledby", tab.id);
      tab.addEventListener("click", function (event) {
        event.preventDefault();
        select(tab, false);
        if (history.replaceState) history.replaceState(null, "", "#" + tab.dataset.tab);
      });
      tab.addEventListener("keydown", function (event) {
        var next = null;
        if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
        if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
        if (event.key === "Home") next = tabs[0];
        if (event.key === "End") next = tabs[tabs.length - 1];
        if (next) {
          event.preventDefault();
          select(next, true);
        }
      });
    });
    panels.setAttribute("data-tabs-ready", "");
    var fromHash = tabs.filter(function (tab) { return "#" + tab.dataset.tab === window.location.hash; })[0];
    select(fromHash || tabs[0], false);
  });

  /* Live championship updates: poll a small version stamp and offer a refresh when results change. */
  document.querySelectorAll("[data-live-url]").forEach(function (root) {
    var pill = root.querySelector("[data-live-pill]");
    var version = root.dataset.liveVersion;
    if (!pill || !window.fetch) return;
    var refresh = pill.querySelector("a");
    if (refresh) {
      /* Reload in place so the open tab (kept in the URL hash) survives. */
      refresh.addEventListener("click", function (event) {
        event.preventDefault();
        window.location.reload();
      });
    }
    var timer = null;
    var check = function () {
      if (document.hidden) return;
      fetch(root.dataset.liveUrl, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) {
          if (data && data.version && data.version !== version) {
            pill.hidden = false;
            window.clearInterval(timer);
          }
        })
        .catch(function () {});
    };
    timer = window.setInterval(check, 30000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) check(); });
  });
})();
