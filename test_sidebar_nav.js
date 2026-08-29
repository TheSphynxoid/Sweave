// Regression: sidebar nav in welcome mode (no active project) + project mode guard.
const { chromium } = require('playwright-core');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8100';

async function toastVisibleWith(page, text) {
  return page.evaluate((t) => {
    const c = document.getElementById('toasts');
    if (!c) return false;
    return Array.from(c.children).some(el => el.textContent.includes(t) && !!el.offsetParent);
  }, text);
}

async function panelState(page, tab) {
  return page.evaluate((t) => {
    const el = document.getElementById('tab-' + t);
    const w = document.getElementById('welcome');
    return el ? { visible: !!el.offsetParent, hidden: el.classList.contains('hidden'), welcomeVisible: !!w.offsetParent } : null;
  }, tab);
}

async function navOpacity(page, tab) {
  return page.evaluate((t) => {
    const el = document.querySelector(`.nav-item[data-tab="${t}"]`);
    return el ? parseFloat(getComputedStyle(el).opacity) : null;
  }, tab);
}

let failures = 0;
function check(name, cond, detail) {
  if (!cond) failures++;
  console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`);
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage();
  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push(String(e)));

  // ---------- WELCOME MODE (active_project = null, ensured by orchestrator) ----------
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(1200);

  const boot = await page.evaluate(() => {
    const w = document.getElementById('welcome');
    return { welcomeVisible: !!w && !!w.offsetParent };
  });
  check('boot: welcome screen shown', boot.welcomeVisible);

  // 1. Click CHAT in welcome mode -> toast, stay on welcome, no panel
  await page.click('.nav-item[data-tab="chat"]');
  await page.waitForTimeout(300);
  let s = await panelState(page, 'chat');
  check('welcome+chat: stays on welcome', s.welcomeVisible, JSON.stringify(s));
  check('welcome+chat: chat panel NOT shown', !s.visible);
  check('welcome+chat: toast "Open a project first"', await toastVisibleWith(page, 'Open a project first'));
  check('welcome+chat: chat nav dimmed', (await navOpacity(page, 'chat')) < 1);
  check('welcome+chat: settings nav not dimmed', (await navOpacity(page, 'settings')) === 1);

  // 2. Click SETTINGS in welcome mode -> settings panel shows
  await page.click('.nav-item[data-tab="settings"]');
  await page.waitForTimeout(300);
  s = await panelState(page, 'settings');
  check('welcome+settings: settings panel visible', s.visible && !s.hidden, JSON.stringify(s));
  check('welcome+settings: welcome hidden', !s.welcomeVisible);

  // 3. Click AGENTS in welcome mode -> back to welcome + toast
  await page.click('.nav-item[data-tab="agents"]');
  await page.waitForTimeout(300);
  s = await panelState(page, 'agents');
  check('welcome+agents: returns to welcome', s.welcomeVisible, JSON.stringify(s));
  check('welcome+agents: settings panel hidden again', !(await panelState(page, 'settings')).visible);
  check('welcome+agents: toast shown', await toastVisibleWith(page, 'Open a project first'));

  // ---------- PROJECT MODE (activate via API, reload) ----------
  const res = await fetch(BASE + '/api/projects/api-project/active', { method: 'POST' });
  if (res.status !== 200) { console.log('FATAL: cannot activate project'); process.exit(1); }
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(1200);

  for (const tab of ['chat', 'children', 'agents', 'memory', 'settings']) {
    await page.click(`.nav-item[data-tab="${tab}"]`);
    await page.waitForTimeout(250);
    const st = await page.evaluate((t) => {
      const el = document.getElementById('tab-' + t);
      const others = {};
      document.querySelectorAll('.nav-item').forEach(b => {
        const p = document.getElementById('tab-' + b.dataset.tab);
        others[b.dataset.tab] = p ? !!p.offsetParent : null;
      });
      return el ? { visible: !!el.offsetParent, hidden: el.classList.contains('hidden'), others } : null;
    }, tab);
    const ok = st && st.visible && !st.hidden &&
      Object.entries(st.others).every(([k, v]) => k === tab ? v : !v);
    check(`project-mode: ${tab} switches`, ok, JSON.stringify(st));
  }

  check('no page errors', pageErrors.length === 0, pageErrors.join(' | '));
  console.log(failures === 0 ? 'ALL GREEN' : `RED: ${failures} failure(s)`);
  await browser.close();
  process.exit(failures === 0 ? 0 : 1);
})();

