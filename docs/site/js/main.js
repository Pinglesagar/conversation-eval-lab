/* conversation-eval-lab — the demo page.
 *
 * Three jobs, all optional. With JavaScript off the page is still complete:
 * the score table renders the graded ("as heard") channel, both columns sit in
 * a disclosure below it, and the commands are selectable text.
 *
 * No network, no dependencies, works from file://.
 */
(function () {
  "use strict";

  /* ---------------------------------------------------------------
   * 1. The one interaction: sent against heard, on the same table.
   * ------------------------------------------------------------- */

  var CHANNELS = {
    sent: {
      head: "As sent",
      status:
        "<b>What was sent</b> — a counterfactual re-simulation on the same notes."
    },
    heard: {
      head: "As heard",
      status:
        "<b>What was heard</b> — the unformatted transcript that was actually graded."
    }
  };

  var table = document.querySelector("[data-score]");
  var switcher = document.querySelector("[data-switch]");

  if (table && switcher) {
    var rows = Array.prototype.slice.call(table.querySelectorAll("[data-row]"));
    var head = table.querySelector("[data-score-head]");
    var status = document.querySelector("[data-switch-status]");
    var buttons = Array.prototype.slice.call(
      switcher.querySelectorAll("[data-channel]")
    );

    var paint = function (channel) {
      rows.forEach(function (row) {
        var value = parseInt(row.getAttribute("data-" + channel), 10);
        var target = row.querySelector("[data-value]");
        if (target) {
          target.textContent = String(value);
        }
        var segments = row.querySelectorAll(".bar i");
        for (var i = 0; i < segments.length; i++) {
          segments[i].classList.toggle("on", i < value);
        }
      });

      if (head) {
        head.textContent = CHANNELS[channel].head;
      }
      if (status) {
        status.innerHTML = CHANNELS[channel].status;
      }

      buttons.forEach(function (button) {
        var on = button.getAttribute("data-channel") === channel;
        button.classList.toggle("is-on", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
      });
    };

    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        paint(button.getAttribute("data-channel"));
      });
    });

    // The toggle is hidden in the markup so it never appears without its
    // behaviour. Reveal it, then repaint the channel the HTML already shows.
    switcher.removeAttribute("hidden");
    paint("heard");
  }

  /* ---------------------------------------------------------------
   * 2. Copy a command. Falls back, and removes itself if it cannot.
   * ------------------------------------------------------------- */

  var copyText = function (text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var scratch = document.createElement("textarea");
      scratch.value = text;
      scratch.setAttribute("readonly", "");
      scratch.style.position = "fixed";
      scratch.style.top = "-1000px";
      document.body.appendChild(scratch);
      scratch.select();
      var ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (err) {
        ok = false;
      }
      document.body.removeChild(scratch);
      ok ? resolve() : reject(new Error("copy unavailable"));
    });
  };

  Array.prototype.forEach.call(
    document.querySelectorAll(".copy"),
    function (button) {
      var original = button.firstChild;
      var timer = null;

      var say = function (label, done, hold) {
        window.clearTimeout(timer);
        original.nodeValue = label;
        button.classList.toggle("is-done", done);
        timer = window.setTimeout(function () {
          original.nodeValue = "Copy";
          button.classList.remove("is-done");
        }, hold);
      };

      button.addEventListener("click", function () {
        copyText(button.getAttribute("data-copy")).then(
          function () {
            say("Copied", true, 1600);
          },
          function () {
            // Clipboard access can be refused (no user activation, an
            // unfocused document, a locked-down browser). Say so and reset —
            // the command beside it is selectable text either way.
            say("Select it", false, 2600);
          }
        );
      });
    }
  );

  /* ---------------------------------------------------------------
   * 3. Mark the section being read, in the nav.
   * ------------------------------------------------------------- */

  if ("IntersectionObserver" in window) {
    var links = {};
    Array.prototype.forEach.call(
      document.querySelectorAll(".nav__list a[href^='#']"),
      function (link) {
        links[link.getAttribute("href").slice(1)] = link;
      }
    );

    var sections = Object.keys(links)
      .map(function (id) {
        return document.getElementById(id);
      })
      .filter(Boolean);

    var visible = {};

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          visible[entry.target.id] = entry.isIntersecting;
        });

        var current = null;
        sections.forEach(function (section) {
          if (visible[section.id] && current === null) {
            current = section.id;
          }
        });

        Object.keys(links).forEach(function (id) {
          if (id === current) {
            links[id].setAttribute("aria-current", "true");
          } else {
            links[id].removeAttribute("aria-current");
          }
        });
      },
      { rootMargin: "-30% 0px -55% 0px", threshold: 0 }
    );

    sections.forEach(function (section) {
      observer.observe(section);
    });
  }
})();
