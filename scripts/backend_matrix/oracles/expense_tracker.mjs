#!/usr/bin/env node

/**
 * Black-box acceptance oracle for the backend-matrix expense-tracker scenario.
 *
 * The oracle deliberately knows nothing about the generated application's source,
 * framework, test IDs, or persistence implementation. It serves the production
 * build and exercises the same rendered accessibility surface a user receives.
 *
 * Exit codes:
 *   0 - every product check passed
 *   1 - one or more product checks failed
 *   2 - the oracle infrastructure or invocation failed
 */

import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const ORACLE_VERSION = "1.1.0";
const DEFAULT_TIMEOUT_MS = 15_000;
const CHECKS = [
  "BOOT_FORM",
  "ADD_FIELDS",
  "DELETE_DURABLE",
  "RECONCILE_DURABLE",
  "FILTER",
  "PERSIST_ADD",
];

const MIME_TYPES = new Map([
  [".css", "text/css; charset=utf-8"],
  [".gif", "image/gif"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".jpeg", "image/jpeg"],
  [".jpg", "image/jpeg"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".mjs", "text/javascript; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".txt", "text/plain; charset=utf-8"],
  [".webmanifest", "application/manifest+json"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

class ProductFailure extends Error {
  constructor(message, details = undefined) {
    super(message);
    this.name = "ProductFailure";
    this.details = details;
  }
}

class InfrastructureFailure extends Error {
  constructor(message, details = undefined) {
    super(message);
    this.name = "InfrastructureFailure";
    this.details = details;
  }
}

function usage() {
  return [
    "Usage:",
    "  node scripts/backend_matrix/oracles/expense_tracker.mjs \\",
    "    --dist <production-dist-directory> \\",
    "    --evidence <result.json> [--artifacts <directory>] [--timeout-ms <ms>]",
    "",
    "The oracle starts its own ephemeral loopback server. It does not accept a",
    "pre-running dev-server URL, and it never reads generated source or storage.",
  ].join("\n");
}

function parseArgs(argv) {
  const options = {
    dist: undefined,
    evidence: undefined,
    artifacts: undefined,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    help: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      options.help = true;
      continue;
    }

    if (!["--dist", "--evidence", "--artifacts", "--timeout-ms"].includes(argument)) {
      throw new InfrastructureFailure(`Unknown argument: ${argument}`);
    }

    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new InfrastructureFailure(`${argument} requires a value`);
    }
    index += 1;

    if (argument === "--dist") options.dist = value;
    if (argument === "--evidence") options.evidence = value;
    if (argument === "--artifacts") options.artifacts = value;
    if (argument === "--timeout-ms") {
      const parsed = Number(value);
      if (!Number.isSafeInteger(parsed) || parsed < 1_000 || parsed > 120_000) {
        throw new InfrastructureFailure("--timeout-ms must be an integer from 1000 to 120000");
      }
      options.timeoutMs = parsed;
    }
  }

  if (!options.help && !options.dist) {
    throw new InfrastructureFailure("--dist is required");
  }

  if (options.dist) options.dist = path.resolve(options.dist);
  if (options.evidence) options.evidence = path.resolve(options.evidence);
  if (options.artifacts) options.artifacts = path.resolve(options.artifacts);
  if (!options.artifacts) {
    options.artifacts = options.evidence
      ? path.join(path.dirname(options.evidence), "expense-tracker-artifacts")
      : path.join(os.tmpdir(), `signalos-expense-oracle-${process.pid}`);
  }
  return options;
}

function serializeError(error) {
  return {
    type: error?.name ?? "Error",
    message: String(error?.message ?? error),
    ...(error?.details === undefined ? {} : { details: error.details }),
  };
}

async function sha256File(filePath) {
  const bytes = await fs.promises.readFile(filePath);
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function normalizeText(value) {
  return String(value ?? "")
    .replace(/\s+/g, " ")
    .trim();
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function isInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

async function containedRealFile(distRoot, candidate) {
  try {
    const linkStat = await fs.promises.lstat(candidate);
    if (linkStat.isSymbolicLink()) return undefined;
    const realCandidate = await fs.promises.realpath(candidate);
    if (!isInside(distRoot, realCandidate)) return undefined;
    const stat = await fs.promises.stat(realCandidate);
    return stat.isFile() ? realCandidate : undefined;
  } catch {
    return undefined;
  }
}

async function existingStaticFile(distRoot, pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return undefined;
  }

  const relative = decoded.replace(/^[/\\]+/, "");
  const candidate = path.resolve(distRoot, relative || "index.html");
  if (!isInside(distRoot, candidate)) return undefined;

  try {
    const direct = await containedRealFile(distRoot, candidate);
    if (direct) return direct;
    const stat = await fs.promises.lstat(candidate);
    if (stat.isDirectory()) {
      const nestedIndex = path.join(candidate, "index.html");
      if (isInside(distRoot, nestedIndex)) return containedRealFile(distRoot, nestedIndex);
    }
  } catch {
    // A client-side route is served by the SPA fallback below.
  }

  if (!path.extname(relative)) return containedRealFile(distRoot, path.join(distRoot, "index.html"));
  return undefined;
}

async function startStaticServer(distRoot) {
  const requestedRoot = path.resolve(distRoot);
  let canonicalRoot;
  try {
    const rootStat = await fs.promises.lstat(requestedRoot);
    if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) {
      throw new Error("dist root is a symlink or is not a directory");
    }
    canonicalRoot = await fs.promises.realpath(requestedRoot);
  } catch (error) {
    throw new InfrastructureFailure(`Production build directory is unsafe or missing: ${requestedRoot}`, serializeError(error));
  }
  const indexPath = await containedRealFile(canonicalRoot, path.join(canonicalRoot, "index.html"));
  if (!indexPath) {
    throw new InfrastructureFailure(`Production entry point is unsafe or missing: ${path.join(requestedRoot, "index.html")}`);
  }

  const serverErrors = [];
  const server = http.createServer((request, response) => {
    void (async () => {
      if (!request.url || !["GET", "HEAD"].includes(request.method ?? "")) {
        response.writeHead(405, { Allow: "GET, HEAD" });
        response.end();
        return;
      }

      const requestUrl = new URL(request.url, "http://127.0.0.1");
      const filePath = await existingStaticFile(canonicalRoot, requestUrl.pathname);
      if (!filePath) {
        response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
        response.end("Not found");
        return;
      }

      try {
        const fileStat = await fs.promises.stat(filePath);
        if (!fileStat.isFile()) throw new Error("not a file");
        response.writeHead(200, {
          "Cache-Control": "no-store",
          "Content-Length": fileStat.size,
          "Content-Type": MIME_TYPES.get(path.extname(filePath).toLowerCase()) ?? "application/octet-stream",
          "X-Content-Type-Options": "nosniff",
        });
        if (request.method === "HEAD") {
          response.end();
          return;
        }
        fs.createReadStream(filePath)
          .on("error", (error) => response.destroy(error))
          .pipe(response);
      } catch {
        response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
        response.end("Not found");
      }
    })().catch((error) => {
      serverErrors.push(serializeError(error));
      if (!response.headersSent) response.writeHead(500);
      response.end();
    });
  });

  await new Promise((resolve, reject) => {
    const onError = (error) => reject(new InfrastructureFailure("Could not start loopback server", serializeError(error)));
    server.once("error", onError);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", onError);
      resolve();
    });
  });

  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new InfrastructureFailure("Loopback server did not expose a TCP address");
  }

  return {
    url: `http://127.0.0.1:${address.port}/`,
    indexPath,
    serverErrors,
    async close() {
      await new Promise((resolve) => server.close(() => resolve()));
    },
  };
}

async function firstVisible(locator) {
  const count = Math.min(await locator.count(), 30);
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false)) return candidate;
  }
  return undefined;
}

async function firstVisibleFrom(locators) {
  for (const locator of locators) {
    const candidate = await firstVisible(locator);
    if (candidate) return candidate;
  }
  return undefined;
}

async function accessibleDescriptor(locator) {
  return locator.evaluate((element) => {
    const tag = element.tagName.toLowerCase();
    const type = element.getAttribute("type") ?? "";
    const labels = "labels" in element && element.labels
      ? Array.from(element.labels).map((label) => label.textContent?.replace(/\s+/g, " ").trim()).filter(Boolean)
      : [];
    const labelledBy = (element.getAttribute("aria-labelledby") ?? "")
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent?.replace(/\s+/g, " ").trim())
      .filter(Boolean);
    const ariaLabel = element.getAttribute("aria-label")?.trim() ?? "";
    const title = element.getAttribute("title")?.trim() ?? "";
    const text = element.textContent?.replace(/\s+/g, " ").trim() ?? "";
    const valueName = ["button", "submit", "reset"].includes(type)
      ? (element.getAttribute("value")?.trim() ?? "")
      : "";
    const nativeTextName = tag === "button" || element.getAttribute("role") === "button" ? text : "";
    return {
      tag,
      type,
      labels,
      labelledBy,
      ariaLabel,
      title,
      text,
      placeholder: element.getAttribute("placeholder") ?? "",
      hasAccessibleName: Boolean(labels.length || labelledBy.length || ariaLabel || title || nativeTextName || valueName),
    };
  });
}

function fieldLocators(root, field) {
  if (field === "description") {
    return [
      root.getByLabel(/description|expense (?:name|title)|item (?:name|title)/i),
      root.getByRole("textbox", { name: /description|expense (?:name|title)|item (?:name|title)/i }),
      root.getByPlaceholder(/description|expense (?:name|title)|item (?:name|title)/i),
      root.locator('textarea[placeholder*="description" i], input[placeholder*="description" i]'),
      root.locator('input[type="text"], textarea').filter({ hasNot: root.locator('[type="search"]') }),
    ];
  }
  if (field === "amount") {
    return [
      root.getByLabel(/amount|price|cost/i),
      root.getByRole("spinbutton", { name: /amount|price|cost/i }),
      root.getByPlaceholder(/amount|price|cost/i),
      root.locator('input[type="number"]'),
    ];
  }
  if (field === "category") {
    return [
      root.getByLabel(/category/i),
      root.getByRole("combobox", { name: /category/i }),
      root.locator("select"),
    ];
  }
  if (field === "date") {
    return [
      root.getByLabel(/date|when/i),
      root.locator('input[type="date"]'),
      root.getByPlaceholder(/date|yyyy|mm.*dd|dd.*mm/i),
    ];
  }
  throw new InfrastructureFailure(`Unknown form field requested by oracle: ${field}`);
}

async function resolveField(root, field) {
  return firstVisibleFrom(fieldLocators(root, field));
}

async function resolveSubmit(root) {
  return firstVisibleFrom([
    root.getByRole("button", { name: /^(?:add|save|create|record)(?: new)?(?: expense| transaction| item)?$/i }),
    root.getByRole("button", { name: /add.*(?:expense|transaction|item)|save.*(?:expense|transaction|item)/i }),
    root.locator('button[type="submit"], input[type="submit"]'),
  ]);
}

async function discoverExpenseForm(page, { requireAccessibleNames = false } = {}) {
  const roots = [];
  const forms = page.locator("form");
  const formCount = Math.min(await forms.count(), 20);
  for (let index = 0; index < formCount; index += 1) {
    const form = forms.nth(index);
    if (await form.isVisible().catch(() => false)) roots.push(form);
  }
  roots.push(page);

  for (const root of roots) {
    const description = await resolveField(root, "description");
    const amount = await resolveField(root, "amount");
    const category = await resolveField(root, "category");
    const date = await resolveField(root, "date");
    const submit = await resolveSubmit(root);
    if (!description || !amount || !category || !date || !submit) continue;

    const fields = { description, amount, category, date, submit };
    const descriptors = {};
    for (const [name, locator] of Object.entries(fields)) {
      descriptors[name] = await accessibleDescriptor(locator);
    }
    if (requireAccessibleNames) {
      const unnamed = Object.entries(descriptors)
        .filter(([, descriptor]) => !descriptor.hasAccessibleName)
        .map(([name]) => name);
      if (unnamed.length) {
        throw new ProductFailure(`Expense form controls lack accessible names: ${unnamed.join(", ")}`, {
          controls: descriptors,
        });
      }
    }
    return { ...fields, descriptors };
  }

  throw new ProductFailure("Could not find one usable form with description, amount, category, date, and submit controls");
}

async function focusStyle(locator) {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      backgroundColor: style.backgroundColor,
      borderColor: style.borderColor,
      borderWidth: style.borderWidth,
      boxShadow: style.boxShadow,
      outlineColor: style.outlineColor,
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
      focusVisible: element.matches(":focus-visible"),
    };
  });
}

function hasVisibleFocusIndicator(before, focused) {
  const outlineWidth = Number.parseFloat(focused.outlineWidth) || 0;
  const outlineVisible = focused.outlineStyle !== "none"
    && outlineWidth > 0
    && !["transparent", "rgba(0, 0, 0, 0)"].includes(focused.outlineColor);
  const shadowVisible = focused.boxShadow !== "none"
    && !focused.boxShadow.includes("rgba(0, 0, 0, 0)");
  const surfaceChanged = ["backgroundColor", "borderColor", "borderWidth"]
    .some((property) => before[property] !== focused[property]);
  return focused.focusVisible && (outlineVisible || shadowVisible || surfaceChanged);
}

async function verifyKeyboardReachability(page, controls) {
  const entries = Object.entries(controls);
  const normalStyles = Object.fromEntries(
    await Promise.all(entries.map(async ([name, locator]) => [name, await focusStyle(locator)])),
  );
  const reached = {};
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    document.body.setAttribute("tabindex", "-1");
    document.body.focus();
    document.body.removeAttribute("tabindex");
  });

  for (let step = 1; step <= 100 && Object.keys(reached).length < entries.length; step += 1) {
    await page.keyboard.press("Tab");
    for (const [name, locator] of entries) {
      if (reached[name]) continue;
      const active = await locator.evaluate((element) => document.activeElement === element).catch(() => false);
      if (!active) continue;
      const focusedStyle = await focusStyle(locator);
      reached[name] = {
        tabStep: step,
        focusVisible: focusedStyle.focusVisible,
        visibleIndicator: hasVisibleFocusIndicator(normalStyles[name], focusedStyle),
        focusedStyle,
      };
    }
  }

  const unreachable = entries.map(([name]) => name).filter((name) => !reached[name]);
  if (unreachable.length) {
    throw new ProductFailure(`Expense form controls are not keyboard reachable: ${unreachable.join(", ")}`, {
      reached,
    });
  }
  const invisible = entries.map(([name]) => name).filter((name) => !reached[name].visibleIndicator);
  if (invisible.length) {
    throw new ProductFailure(`Keyboard focus is not visibly indicated on: ${invisible.join(", ")}`, {
      reached,
    });
  }
  return reached;
}

async function selectCategory(locator, category, page) {
  const tag = await locator.evaluate((element) => element.tagName.toLowerCase());
  if (tag === "select") {
    const options = await locator.locator("option").evaluateAll((nodes) => nodes.map((node) => ({
      label: node.textContent?.replace(/\s+/g, " ").trim() ?? "",
      value: node.value,
    })));
    const match = options.find((option) => option.label.toLowerCase() === category.toLowerCase())
      ?? options.find((option) => option.value.toLowerCase() === category.toLowerCase());
    if (!match) {
      throw new ProductFailure(`Category control does not offer ${category}`, { options });
    }
    await locator.selectOption({ value: match.value });
    return;
  }

  await locator.click();
  if (await locator.isEditable().catch(() => false)) await locator.fill(category);
  const option = await firstVisible(page.getByRole("option", { name: new RegExp(`^${escapeRegex(category)}$`, "i") }));
  if (option) {
    await option.click();
  } else {
    await locator.press("Enter").catch(() => undefined);
  }
}

async function fillExpense(page, expense) {
  const form = await discoverExpenseForm(page);
  await form.description.fill(expense.description);
  await form.amount.fill(String(expense.amount));
  await selectCategory(form.category, expense.category, page);
  await form.date.fill(expense.date);
  await form.submit.click();
  await pollUntil(
    async () => (await visibleTextCount(page, expense.description)) > 0,
    `Expense was not rendered after submitting: ${expense.description}`,
  );
}

async function visibleTextCount(page, text) {
  const locator = page.getByText(text, { exact: true });
  const count = Math.min(await locator.count(), 30);
  let visible = 0;
  for (let index = 0; index < count; index += 1) {
    if (await locator.nth(index).isVisible().catch(() => false)) visible += 1;
  }
  return visible;
}

async function pollUntil(predicate, failureMessage, timeoutMs = 8_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      if (await predicate()) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new ProductFailure(failureMessage, lastError ? { lastError: serializeError(lastError) } : undefined);
}

async function findAction(container, action) {
  if (action === "delete") {
    return firstVisibleFrom([
      container.getByRole("button", { name: /delete|remove/i }),
      container.getByRole("link", { name: /delete|remove/i }),
    ]);
  }
  if (action === "reconcile") {
    const named = await firstVisibleFrom([
      container.getByRole("checkbox", { name: /reconcil/i }),
      container.getByRole("switch", { name: /reconcil/i }),
      container.getByRole("button", { name: /reconcil/i }),
    ]);
    if (named) return named;

    // A checkbox's checked state is intrinsically accessible, even when its nearby
    // label was not specific enough for the regex above. There must be only one in
    // the record so the action remains unambiguous to this black-box oracle.
    const checkboxes = container.getByRole("checkbox");
    const visible = [];
    for (let index = 0; index < Math.min(await checkboxes.count(), 5); index += 1) {
      const checkbox = checkboxes.nth(index);
      if (await checkbox.isVisible().catch(() => false)) visible.push(checkbox);
    }
    return visible.length === 1 ? visible[0] : undefined;
  }
  throw new InfrastructureFailure(`Unknown record action requested by oracle: ${action}`);
}

async function findRecordContainer(page, description, { action, includes = [], excludes = [] } = {}) {
  const textLocator = await firstVisible(page.getByText(description, { exact: true }));
  if (!textLocator) throw new ProductFailure(`Could not find rendered expense: ${description}`);

  let current = textLocator;
  for (let depth = 0; depth < 8; depth += 1) {
    const tag = await current.evaluate((element) => element.tagName.toLowerCase()).catch(() => "");
    if (["body", "html"].includes(tag)) break;
    const text = normalizeText(await current.innerText().catch(() => ""));
    const hasExpectedText = includes.every((value) => text.toLowerCase().includes(String(value).toLowerCase()));
    const hasExcludedRecord = excludes.some((value) => text.includes(String(value)));
    const foundAction = action ? await findAction(current, action) : undefined;
    if (hasExpectedText && !hasExcludedRecord && (!action || foundAction)) {
      return { container: current, action: foundAction, text };
    }
    current = current.locator("..");
  }

  throw new ProductFailure(`Could not isolate the record for ${description}${action ? ` with a ${action} action` : ""}`);
}

function amountAppears(text, amount) {
  const compact = text.replace(/\s+/g, "").replace(/,/g, "");
  return compact.includes(Number(amount).toFixed(2)) || compact.includes(String(Number(amount)));
}

function dateAppears(text, isoDate) {
  const [year, month, day] = isoDate.split("-").map(Number);
  const shortMonth = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
  const longMonth = new Intl.DateTimeFormat("en-US", { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
  const variants = [
    isoDate,
    `${month}/${day}/${year}`,
    `${String(month).padStart(2, "0")}/${String(day).padStart(2, "0")}/${year}`,
    `${day}/${month}/${year}`,
    `${String(day).padStart(2, "0")}/${String(month).padStart(2, "0")}/${year}`,
    `${shortMonth} ${day}, ${year}`,
    `${longMonth} ${day}, ${year}`,
    `${day} ${shortMonth} ${year}`,
    `${day} ${longMonth} ${year}`,
  ];
  const normalized = normalizeText(text).toLowerCase();
  return variants.some((variant) => normalized.includes(variant.toLowerCase()));
}

async function reconcileState(container, control) {
  const controlState = await control.evaluate((element) => ({
    tag: element.tagName.toLowerCase(),
    type: element.getAttribute("type") ?? "",
    checked: "checked" in element ? Boolean(element.checked) : undefined,
    ariaChecked: element.getAttribute("aria-checked"),
    ariaPressed: element.getAttribute("aria-pressed"),
    text: (element.getAttribute("aria-label") || element.textContent || "").replace(/\s+/g, " ").trim(),
  }));

  let value;
  let source;
  if (controlState.type === "checkbox" || controlState.checked !== undefined) {
    value = controlState.checked;
    source = "checked";
  } else if (["true", "false"].includes(controlState.ariaChecked)) {
    value = controlState.ariaChecked === "true";
    source = "aria-checked";
  } else if (["true", "false"].includes(controlState.ariaPressed)) {
    value = controlState.ariaPressed === "true";
    source = "aria-pressed";
  } else {
    const actionText = controlState.text.toLowerCase();
    if (/\b(?:unmark|undo|unreconcile)\b.*reconcil|\bmark\b.*(?:as )?reconcil/.test(actionText)) {
      value = !/\bmark\b.*(?:as )?reconcil/.test(actionText) || /\bunmark\b|\bundo\b|\bunreconcile\b/.test(actionText);
      source = "action-name";
    } else if (/^reconcile(?: expense| transaction| item)?$/i.test(actionText)) {
      value = false;
      source = "action-name";
    } else {
      const recordText = normalizeText(await container.innerText()).toLowerCase();
      if (/\b(?:not reconciled|unreconciled|pending)\b/.test(recordText)) {
        value = false;
        source = "record-status";
      } else if (/\breconciled\b/.test(recordText)) {
        value = true;
        source = "record-status";
      }
    }
  }

  if (typeof value !== "boolean") {
    throw new ProductFailure("Reconcile state is not exposed through a checkbox, ARIA state, or visible status", {
      control: controlState,
    });
  }
  return { value, source, control: controlState };
}

async function clickDelete(page, description, excludes = []) {
  const record = await findRecordContainer(page, description, { action: "delete", excludes });
  await record.action.click();

  const dialog = await firstVisible(page.getByRole("dialog")).catch(() => undefined);
  if (dialog && (await visibleTextCount(page, description)) > 0) {
    const confirmation = await firstVisibleFrom([
      dialog.getByRole("button", { name: /^(?:delete|remove|confirm|yes)(?: expense| item)?$/i }),
      dialog.getByRole("button", { name: /delete|remove/i }),
    ]);
    if (confirmation) await confirmation.click();
  }
}

async function navigateToCanonicalProduct(page, baseUrl) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("body").waitFor({ state: "visible" });
}

async function assertRemainsAbsent(page, description, stableMs = 1_500) {
  const deadline = Date.now() + stableMs;
  while (Date.now() < deadline) {
    if ((await visibleTextCount(page, description)) !== 0) {
      throw new ProductFailure(`Expense returned after hydration: ${description}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

async function filterControl(page) {
  const selects = page.locator("select");
  for (let index = 0; index < Math.min(await selects.count(), 20); index += 1) {
    const select = selects.nth(index);
    if (!(await select.isVisible().catch(() => false))) continue;
    const options = await select.locator("option").evaluateAll((nodes) => nodes.map((node) => ({
      label: node.textContent?.replace(/\s+/g, " ").trim() ?? "",
      value: node.value,
    })));
    const labels = options.map((option) => option.label.toLowerCase());
    if (labels.some((label) => /^(?:all|all categories|any)$/.test(label))
      && labels.includes("food")
      && labels.includes("travel")) {
      return { kind: "select", locator: select, options };
    }
  }

  const all = await firstVisibleFrom([
    page.getByRole("button", { name: /^(?:all|all categories)$/i }),
    page.getByRole("tab", { name: /^(?:all|all categories)$/i }),
  ]);
  const food = await firstVisibleFrom([
    page.getByRole("button", { name: /^food$/i }),
    page.getByRole("tab", { name: /^food$/i }),
  ]);
  const travel = await firstVisibleFrom([
    page.getByRole("button", { name: /^travel$/i }),
    page.getByRole("tab", { name: /^travel$/i }),
  ]);
  if (all && food && travel) return { kind: "buttons", buttons: { all, food, travel } };

  throw new ProductFailure("Could not find an accessible category filter offering All, Food, and Travel");
}

async function applyFilter(control, category) {
  if (control.kind === "buttons") {
    await control.buttons[category.toLowerCase()].click();
    return;
  }
  const requested = category.toLowerCase();
  const match = control.options.find((option) => option.label.toLowerCase() === requested)
    ?? (requested === "all" ? control.options.find((option) => /^(?:all categories|any)$/.test(option.label.toLowerCase())) : undefined);
  if (!match) throw new ProductFailure(`Filter does not offer ${category}`, { options: control.options });
  await control.locator.selectOption({ value: match.value });
}

function expense(token, suffix, amount, category, date) {
  return {
    description: `Oracle ${token} ${suffix}`,
    amount,
    category,
    date,
  };
}

const PRODUCT_CHECKS = {
  async BOOT_FORM({ page }) {
    const form = await discoverExpenseForm(page, { requireAccessibleNames: true });
    const keyboard = await verifyKeyboardReachability(page, {
      description: form.description,
      amount: form.amount,
      category: form.category,
      date: form.date,
      submit: form.submit,
    });
    return {
      controls: Object.fromEntries(Object.entries(form.descriptors).map(([name, descriptor]) => [name, {
        tag: descriptor.tag,
        type: descriptor.type,
        labels: descriptor.labels,
        labelledBy: descriptor.labelledBy,
        ariaLabel: descriptor.ariaLabel,
        title: descriptor.title,
        text: descriptor.text,
      }])),
      keyboard,
    };
  },

  async ADD_FIELDS({ page, token }) {
    const item = expense(token, "field proof", 42.37, "Food", "2026-05-20");
    await fillExpense(page, item);
    const record = await findRecordContainer(page, item.description, { includes: [item.category] });
    if (!amountAppears(record.text, item.amount)) {
      throw new ProductFailure("Added expense does not render its submitted amount", { observed: record.text, expected: item.amount });
    }
    if (!dateAppears(record.text, item.date)) {
      throw new ProductFailure("Added expense does not render its submitted date", { observed: record.text, expected: item.date });
    }
    return { submitted: item, observedRecord: record.text };
  },

  async DELETE_DURABLE({ page, token, baseUrl }) {
    const target = expense(token, "delete target", 11.41, "Food", "2026-04-11");
    const survivor = expense(token, "delete survivor", 22.52, "Travel", "2026-04-12");
    // Insert the survivor first and the target second.  This ordering is
    // deliberate: an implementation whose row buttons are all accidentally
    // hard-wired to expenses[0] used to pass when the target was inserted
    // first.  Acting on the second record proves the control is bound to the
    // record the user selected, not merely to a convenient array position.
    await fillExpense(page, survivor);
    await fillExpense(page, target);
    await clickDelete(page, target.description, [survivor.description]);
    await pollUntil(
      async () => (await visibleTextCount(page, target.description)) === 0,
      "Deleted expense remained visible",
    );
    if ((await visibleTextCount(page, survivor.description)) === 0) {
      throw new ProductFailure("Deleting one expense also removed the survivor");
    }
    await navigateToCanonicalProduct(page, baseUrl);
    await pollUntil(
      async () => (await visibleTextCount(page, survivor.description)) > 0,
      "Surviving expense did not hydrate after canonical navigation",
    );
    await assertRemainsAbsent(page, target.description);
    return { deleted: target.description, survivor: survivor.description, durableAfterReload: true };
  },

  async RECONCILE_DURABLE({ page, token, baseUrl }) {
    const target = expense(token, "reconcile target", 33.63, "Food", "2026-03-13");
    const survivor = expense(token, "reconcile survivor", 44.74, "Travel", "2026-03-14");
    // Keep the target away from index zero for the same record-binding proof as
    // DELETE_DURABLE.  A target-local control must update this second record.
    await fillExpense(page, survivor);
    await fillExpense(page, target);

    let targetRecord = await findRecordContainer(page, target.description, { action: "reconcile", excludes: [survivor.description] });
    let survivorRecord = await findRecordContainer(page, survivor.description, { action: "reconcile", excludes: [target.description] });
    const beforeTarget = await reconcileState(targetRecord.container, targetRecord.action);
    const beforeSurvivor = await reconcileState(survivorRecord.container, survivorRecord.action);
    if (beforeTarget.value) {
      throw new ProductFailure("A newly added expense starts reconciled; the user cannot demonstrate marking it reconciled", { state: beforeTarget });
    }

    await targetRecord.action.click();
    await pollUntil(async () => {
      targetRecord = await findRecordContainer(page, target.description, { action: "reconcile", excludes: [survivor.description] });
      return (await reconcileState(targetRecord.container, targetRecord.action)).value === true;
    }, "Reconcile action did not expose a reconciled state");
    const afterTarget = await reconcileState(targetRecord.container, targetRecord.action);
    survivorRecord = await findRecordContainer(page, survivor.description, { action: "reconcile", excludes: [target.description] });
    const afterSurvivor = await reconcileState(survivorRecord.container, survivorRecord.action);
    if (afterSurvivor.value !== beforeSurvivor.value) {
      throw new ProductFailure("Reconciling the target also changed the survivor", { beforeSurvivor, afterSurvivor });
    }

    await navigateToCanonicalProduct(page, baseUrl);
    await pollUntil(
      async () => (await visibleTextCount(page, target.description)) > 0
        && (await visibleTextCount(page, survivor.description)) > 0,
      "Reconcile records did not hydrate after canonical navigation",
    );
    await pollUntil(async () => {
      targetRecord = await findRecordContainer(page, target.description, { action: "reconcile", excludes: [survivor.description] });
      return (await reconcileState(targetRecord.container, targetRecord.action)).value === true;
    }, "Reconciled state did not hydrate after canonical navigation");
    survivorRecord = await findRecordContainer(page, survivor.description, { action: "reconcile", excludes: [target.description] });
    const reloadedTarget = await reconcileState(targetRecord.container, targetRecord.action);
    const reloadedSurvivor = await reconcileState(survivorRecord.container, survivorRecord.action);
    if (!reloadedTarget.value) {
      throw new ProductFailure("Reconciled state was lost after page refresh", { afterTarget, reloadedTarget });
    }
    if (reloadedSurvivor.value !== beforeSurvivor.value) {
      throw new ProductFailure("Survivor reconcile state changed after page refresh", { beforeSurvivor, reloadedSurvivor });
    }
    return { beforeTarget, afterTarget, reloadedTarget, survivor: { before: beforeSurvivor, after: afterSurvivor, reloaded: reloadedSurvivor } };
  },

  async FILTER({ page, token }) {
    const food = expense(token, "filter alpha", 55.85, "Food", "2026-02-15");
    const travel = expense(token, "filter beta", 66.96, "Travel", "2026-02-16");
    await fillExpense(page, food);
    await fillExpense(page, travel);
    const control = await filterControl(page);

    await applyFilter(control, "Food");
    await pollUntil(async () => (await visibleTextCount(page, food.description)) > 0
      && (await visibleTextCount(page, travel.description)) === 0, "Food filter did not isolate Food expenses");

    await applyFilter(control, "Travel");
    await pollUntil(async () => (await visibleTextCount(page, travel.description)) > 0
      && (await visibleTextCount(page, food.description)) === 0, "Travel filter did not isolate Travel expenses");

    await applyFilter(control, "All");
    await pollUntil(async () => (await visibleTextCount(page, food.description)) > 0
      && (await visibleTextCount(page, travel.description)) > 0, "All filter did not restore both categories");
    return { categoriesTested: ["Food", "Travel", "All"], controlKind: control.kind };
  },

  async PERSIST_ADD({ page, token, baseUrl }) {
    const item = expense(token, "persistent add", 77.17, "Food", "2026-01-17");
    await fillExpense(page, item);
    await navigateToCanonicalProduct(page, baseUrl);
    await pollUntil(
      async () => (await visibleTextCount(page, item.description)) > 0,
      "Added expense did not hydrate after canonical navigation",
    );
    return { description: item.description, durableAfterReload: true };
  },
};

async function openProductPage(context, baseUrl, diagnostics) {
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") diagnostics.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => diagnostics.pageErrors.push(serializeError(error)));
  page.on("requestfailed", (request) => diagnostics.requestFailures.push({
    method: request.method(),
    url: request.url(),
    failure: request.failure()?.errorText ?? "unknown",
  }));
  page.on("dialog", (dialog) => void dialog.accept().catch(() => undefined));
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("body").waitFor({ state: "visible" });
  return page;
}

function looksLikeInfrastructureError(error) {
  if (error instanceof InfrastructureFailure) return true;
  const message = String(error?.message ?? error).toLowerCase();
  return [
    "executable doesn't exist",
    "browser has been closed",
    "browser.newcontext",
    "target page, context or browser has been closed",
    "failed to launch",
  ].some((fragment) => message.includes(fragment));
}

async function runCheck({ browser, baseUrl, artifacts, timeoutMs, name, token }) {
  const startedAt = new Date();
  const diagnostics = { consoleErrors: [], pageErrors: [], requestFailures: [] };
  let context;
  let page;
  try {
    context = await browser.newContext({
      locale: "en-US",
      timezoneId: "UTC",
      reducedMotion: "reduce",
      serviceWorkers: "block",
      viewport: { width: 1440, height: 1000 },
    });
    context.setDefaultTimeout(timeoutMs);
    context.setDefaultNavigationTimeout(timeoutMs);
    const productOrigin = new URL(baseUrl).origin;
    await context.route("**/*", async (route) => {
      const requestUrl = new URL(route.request().url());
      if (["data:", "blob:"].includes(requestUrl.protocol) || requestUrl.origin === productOrigin) {
        await route.continue();
      } else {
        await route.abort("blockedbyclient");
      }
    });
    await context.routeWebSocket("**/*", async (webSocketRoute) => {
      await webSocketRoute.close({ code: 1008, reason: "Oracle blocks WebSocket dependencies" });
    });
    page = await openProductPage(context, baseUrl, diagnostics);
    const evidence = await PRODUCT_CHECKS[name]({ page, token, baseUrl });
    return {
      name,
      status: "pass",
      startedAt: startedAt.toISOString(),
      durationMs: Date.now() - startedAt.getTime(),
      evidence,
      diagnostics,
    };
  } catch (error) {
    const classification = looksLikeInfrastructureError(error) ? "infrastructure" : "product";
    let screenshot;
    if (page && !page.isClosed()) {
      await fs.promises.mkdir(artifacts, { recursive: true });
      screenshot = path.join(artifacts, `${name.toLowerCase()}.png`);
      await page.screenshot({ path: screenshot, fullPage: true }).catch(() => {
        screenshot = undefined;
      });
    }
    return {
      name,
      status: classification === "infrastructure" ? "infra-error" : "fail",
      startedAt: startedAt.toISOString(),
      durationMs: Date.now() - startedAt.getTime(),
      error: serializeError(error),
      ...(screenshot ? { screenshot } : {}),
      diagnostics,
    };
  } finally {
    await context?.close().catch(() => undefined);
  }
}

async function writeEvidence(outputPath, result) {
  if (!outputPath) return;
  await fs.promises.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.promises.writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
}

async function execute(options) {
  const startedAt = new Date();
  const indexPath = path.join(options.dist, "index.html");
  const result = {
    schemaVersion: 1,
    oracle: "expense-tracker-black-box",
    oracleVersion: ORACLE_VERSION,
    status: "infra-error",
    exitCode: 2,
    startedAt: startedAt.toISOString(),
    finishedAt: undefined,
    durationMs: undefined,
    input: {
      dist: options.dist,
      indexSha256: undefined,
      timeoutMs: options.timeoutMs,
    },
    runtime: {
      node: process.version,
      platform: `${process.platform}-${process.arch}`,
      browser: undefined,
    },
    isolation: {
      sourceInspected: false,
      storageInspected: false,
      network: "loopback-origin-only",
      webSockets: "blocked",
      server: "oracle-owned-ephemeral-loopback",
      browserContext: "fresh-per-check",
    },
    checks: [],
    infrastructureErrors: [],
  };

  let server;
  let browser;
  try {
    server = await startStaticServer(options.dist);
    result.input.indexSha256 = await sha256File(server.indexPath).catch((error) => {
      throw new InfrastructureFailure(`Could not read contained production entry point: ${indexPath}`, serializeError(error));
    });
    try {
      browser = await chromium.launch({ headless: true });
    } catch (error) {
      throw new InfrastructureFailure(
        "Chromium could not launch. Install the repository's pinned Playwright browser before running a paid matrix.",
        serializeError(error),
      );
    }
    result.runtime.browser = await browser.version();

    const token = crypto.randomBytes(4).toString("hex");
    for (const name of CHECKS) {
      const check = await runCheck({
        browser,
        baseUrl: server.url,
        artifacts: options.artifacts,
        timeoutMs: options.timeoutMs,
        name,
        token,
      });
      result.checks.push(check);
      if (check.status === "infra-error") break;
    }

    if (server.serverErrors.length) {
      result.infrastructureErrors.push(...server.serverErrors);
    }
    const hasInfrastructureError = result.infrastructureErrors.length > 0
      || result.checks.some((check) => check.status === "infra-error");
    const hasProductFailure = result.checks.some((check) => check.status === "fail");
    const complete = result.checks.length === CHECKS.length;
    if (hasInfrastructureError || !complete) {
      result.status = "infra-error";
      result.exitCode = 2;
    } else if (hasProductFailure) {
      result.status = "fail";
      result.exitCode = 1;
    } else {
      result.status = "pass";
      result.exitCode = 0;
    }
  } catch (error) {
    result.infrastructureErrors.push(serializeError(error));
    result.status = "infra-error";
    result.exitCode = 2;
  } finally {
    await browser?.close().catch(() => undefined);
    await server?.close().catch(() => undefined);
    result.finishedAt = new Date().toISOString();
    result.durationMs = Date.now() - startedAt.getTime();
  }
  return result;
}

let options;
let result;
try {
  options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(`${usage()}\n`);
    process.exitCode = 0;
  } else {
    result = await execute(options);
    try {
      await writeEvidence(options.evidence, result);
    } catch (error) {
      result.status = "infra-error";
      result.exitCode = 2;
      result.infrastructureErrors.push(serializeError(new InfrastructureFailure(
        `Could not write evidence file: ${options.evidence}`,
        serializeError(error),
      )));
    }
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exitCode = result.exitCode;
  }
} catch (error) {
  result = {
    schemaVersion: 1,
    oracle: "expense-tracker-black-box",
    oracleVersion: ORACLE_VERSION,
    status: "infra-error",
    exitCode: 2,
    infrastructureErrors: [serializeError(error)],
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exitCode = 2;
}
