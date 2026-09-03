/* conversation-eval-lab — the demo page.
 *
 * Four jobs, all optional. With JavaScript off the page is still complete:
 * the score table renders the graded ("as heard") channel, both columns sit in
 * a disclosure below it, the commands are selectable text, and nothing is
 * hidden waiting for a scroll.
 *
 * No network, no dependencies, works from file://.
 */
(function () {
  "use strict";

  var hasIO = "IntersectionObserver" in window;
  var reduced =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // The motion CSS is gated on this class so a browser without the observer,
  // or a script that never ran, shows every element exactly where it belongs.
  if (hasIO && !reduced) {
    document.documentElement.className += " has-io";
  }

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

    // First reveal: if the table is still below the fold, clear the cells now
    // and let paint() light them, staggered, when it scrolls into view. A
    // table already on screen keeps its lit cells and never blinks.
    if (
      hasIO &&
      !reduced &&
      table.getBoundingClientRect().top > window.innerHeight
    ) {
      var lit = table.querySelectorAll(".bar i.on");
      for (var k = 0; k < lit.length; k++) {
        lit[k].classList.remove("on");
      }
      table.classList.add("is-snapping");
      var snap = new IntersectionObserver(
        function (entries, self) {
          for (var e = 0; e < entries.length; e++) {
            if (entries[e].isIntersecting) {
              paint("heard");
              self.disconnect();
              window.setTimeout(function () {
                table.classList.remove("is-snapping");
              }, 600);
            }
          }
        },
        { threshold: 0.35 }
      );
      snap.observe(table);
    }
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
   * 3. Settle the section headers in as they arrive.
   *    Only headers below the fold at load are marked, so nothing the
   *    reader can already see ever starts transparent.
   * ------------------------------------------------------------- */

  if (hasIO && !reduced) {
    var heads = Array.prototype.filter.call(
      document.querySelectorAll(".sec__head"),
      function (head) {
        return head.getBoundingClientRect().top > window.innerHeight;
      }
    );
    if (heads.length) {
      var settle = new IntersectionObserver(
        function (entries, self) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-in");
              self.unobserve(entry.target);
            }
          });
        },
        { rootMargin: "0px 0px -12% 0px", threshold: 0 }
      );
      heads.forEach(function (head) {
        head.classList.add("settle");
        settle.observe(head);
      });
    }
  }

  /* ---------------------------------------------------------------
   * 4. Mark the section being read, in the nav.
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
