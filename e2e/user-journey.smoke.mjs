/**
 * Foundry user-journey smoke E2E — the layer that was missing.
 *
 * Every UI unit test mocks the bridge; nothing walked the real cockpit, so
 * journey-breaking bugs (#50 chat wipe, #51 folder picker, #52 model race)
 * shipped "green". This drives the ACTUAL built UI (dist) through the launch →
 * onboarding → app-boot journey with a faithful window.__TAURI__ mock and
 * asserts each stage, so a dead button / stuck onboarding / silent failure
 * can't reach a release again.
 *
 * Run:  node e2e/user-journey.smoke.mjs
 * Needs: `npm run build` first (serves ./dist), and Playwright's chromium
 *        (PLAYWRIGHT_BROWSERS_PATH may point at a shared browser cache).
 * Exit:  0 = all journey assertions pass, 1 = a stage broke.
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = normalize(join(dirname(fileURLToPath(import.meta.url)), '..'));
const DIST = join(ROOT, 'dist');
const MIME = { '.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css',
  '.json':'application/json','.woff':'font/woff','.woff2':'font/woff2','.svg':'image/svg+xml',
  '.png':'image/png','.ico':'image/x-icon','.map':'application/json' };

const server = createServer(async (req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]); if (p === '/') p = '/index.html';
  let body, type;
  try { body = await readFile(join(DIST, p)); type = MIME[extname(p)] || 'application/octet-stream'; }
  catch { try { body = await readFile(join(DIST, 'index.html')); type = 'text/html'; } catch { body = null; } } // SPA fallback
  if (res.headersSent) return;
  if (body) { res.writeHead(200, { 'content-type': type }); res.end(body); }
  else { res.writeHead(404); res.end('not found'); }
});
await new Promise((r) => server.listen(0, r));
const port = server.address().port;

const fail = (msg) => { console.error('FAIL:', msg); failures.push(msg); };
const failures = [];
const browser = await chromium.launch();
const page = await browser.newPage();
const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', (e) => fail('pageerror: ' + e.message));

// Faithful v1-style __TAURI__ global (the app runs with withGlobalTauri:true).
await page.addInitScript(() => {
  const smart = (cmd) => {
    const c = String(cmd);
    if (/model/i.test(c)) return ['claude-sonnet-4-5', 'claude-opus-4-8'];
    // No active product workspace after onboarding (the honest state: onboarding
    // sets only the projects root). Must precede the generic /workspace/ match so
    // get_workspace_status still returns an object.
    if (c === 'get_workspace') return null;
    if (/list|history|projects|artifacts|secrets|audit/i.test(c)) return [];
    if (/status|state|workspace|identity|budget|cost|update|version/i.test(c)) return { ok: true };
    if (/store|clear|set_|save|test|restart|ensure|mkdir|watch/i.test(c)) return true;
    return null;
  };
  const invoke = async (cmd) => smart(cmd);
  window.__TAURI__ = {
    core: { invoke }, invoke,
    event: { listen: async () => (() => {}), emit: async () => {}, once: async () => (() => {}) },
    window: { getCurrentWindow: () => ({ listen: async () => () => {}, onCloseRequested: async () => () => {}, close(){}, minimize(){}, maximize(){}, isMaximized: async () => false }) },
    fs: { mkdir: async () => {}, exists: async () => true },
    shell: { open: async () => {} },
  };
});
await page.addInitScript(() => {
  // #51: the reliable dialog binding the app now uses.
  window.__mockDialogPath = 'C:/Users/foundry/Foundry Projects';
});

await page.goto(`http://localhost:${port}/index.html`, { waitUntil: 'networkidle', timeout: 20000 }).catch((e) => fail('goto: ' + e.message));
await page.waitForTimeout(1200);

// Stage 1 — the shell renders.
const rootLen = await page.evaluate(() => (document.getElementById('root')?.innerHTML || '').length);
if (rootLen < 500) fail(`shell did not render (root length ${rootLen})`);

// Stage 2 — walk onboarding to completion (provider -> key -> root -> Seal & start).
for (let step = 1; step <= 6; step++) {
  const anth = page.locator('[data-ai="anthropic"]');
  if (await anth.count() && await anth.first().isVisible().catch(() => false)) { await anth.first().click().catch(() => {}); await page.waitForTimeout(150); }
  const key = page.locator('#apiKey');
  if (await key.count() && await key.first().isVisible().catch(() => false)) { await key.first().fill('sk-ant-api03-smoke').catch(() => {}); }
  const root = page.locator('#identFolder');
  if (await root.count() && await root.first().isVisible().catch(() => false)) { await root.first().fill('C:/Users/foundry/Foundry Projects').catch(() => {}); }
  const seal = page.locator('button:has-text("Seal")');
  if (await seal.count() && await seal.first().isVisible().catch(() => false)) { await seal.first().click().catch((e) => fail('seal click: ' + e.message)); await page.waitForTimeout(2000); break; }
  await page.evaluate(() => { if (typeof window.nextStep === 'function') window.nextStep(); });
  await page.waitForTimeout(400);
}

// Stage 3 — onboarding completed and the app booted.
const appVisible = await page.locator('#app').isVisible().catch(() => false);
if (!appVisible) fail('app did not become visible after onboarding (stuck on onboarding)');

// Stage 4 — no console errors surfaced during the journey.
if (consoleErrors.length) fail('console errors during journey: ' + JSON.stringify(consoleErrors.slice(0, 5)));

// Stage 5 — the cockpit's major views all render (dead-view / broken-wiring guard).
// Onboarding lands on 'dashboard'; walk each nav target and assert the app shell
// still renders substantial content and surfaces no new console errors.
const errBefore5 = consoleErrors.length;
for (const view of ['build', 'dashboard', 'vault', 'settings', 'preview', 'build']) {
  await page.evaluate((v) => { if (typeof window.switchTab === 'function') window.switchTab(v); }, view).catch((e) => fail(`switchTab(${view}): ${e.message}`));
  await page.waitForTimeout(250);
  const len = await page.evaluate(() => (document.getElementById('app')?.innerHTML || '').length);
  if (len < 500) fail(`view '${view}' rendered too little (app length ${len})`);
}
if (consoleErrors.length > errBefore5) fail('console errors during view navigation: ' + JSON.stringify(consoleErrors.slice(errBefore5, errBefore5 + 5)));

// Stage 6 — #53: a build with no active project must GUIDE (open New Project),
// never dead-end in "No workspace selected". We're on the Build tab; type a
// delivery intent and send.
await page.evaluate(() => { if (typeof window.switchTab === 'function') window.switchTab('build'); });
await page.waitForTimeout(300);
const composer = page.locator('#chatInput');
if (await composer.count() && await composer.first().isVisible().catch(() => false)) {
  await composer.first().fill('build a task manager app').catch(() => {});
  await page.evaluate(() => { if (typeof window.sendMsg === 'function') window.sendMsg(); });
  await page.waitForTimeout(600);
  const appText = await page.evaluate(() => document.getElementById('app')?.textContent || '');
  if (!/no project open yet/i.test(appText)) fail('#53: build with no workspace did not surface the New-Project guidance');
  if (/no workspace selected/i.test(appText)) fail('#53: raw "No workspace selected" leaked to the user instead of guidance');
} else {
  fail('#53: composer (#chatInput) not found on the Build tab');
}

await browser.close();
server.close();

if (failures.length) { console.error(`\nJOURNEY SMOKE FAILED (${failures.length}).`); process.exit(1); }
console.log('JOURNEY SMOKE PASSED: shell renders, onboarding completes, app boots, all major views render, #53 no-workspace build guides to New Project, no console errors.');
