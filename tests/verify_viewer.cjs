#!/usr/bin/env node
'use strict';

// Run from any directory: node work/verify_integrated_viewer.cjs [viewer URL].
// Inspects actual animated Object3D positions exposed by studentFlowInspect().
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');

const root = path.resolve(__dirname, '..');
const qa = process.env.CAMPUS_QA_DIR || fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'campus-viewer-qa-'));
const target = process.argv[2] || 'http://127.0.0.1:8768/?count=1000&floor=0&paused=0';
const report = { startedAt: new Date().toISOString(), target, checks: [], console: [], pageErrors: [], failedRequests: [], responses: [], status: 'NOT READY' };
fs.mkdirSync(qa, { recursive: true });
let browser, page;

function libraryEntries(inspect) {
  assert.ok(Array.isArray(inspect.library), 'studentFlowInspect().library must contain actual object snapshots');
  return inspect.library;
}
function visibleEntries(inspect) { return libraryEntries(inspect).filter(a => a.visible); }
function distance(a, b) { return Math.hypot(...a.map((n, i) => n - b[i])); }
async function snapshot() {
  return page.evaluate(() => ({
    status: structuredClone(window.studentFlowStatus),
    inspect: window.studentFlowInspect(),
    diagnostics: structuredClone(window.studentFlowDiagnostics),
    playLabel: document.getElementById('play').textContent,
    timeline: { value: Number(document.getElementById('timeline').value), max: Number(document.getElementById('timeline').max) }
  }));
}
async function step(name, callback) {
  const entry = { name, startedAt: new Date().toISOString() };
  report.checks.push(entry);
  try { entry.evidence = await callback(); entry.result = 'PASS'; }
  catch (error) { entry.result = 'FAIL'; entry.error = error.stack || String(error); }
  console.log(JSON.stringify({ name, result: entry.result, error: entry.error }));
}
async function stableFrame() {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function pause() {
  if ((await page.locator('#play').innerText()).includes('暂停')) await page.locator('#play').click();
  await stableFrame();
}
async function seek(time) {
  await page.locator('#timeline').evaluate((el, value) => {
    el.value = String(value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, time);
  await stableFrame();
}
async function screenshot(name) {
  const file = path.join(qa, name + '.png');
  await page.screenshot({ path: file, fullPage: true, timeout: 45000 });
  return path.relative(root, file);
}

(async () => {
  try {
    browser = await chromium.launch({
      executablePath: process.env.CHROME_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      headless: true,
      args: ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']
    });
    page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
    page.setDefaultTimeout(45000);
    page.on('console', message => report.console.push({ type: message.type(), text: message.text() }));
    page.on('pageerror', error => report.pageErrors.push(error.stack || String(error)));
    page.on('requestfailed', request => report.failedRequests.push({ url: request.url(), failure: request.failure() }));
    page.on('response', response => {
      if (response.status() >= 400) report.responses.push({ url: response.url(), status: response.status() });
    });
    await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForFunction(() => window.studentFlowReady === true && typeof window.studentFlowInspect === 'function', null, { timeout: 120000 });

    await step('campus_initial_1000', async () => {
      const value = await snapshot();
      assert.equal(value.status.floor, 0);
      assert.equal(value.status.crowdCount, 1000);
      assert.equal(value.status.libraryAnimation.source, 'Students_Animated.glb');
      assert.equal(value.status.libraryAnimation.clip, '_defaultAnim_');
      assert.equal(value.status.libraryAnimation.agents, 120);
      assert.equal(libraryEntries(value.inspect).length, 120);
      assert.equal(visibleEntries(value.inspect).length, 0, 'indoor animated objects must be hidden in campus mode');
      assert.equal(new Set(libraryEntries(value.inspect).map(a => a.id)).size, 120);
      return { ...value, screenshot: await screenshot('01-campus-1000') };
    });

    await step('original_library_animation_moves', async () => {
      await page.locator('#libraryTour').click();
      await stableFrame();
      const before = await snapshot();
      assert.equal(before.status.floor, 2);
      assert.ok(Math.abs(before.status.time - 60) < 5, 'library tour must open around 60 seconds');
      assert.ok(before.playLabel.includes('暂停'), 'library tour must start playback');
      await page.waitForTimeout(4000);
      const after = await snapshot();
      const baseline = new Map(libraryEntries(before.inspect).map(a => [a.id, a]));
      const movements = visibleEntries(after.inspect).map(a => ({
        id: a.id,
        floor: a.floor,
        before: baseline.get(a.id)?.position,
        after: a.position,
        displacement: baseline.has(a.id) ? distance(a.position, baseline.get(a.id).position) : 0
      })).filter(a => a.displacement > 0.01);
      assert.ok(after.status.time > before.status.time, 'playback clock must advance');
      assert.ok(movements.length > 0, 'at least one actual original GLB root must change world position within five seconds');
      assert.ok(after.status.libraryAnimation.time > before.status.libraryAnimation.time, 'AnimationMixer time must advance');
      return { before, after, movements, screenshot: await screenshot('02-library-tour') };
    });

    await step('five_floor_visibility', async () => {
      await pause();
      await seek(230);
      const floors = [];
      for (let floor = 1; floor <= 5; floor++) {
        await page.locator('#floor').selectOption(String(floor));
        await stableFrame();
        const value = await snapshot();
        const visible = visibleEntries(value.inspect);
        assert.equal(value.status.floor, floor);
        assert.ok(visible.length > 0, `${floor}F must show actual students at 230 seconds`);
        assert.ok(visible.every(a => Number(a.floor) === floor), `${floor}F must hide all other floors' students`);
        floors.push({ floor, visibleIds: visible.map(a => a.id), snapshot: value });
      }
      return { floors, screenshot: await screenshot('03-library-5f') };
    });

    await step('whole_library_all_floors', async () => {
      await page.locator('#libraryAll').click();
      await stableFrame();
      const value = await snapshot();
      assert.equal(value.status.floor, -1);
      const visible = visibleEntries(value.inspect);
      const floorCounts = {};
      for (const entry of visible) floorCounts[entry.floor] = (floorCounts[entry.floor] || 0) + 1;
      for (let floor = 1; floor <= 5; floor++) assert.ok(floorCounts[floor] > 0, `whole-library mode must show ${floor}F`);
      assert.equal(new Set(visible.map(a => a.id)).size, visible.length, 'whole-library mode must not duplicate roots');
      return { ...value, floorCounts, screenshot: await screenshot('04-library-all') };
    });

    await step('paused_seek_matches_mixer_and_objects', async () => {
      await pause();
      await seek(65);
      const first = await snapshot();
      await page.waitForTimeout(700);
      const held = await snapshot();
      assert.ok(Math.abs(first.status.time - 65) < 0.2);
      assert.ok(Math.abs(first.status.libraryAnimation.time - 65) < 0.2);
      assert.ok(Math.abs(held.status.time - first.status.time) < 0.001, 'paused time must hold');
      assert.ok(Math.abs(held.status.libraryAnimation.time - first.status.libraryAnimation.time) < 0.001, 'paused animation must hold');
      const prior = new Map(libraryEntries(first.inspect).map(a => [a.id, a]));
      assert.ok(libraryEntries(held.inspect).every(a => distance(a.position, prior.get(a.id).position) < 0.00001), 'actual object roots must hold while paused');
      await seek(95);
      const second = await snapshot();
      assert.ok(Math.abs(second.status.time - 95) < 0.2);
      assert.ok(Math.abs(second.status.libraryAnimation.time - 95) < 0.2);
      const changes = libraryEntries(second.inspect).map(a => ({ id: a.id, displacement: distance(a.localPosition, prior.get(a.id).localPosition) })).filter(a => a.displacement > 0.01);
      assert.ok(changes.length > 0, 'seeking must change actual original GLB positions');
      return { first, held, second, changes };
    });

    await step('playback_loops_at_end', async () => {
      await pause();
      const beforeSeek = await snapshot();
      const end = beforeSeek.timeline.max;
      assert.ok(end > 1000, 'timeline must cover original library animation');
      await seek(end - 0.15);
      await page.locator('#speed').selectOption('5');
      await page.locator('#play').click();
      await page.waitForTimeout(1600);
      const after = await snapshot();
      assert.ok(after.status.time < end - 1, 'time must wrap rather than stop at the final frame');
      assert.ok(after.playLabel.includes('暂停'), 'loop must remain playing');
      assert.ok(after.status.libraryAnimation.time < end - 1, 'original GLB mixer must loop with the global clock');
      await page.locator('#speed').selectOption('1');
      return { end, after };
    });

    await step('return_to_campus_hides_library_without_duplicates', async () => {
      await page.locator('#floor').selectOption('0');
      await stableFrame();
      const value = await snapshot();
      assert.equal(value.status.floor, 0);
      assert.equal(value.status.crowdCount, 1000);
      assert.equal(visibleEntries(value.inspect).length, 0);
      assert.equal(libraryEntries(value.inspect).length, 120);
      assert.equal(new Set(libraryEntries(value.inspect).map(a => a.id)).size, 120);
      assert.equal(Number(value.status.libraryVisible), 0);
      return { ...value, screenshot: await screenshot('05-return-campus') };
    });

    await step('runtime_errors', async () => {
      const value = await snapshot();
      assert.equal(report.pageErrors.length, 0, 'no uncaught browser errors');
      assert.equal(value.diagnostics?.errors?.length || 0, 0, 'no application runtime errors');
      const failedAssets = report.responses.filter(r => !r.url.endsWith('/favicon.ico'));
      assert.equal(failedAssets.length, 0, 'all model/script/data requests must succeed');
      assert.equal(report.failedRequests.length, 0, 'no failed browser requests');
      return { diagnostics: value.diagnostics, ignoredFavicon: report.responses.filter(r => r.url.endsWith('/favicon.ico')) };
    });
  } catch (error) {
    report.fatal = error.stack || String(error);
    console.error(report.fatal);
  } finally {
    if (page) {
      try { report.final = await snapshot(); } catch (error) { report.finalSnapshotError = String(error); }
    }
    report.finishedAt = new Date().toISOString();
    report.status = !report.fatal && report.checks.length === 8 && report.checks.every(c => c.result === 'PASS') ? 'PASS' : 'NOT READY';
    fs.writeFileSync(path.join(qa, 'browser-validation.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify({ status: report.status, report: path.join(qa, 'browser-validation.json') }));
    if (browser) await browser.close();
    process.exitCode = report.status === 'PASS' ? 0 : 1;
  }
})();
