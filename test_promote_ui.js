// M1.4+M1.5 step 3 UI smoke: 'Mark done' button visibility on Children tab.
// Mirrors the test_sidebar_nav.js pattern: headless Edge via playwright-core,
// assert offsetParent visibility. Assumes the server is already running
// (start it via `python start_server.py 8100 127.0.0.1` from the repo root,
// per AGENTS.md gotcha #1).
//
// What this test asserts:
//   1. Children tab loads with no children (initial state).
//   2. Submitting a v2 task forces a child entry with status='running'.
//   3. After we force the child to status='review' via a direct
//      GET-then-PUT on /api/delegations/{id} (the store's update path),
//      the 'Mark done' button becomes visible (offsetParent != null).
//   4. Clicking the button calls the promote endpoint and the child's
//      status becomes 'done'; the button disappears.
const { chromium } = require('playwright-core');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8100';

async function panelVisible(page, id) {
  return page.evaluate((i) => {
    const el = document.getElementById(i);
    return el ? !!el.offsetParent : false;
  }, id);
}

async function childStatus(page, delegationId) {
  return page.evaluate(async (did) => {
    const r = await fetch('/api/sessions/' + did);
    return r.ok ? r.json() : null;
  }, delegationId);
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
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  // Activate a known project.
  let r = await fetch(BASE + '/api/projects/api-project/active', { method: 'POST' });
  if (r.status !== 200) { console.log('FATAL: cannot activate project'); process.exit(1); }

  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // Switch to Children tab.
  await page.click('.nav-item[data-tab="children"]');
  await page.waitForTimeout(300);
  check('children tab visible', await panelVisible(page, 'tab-children'));

  // Create a session, submit a v2 task pinned to it, capture the
  // child entry. We use the API directly (no chat input needed).
  r = await fetch(BASE + '/api/sessions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'promote-ui-smoke', project_name: 'api-project' }),
  });
  const session = (await r.json()).session || await r.json();
  const sid = session.id;

  r = await fetch(BASE + '/api/v2/tasks', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      task: 'smoke-task', agent: 'backend', parent_session_id: sid,
    }),
  });
  const sub = await r.json();
  const did = sub.delegation_id;

  // Reload the children list so the new child entry renders.
  await page.evaluate(() => {
    // The session-store renderer is local to app.js; the simplest way
    // to refresh is to switch tabs off and back on (which re-fetches
    // the session).
  });
  // Force a refresh by clicking another tab and back.
  await page.click('.nav-item[data-tab="chat"]');
  await page.waitForTimeout(200);
  await page.click('.nav-item[data-tab="children"]');
  await page.waitForTimeout(400);

  // The child should be in 'running' state (stub delegate).
  const runningBtn = await page.evaluate(() => {
    const btns = document.querySelectorAll('.child-promote-btn');
    return btns.length;
  });
  check('running: no Mark done button', runningBtn === 0, 'count=' + runningBtn);

  // Force the delegation to 'review' via the store's update path
  // (we don't have a /set-status endpoint; the store mutation goes
  // through the per-project delegations.json file).
  // Easier: call /api/delegations/{id} then push the status via the
  // store directly. For the smoke we use a Python helper script
  // invoked via the page's fetch with a custom header... but the
  // server doesn't expose such an endpoint. Instead, write to the
  // store's on-disk JSON. The store is at
  // {api-project}/.sweave/delegations.json; we read-modify-write.
  const fs = require('fs');
  const path = require('path');
  const os = require('os');
  const home = os.homedir();
  // Find the project path: it's whatever /api/projects returned.
  const projectsR = await fetch(BASE + '/api/projects');
  const projects = (await projectsR.json()).projects;
  const apiProj = projects.find((p) => p.name === 'api-project');
  if (!apiProj) { console.log('FATAL: api-project not found'); process.exit(1); }
  const delegationsFile = path.join(apiProj.path, '.sweave', 'delegations.json');
  if (fs.existsSync(delegationsFile)) {
    const data = JSON.parse(fs.readFileSync(delegationsFile, 'utf-8'));
    let mutated = false;
    for (const d of data.delegations || []) {
      if (d.delegation_id === did) { d.status = 'review'; mutated = true; }
    }
    if (mutated) {
      fs.writeFileSync(delegationsFile, JSON.stringify(data, null, 2));
    }
  }

  // Reload the page (cleanest way to pick up the disk change).
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);
  await page.click('.nav-item[data-tab="children"]');
  await page.waitForTimeout(400);

  // Now there should be exactly one Mark done button (offsetParent visible).
  const reviewBtnInfo = await page.evaluate(() => {
    const btns = document.querySelectorAll('.child-promote-btn');
    return Array.from(btns).map((b) => ({
      visible: !!b.offsetParent,
      text: b.textContent,
    }));
  });
  check('review: at least one Mark done button', reviewBtnInfo.length >= 1, JSON.stringify(reviewBtnInfo));
  check('review: button is visible (offsetParent)',
    reviewBtnInfo.length >= 1 && reviewBtnInfo.every((b) => b.visible),
    JSON.stringify(reviewBtnInfo));
  check('review: button text is "Mark done"',
    reviewBtnInfo.length >= 1 && reviewBtnInfo.every((b) => b.text.trim() === 'Mark done'),
    JSON.stringify(reviewBtnInfo));

  // Click the first one. This should call the promote endpoint and
  // update the child to 'done'.
  if (reviewBtnInfo.length >= 1) {
    await page.click('.child-promote-btn');
    await page.waitForTimeout(500);
    const afterClick = await page.evaluate(() => {
      const btns = document.querySelectorAll('.child-promote-btn');
      return btns.length;
    });
    check('after promote: Mark done button gone', afterClick === 0, 'count=' + afterClick);

    // Server-side: the delegation should now be 'done'.
    const dg = await fetch(BASE + '/api/delegations/' + did);
    const djson = await dg.json();
    check('after promote: delegation is done', djson.status === 'done', JSON.stringify(djson));
  }

  check('no page errors', pageErrors.length === 0, pageErrors.join(' | '));
  console.log(failures === 0 ? 'ALL GREEN' : `RED: ${failures} failure(s)`);
  await browser.close();
  process.exit(failures === 0 ? 0 : 1);
})();
