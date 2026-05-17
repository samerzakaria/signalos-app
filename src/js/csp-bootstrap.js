/**
 * csp-bootstrap.js — Reconcile inline HTML attributes with a nonce-protected CSP.
 *
 * Tauri 2 auto-injects a `nonce-<random>` value into `script-src` and
 * `style-src`. Per the CSP spec, the presence of a nonce makes the browser
 * IGNORE any sibling `'unsafe-inline'` source — so every inline `style="..."`
 * attribute and inline `onclick="..."` handler in the bundled index.html is
 * silently dropped at parse time, even though our tauri.conf.json CSP lists
 * `'unsafe-inline'`. Result: a static-looking shell where nothing clicks.
 *
 * Rather than refactor the v2 HTML (~150 inline attributes) or relax the CSP,
 * this bootstrap rewrites them at load time using mechanisms the browser DOES
 * accept under a nonce-protected CSP:
 *
 *   • inline style="…"   → moved into a single <style nonce> block, replaced
 *                          on the element with an auto-generated class name.
 *   • inline onclick="…" → attribute removed; the call expression is parsed
 *                          (funcName(arg1, arg2)) and re-bound via
 *                          addEventListener("click", …). Supported arg
 *                          tokens cover every form used in our HTML:
 *                          `this`, `event`, single-quoted strings, booleans.
 *                          The special `event.stopPropagation()` form is
 *                          recognised explicitly.
 *
 * The nonce is read from any existing nonced element (Tauri injects its IPC
 * bridge as a nonced inline script, so `document.querySelector("script[nonce]")`
 * always finds one in a Tauri webview).
 */

(function bootstrapCspCompat() {
  let sharedStyleEl = null;     // singleton <style nonce> for all hoisted rules
  let inlineCounter = 0;        // monotonic class-name generator

  function pickNonce() {
    const el = document.querySelector("script[nonce], style[nonce]");
    // `getAttribute("nonce")` is masked by the browser; the `.nonce` IDL
    // attribute returns the real value.
    return el ? (el.nonce || el.getAttribute("nonce") || "") : "";
  }

  function ensureStyleEl(nonce) {
    if (sharedStyleEl) return sharedStyleEl;
    sharedStyleEl = document.createElement("style");
    sharedStyleEl.setAttribute("data-csp-bootstrap", "inline-hoist");
    if (nonce) sharedStyleEl.nonce = nonce;
    document.head.appendChild(sharedStyleEl);
    return sharedStyleEl;
  }

  function hoistInlineStyles(root, nonce) {
    const own = root.nodeType === 1 && root.hasAttribute && root.hasAttribute("style") ? [root] : [];
    const descendants = root.querySelectorAll ? root.querySelectorAll("[style]") : [];
    const targets = [...own, ...descendants];
    if (!targets.length) return;
    const styleEl = ensureStyleEl(nonce);
    let appended = "";
    targets.forEach((el) => {
      const css = el.getAttribute("style");
      if (!css) return;
      const cls = "_inl" + (inlineCounter++);
      appended += "." + cls + "{" + css + "}\n";
      el.removeAttribute("style");
      el.classList.add(cls);
    });
    if (appended) styleEl.appendChild(document.createTextNode(appended));
  }

  function parseArg(raw, el, event) {
    const t = raw.trim();
    if (t === "") return undefined;
    if (t === "this") return el;
    if (t === "event") return event;
    if (t === "true") return true;
    if (t === "false") return false;
    if (t === "null") return null;
    // Single- or double-quoted string literal
    if ((t.startsWith("'") && t.endsWith("'")) || (t.startsWith('"') && t.endsWith('"'))) {
      return t.slice(1, -1);
    }
    // Numeric literal
    if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
    // Unknown — return the raw string. Safer than crashing the handler.
    return t;
  }

  // Resolve `this.parentElement.remove`-style chains used by a couple of
  // dynamically injected file-toast close buttons. Last segment must be the
  // method to call; intermediate segments are property accesses.
  function callThisChain(el, chain) {
    let target = el;
    for (let i = 0; i < chain.length - 1; i++) {
      if (target == null) return;
      target = target[chain[i]];
    }
    const method = chain[chain.length - 1];
    if (target != null && typeof target[method] === "function") {
      target[method]();
    }
  }

  function bindHandler(el) {
    const expr = (el.getAttribute("onclick") || "").trim();
    el.removeAttribute("onclick");

    // Pattern 1: `event.method()` — bare event member call (modal stopPropagation).
    const eventMatch = expr.match(/^event\.(\w+)\(\s*\)\s*;?\s*$/);
    if (eventMatch) {
      const method = eventMatch[1];
      el.addEventListener("click", (ev) => {
        if (typeof ev[method] === "function") ev[method]();
      });
      return;
    }

    // Pattern 2: `this.a.b.method()` — chained access on the clicked element.
    const thisMatch = expr.match(/^this((?:\.\w+)+)\(\s*\)\s*;?\s*$/);
    if (thisMatch) {
      const chain = thisMatch[1].slice(1).split(".");
      el.addEventListener("click", () => callThisChain(el, chain));
      return;
    }

    // Pattern 3: `funcName(arg, arg, ...)` — call a global function.
    const callMatch = expr.match(/^(\w+)\s*\((.*)\)\s*;?\s*$/);
    if (!callMatch) {
      console.warn("[csp-bootstrap] unparsed onclick:", expr);
      return;
    }
    const fname = callMatch[1];
    const argsRaw = callMatch[2];
    const argTokens = argsRaw.trim() === "" ? [] : splitArgs(argsRaw);
    el.addEventListener("click", (ev) => {
      const fn = window[fname];
      if (typeof fn !== "function") {
        console.warn("[csp-bootstrap] missing handler:", fname);
        return;
      }
      const args = argTokens.map((tok) => parseArg(tok, el, ev));
      try {
        fn.apply(el, args);
      } catch (err) {
        console.error("[csp-bootstrap] handler threw:", fname, err);
      }
    });
  }

  function bindInlineHandlers(root) {
    if (root.nodeType === 1 && root.hasAttribute && root.hasAttribute("onclick")) bindHandler(root);
    if (root.querySelectorAll) {
      root.querySelectorAll("[onclick]").forEach(bindHandler);
    }
  }

  // Split a CSV argument list while respecting single- and double-quoted
  // string literals. No support for escaped quotes (not needed — our HTML
  // doesn't have any).
  function splitArgs(s) {
    const out = [];
    let depth = 0;
    let quote = null;
    let start = 0;
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (quote) {
        if (c === quote) quote = null;
        continue;
      }
      if (c === "'" || c === '"') { quote = c; continue; }
      if (c === "(") depth++;
      else if (c === ")") depth--;
      else if (c === "," && depth === 0) {
        out.push(s.slice(start, i));
        start = i + 1;
      }
    }
    out.push(s.slice(start));
    return out;
  }

  function processSubtree(root, nonce) {
    hoistInlineStyles(root, nonce);
    bindInlineHandlers(root);
  }

  function run() {
    const nonce = pickNonce();
    processSubtree(document.body, nonce);

    // app-v2.js injects HTML via innerHTML in dozens of places (chat bubbles,
    // secret rows, activity cards, modals, toasts). Those nodes may carry
    // their own inline style="…" / onclick="…", which would silently fall
    // through the CSP nonce gate. Catch them at insertion time.
    const observer = new MutationObserver((records) => {
      for (const r of records) {
        for (const node of r.addedNodes) {
          if (node.nodeType !== 1) continue;
          processSubtree(node, nonce);
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run, { once: true });
  } else {
    run();
  }
})();
