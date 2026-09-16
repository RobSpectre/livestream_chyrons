// Chyron overlay harness. Needs Chrome already listening on :9333 with the
// overlay loaded (see README "Verifying" section).
import { readFileSync, writeFileSync } from 'node:fs';

const CDP = process.env.CDP || 'http://127.0.0.1:9333';
const URL_MATCH = process.env.URL_MATCH || '127.0.0.1:8790';
const SHOT_DIR = process.env.SHOT_DIR || '/tmp';

// The OBS browser sources and this harness must agree on the banner canvas:
// read the size straight out of the script that configures OBS.
const OBS_SCRIPT = readFileSync(new URL('../scripts/obs_chyrons.py', import.meta.url), 'utf8');
const SOURCE_WIDTH = Number(/^SOURCE_WIDTH = (\d+)/m.exec(OBS_SCRIPT)[1]);
const SOURCE_HEIGHT = Number(/^SOURCE_HEIGHT = (\d+)/m.exec(OBS_SCRIPT)[1]);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, pass, detail = '') => {
  results.push({ name, pass });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`);
};

async function pickTarget(match = URL_MATCH, tries = 40) {
  for (let i = 0; i < tries; i++) {
    try {
      const list = await (await fetch(`${CDP}/json/list`)).json();
      const t = list.find((x) => x.type === 'page' && x.url.includes(match));
      if (t) return t;
    } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error(`no page target matching ${match}`);
}

class Client {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
    this.logs = []; this.errors = [];
  }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws failed')); });
    const c = new Client(ws);
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && c.pending.has(m.id)) {
        const { res, rej } = c.pending.get(m.id);
        c.pending.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method === 'Runtime.consoleAPICalled') {
        const txt = m.params.args.map((a) => a.value ?? a.description ?? a.type).join(' ');
        c.logs.push(`${m.params.type}: ${txt}`);
        if (m.params.type === 'error') c.errors.push(txt);
      } else if (m.method === 'Runtime.exceptionThrown') {
        const d = m.params.exceptionDetails;
        c.errors.push(`${d.text} ${d.exception?.description || ''} @${d.lineNumber}:${d.columnNumber}`);
      }
    };
    return c;
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res, rej) => this.pending.set(id, { res, rej }));
  }
  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(`${r.exceptionDetails.text} ${r.exceptionDetails.exception?.description || ''}`);
    return r.result.value;
  }
  async navigate(url, waitMs = 2500) {
    await this.send('Page.navigate', { url });
    await sleep(waitMs);
    // A navigation can resolve before the new document commits; wait for the
    // overlay to actually mount (tally mode mounts a transparent div) so
    // assertions never read the previous page.
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      const ready = await this.eval(
        `document.readyState === 'complete' && !!document.querySelector('#app > div')`,
      ).catch(() => false);
      if (ready) return;
      await sleep(200);
    }
    throw new Error(`overlay did not mount at ${url}`);
  }
  async shot(path) {
    const r = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    writeFileSync(path, Buffer.from(r.data, 'base64'));
    console.log('shot ->', path);
  }
}

// Reads the rendered banners: chips, capsule colours, geometry and type sizes.
const READ = `(() => {
  return [...document.querySelectorAll('[data-agent]')].map((card) => {
    const brand = card.querySelector('[data-role="brand"]');
    const state = card.querySelector('[data-role="state"]');
    const tiles = [...card.querySelectorAll('[data-metric]')];
    const cardStyle = getComputedStyle(card);
    const rects = tiles.map((t) => t.getBoundingClientRect());
    const gaps = rects.slice(1).map((r, i) => Math.round(r.left - rects[i].right));
    return {
      label: brand.textContent.trim(),
      key: card.getAttribute('data-agent'),
      brandBg: getComputedStyle(brand).backgroundColor,
      brandInk: getComputedStyle(brand).color,
      stateBg: getComputedStyle(state).backgroundColor,
      stateInk: getComputedStyle(state).color,
      stateTone: state.getAttribute('data-tone'),
      cardBg: cardStyle.backgroundColor,
      borderPx: Math.round(parseFloat(cardStyle.borderTopWidth)),
      shadow: cardStyle.boxShadow,
      radiusPx: Math.round(parseFloat(cardStyle.borderTopLeftRadius)),
      state: state.textContent.trim(),
      tiles: tiles.length,
      gapPx: Math.round(gaps.reduce((sum, g) => sum + g, 0) / Math.max(1, gaps.length)),
      text: card.textContent.replace(/\\s+/g, ' ').trim(),
      metrics: tiles.map((t) => {
        const label = t.querySelector('[data-role="label"]');
        const value = t.querySelector('[data-role="value"]');
        return {
          metric: t.getAttribute('data-metric'),
          title: label.textContent.trim(),
          value: value.textContent.trim(),
          fill: getComputedStyle(t).backgroundColor,
          ink: getComputedStyle(value).color,
          weight: getComputedStyle(value).fontWeight,
          // CSS pixels at this viewport; the OBS canvas scales these by 0.5.
          labelPx: Math.round(parseFloat(getComputedStyle(label).fontSize)),
          valuePx: Math.round(parseFloat(getComputedStyle(value).fontSize)),
          playerPx: Math.round(parseFloat(getComputedStyle(label).fontSize) * 0.5),
        };
      }),
    };
  });
})()`;

/** WCAG relative luminance and contrast, for the block-colour / ink pairs. */
const luminance = (color) => {
  const parts = color.match(/[0-9]+/g).slice(0, 3).map((value) => {
    const c = Number(value) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2];
};
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
/** Channel distance, used to prove no capsule is close to the key colour. */
const distance = (a, b) => {
  const one = a.match(/[0-9]+/g).slice(0, 3).map(Number);
  const two = b.match(/[0-9]+/g).slice(0, 3).map(Number);
  return Math.max(...one.map((value, index) => Math.abs(value - two[index])));
};

const client = await Client.connect((await pickTarget()).webSocketDebuggerUrl);
await client.send('Runtime.enable');
await client.send('Page.enable');
await client.send('Emulation.setDeviceMetricsOverride', { width: 3840, height: 2160, deviceScaleFactor: 1, mobile: false });
await sleep(400);
await client.navigate('http://127.0.0.1:8790/?bg=green');
await sleep(3000); // first telemetry frame

// The API payload is the reference the DOM is compared against below.
const api = await client.eval(`fetch('/api/telemetry',{cache:'no-store'}).then(r=>r.json())`);

// ---- layout ----
const banners = await client.eval(READ);
check('three banners render', banners.length === 3, `got ${banners.length}`);
check('banners are Codex/Claude/Hermes', banners.map((b) => b.label).join(',') === 'CODEX,CLAUDE,HERMES',
  banners.map((b) => b.label).join(','));
check('every banner carries six capsules', banners.every((b) => b.tiles === 6),
  banners.map((b) => b.tiles).join(','));
check('model and hour graph lead, then the four numbers',
  banners.every((b) => b.metrics.map((m) => m.title).join(',') === 'MODEL,1H TOK,TOK/S,CTX,SESS,30D'),
  banners[0]?.metrics.map((m) => m.title).join(','));
check('no quota capsule is rendered',
  (await client.eval(`document.querySelectorAll('[data-metric="quota"]').length`)) === 0);
check('the capsule row fills the card with no trailing gap',
  (await client.eval(`(() => {
    const cards = [...document.querySelectorAll('[data-agent]')];
    return cards.every((card) => {
      const grid = card.querySelector('[data-role="capsules"]');
      const cardBox = card.getBoundingClientRect();
      const gridBox = grid.getBoundingClientRect();
      const style = getComputedStyle(card);
      const inset = parseFloat(style.paddingLeft) + parseFloat(style.borderLeftWidth);
      return Math.abs(gridBox.left - cardBox.left - inset) <= 1 &&
        Math.abs(gridBox.right - (cardBox.right - inset)) <= 1;
    });
  })()`)) === true,
  'capsules span the card content box exactly');
check('capsules are laid out six across and reach the right edge',
  (await client.eval(`(() => {
    const grid = document.querySelector('[data-agent="codex"] [data-role="capsules"]');
    const tiles = [...grid.querySelectorAll('[data-metric]')];
    const rows = new Set(tiles.map((t) => Math.round(t.getBoundingClientRect().top)));
    const last = tiles[tiles.length - 1].getBoundingClientRect();
    const style = getComputedStyle(grid);
    const inset = parseFloat(style.paddingRight);
    return rows.size === 1 && Math.round(grid.getBoundingClientRect().right - inset - last.right) <= 1;
  })()`)) === true,
  'one row, last capsule flush with the row edge, no trailing gap');
check('turn state is in the header', banners.every((b) => b.state.length > 0 && b.state.length < 9),
  banners.map((b) => b.state).join(' '));
check('block colours are white / claude-orange / hermes-blue on the chips and capsules',
  banners.map((b) => b.brandBg).join(' ') === 'rgb(255, 255, 255) rgb(217, 119, 87) rgb(0, 0, 242)' &&
    banners.every((b) => b.metrics.every((m) => m.fill === b.brandBg)),
  banners.map((b) => b.brandBg).join(' | '));
check('the state chip is tone-coloured with black ink, not the banner colour',
  banners.every((b) => ['active', 'idle', 'attention'].includes(b.stateTone) &&
    b.stateInk === 'rgb(0, 0, 0)' && b.stateBg !== b.brandBg),
  banners.map((b) => `${b.label} ${b.stateTone} ${b.stateBg}`).join(' | '));

// ---- neobrutalist geometry (https://www.neobrutalism.dev/docs) ----
const shadowNumbers = (shadow) => (shadow.match(/-?[0-9.]+px/g) ?? []).map((v) => parseFloat(v));
/** A hard offset shadow: black, equal x/y offsets, zero blur. */
const hardOffset = (shadow) =>
  shadow.includes('rgb(0, 0, 0)') &&
  shadowNumbers(shadow).some((value, index, all) => value >= 10 && all[index + 1] >= 10 && all[index + 2] === 0);
check('cards wear the black frame: thick border, hard offset shadow, small radius',
  banners.every((b) => b.borderPx >= 4 && hardOffset(b.shadow) && b.radiusPx <= 16),
  `${banners[0]?.borderPx}px border, radius ${banners[0]?.radiusPx}px, shadow ${banners[0]?.shadow}`);
check('text sits on its block colour at readable contrast (>= 4.5:1)',
  banners.every((b) => contrast(b.brandInk, b.brandBg) >= 4.5 &&
    b.metrics.every((m) => contrast(m.ink, m.fill) >= 4.5)),
  banners.map((b) => `${b.label} ${contrast(b.brandInk, b.brandBg).toFixed(1)}:1`).join(' '));
check('values are heavy weight, not light',
  banners.every((b) => b.metrics.every((m) => Number(m.weight) >= 700)),
  banners[0]?.metrics.map((m) => m.weight).join(','));
check('cards are light, so the black frames and block colours read',
  banners.every((b) => luminance(b.cardBg) >= 0.6),
  banners.map((b) => `${b.label} tint luminance ${luminance(b.cardBg).toFixed(2)}`).join(' '));
check('capsule blocks separate from the card they sit on',
  banners.every((b) => b.metrics.every((m) => distance(m.fill, b.cardBg) >= 30)),
  banners.map((b) => `${b.label} ${distance(b.metrics[0].fill, b.cardBg)}`).join(' '));
const widths = await client.eval(`(() => {
  const row = document.querySelector('[data-agent="codex"] [data-role="capsules"]');
  return [...row.querySelectorAll('[data-metric]')].map((t) => ({
    key: t.getAttribute('data-metric'),
    left: Math.round(t.getBoundingClientRect().left),
    width: Math.round(t.getBoundingClientRect().width),
    overflow: [...t.querySelectorAll('[data-role="label"], [data-role="value"]')]
      .some((el) => el.scrollWidth > el.clientWidth + 1),
  }));
})()`);
const byKey = Object.fromEntries(widths.map((w) => [w.key, w]));
const inOrder = [...widths].sort((a, b) => a.left - b.left).map((w) => w.key);
check('model and the hour graph lead the row', inOrder[0] === 'model' && inOrder[1] === 'spark',
  inOrder.join(' '));
check('the 1H graph capsule takes two units, everything else takes one',
  Math.abs(byKey.spark.width / byKey.speed.width - 2) < 0.12,
  widths.map((w) => `${w.key}=${w.width}`).join(' '));
check('no capsule text is clipped at the narrower width',
  widths.every((w) => !w.overflow), widths.filter((w) => w.overflow).map((w) => w.key).join(',') || 'none clipped');
check('thin capsules still read as blocks, not pills',
  widths.filter((w) => w.key !== 'spark').every((w) => w.width >= 300),
  `narrowest non-graph capsule ${Math.min(...widths.filter((w) => w.key !== 'spark').map((w) => w.width))}px`);
const barGeometry = await client.eval(`(() => {
  const svg = document.querySelector('[data-metric="spark"] [data-role="sparkline"]');
  const rects = [...svg.querySelectorAll('rect')];
  const box = svg.getBoundingClientRect();
  const first = rects[0].getBoundingClientRect();
  const second = rects[1].getBoundingClientRect();
  return {
    bars: rects.length,
    barPx: Math.round(first.width * 10) / 10,
    gapPx: Math.round((second.left - first.right) * 10) / 10,
    viewBoxWidth: svg.viewBox.baseVal.width,
    heightPx: Math.round(box.height),
  };
})()`);
check('bars are finer than the old fixed-width slot', barGeometry.viewBoxWidth === 100 &&
  barGeometry.barPx < 55 && barGeometry.barPx > 10,
  `${barGeometry.bars} bars of ${barGeometry.barPx}px with ${barGeometry.gapPx}px gaps in ${barGeometry.heightPx}px`);
check('gap between capsules is at least 10px on the canvas',
  banners.every((b) => b.gapPx >= 20), `gap ${banners[0]?.gapPx}px source = ${(banners[0]?.gapPx ?? 0) / 2}px canvas`);
check('no block colour can be confused with the chroma key',
  banners.every((b) => distance(b.cardBg, 'rgb(0, 255, 0)') > 60 &&
    distance(b.brandBg, 'rgb(0, 255, 0)') > 60) &&
    distance('rgb(255, 255, 255)', 'rgb(0, 255, 0)') > 60 &&
    distance('rgb(217, 119, 87)', 'rgb(0, 255, 0)') > 60 &&
    distance('rgb(0, 0, 242)', 'rgb(0, 255, 0)') > 60,
  'every fill is far from #00ff00 in at least one channel');

// ---- the two new capsules ----
check('every banner names a model, compacted to fit',
  banners.every((b) => /^[A-Z0-9][A-Z0-9 .-]{1,15}$/.test(b.metrics.find((m) => m.metric === 'model')?.value ?? '')),
  banners.map((b) => b.metrics.find((m) => m.metric === 'model')?.value).join(' | '));
check('model capsule is one step smaller than the numbers',
  Math.round(await client.eval(`parseFloat(getComputedStyle(document.querySelector('[data-metric="model"] [data-role="value"]')).fontSize)`)) === 56,
  await client.eval(`getComputedStyle(document.querySelector('[data-metric="model"] [data-role="value"]')).fontSize`));
const sparks = await client.eval(`(() => [...document.querySelectorAll('[data-role="sparkline"]')].map((svg) => ({
  bars: svg.querySelectorAll('rect').length,
  values: [...svg.querySelectorAll('rect')].map((r) => Number(r.getAttribute('data-value'))),
  width: Math.round(svg.getBoundingClientRect().width),
  height: Math.round(svg.getBoundingClientRect().height),
  label: svg.getAttribute('aria-label'),
})))()`);
check('every banner draws a 24-bucket sparkline', sparks.length === 3 && sparks.every((s) => s.bars === 24),
  sparks.map((s) => s.bars).join(','));
check('sparkline series matches the API',
  sparks.every((s, index) => JSON.stringify(s.values) === JSON.stringify(api.agents[index].extras.spark.series)),
  sparks.map((s) => s.label).join(' | '));
check('sparkline bars scale to their bucket, not all equal',
  sparks.some((s) => new Set(s.values).size > 1) &&
    (await client.eval(`(() => [...document.querySelectorAll('[data-role="sparkline"]')].every((svg) => {
      const rects = [...svg.querySelectorAll('rect')];
      return rects.every((r) => Number(r.getAttribute('height')) > 0 && Number(r.getAttribute('height')) <= 100);
    }))()`)) === true);
check('1H TOK value is the sum of its buckets',
  banners.every((b, index) => {
    const total = api.agents[index].extras.spark.series.reduce((sum, value) => sum + value, 0);
    return b.metrics.find((m) => m.metric === 'spark')?.value === String(api.agents[index].extras.spark.value) && total >= 0;
  }),
  banners.map((b) => b.metrics.find((m) => m.metric === 'spark')?.value).join(' | '));

// ---- legibility: sizes are in CSS px at a 3840-wide source, canvas scale 0.5 ----
const numeric = (b) => b.metrics.filter((m) => m.metric !== 'model' && m.metric !== 'spark');
const sizes = numeric(banners[0] ?? { metrics: [] });
check('numeric values render at 72px in the 4K source (36px on the 1080p canvas)',
  sizes.every((m) => m.valuePx === 72), sizes.map((m) => m.valuePx).join(','));
check('every capsule label renders at 30px in the 4K source (15px on the canvas)',
  banners.every((b) => b.metrics.every((m) => m.labelPx === 30)),
  banners[0]?.metrics.map((m) => m.labelPx).join(','));
check('no third line of small text per capsule',
  (await client.eval(`document.querySelectorAll('[data-role="detail"]').length`)) === 0);
const brandPx = await client.eval(`parseFloat(getComputedStyle(document.querySelector('[data-role="brand"]')).fontSize)`);
const statePx = await client.eval(`parseFloat(getComputedStyle(document.querySelector('[data-role="state"]')).fontSize)`);
check('brand and turn state are headline sized',
  brandPx >= 48 && statePx >= 40, `brand ${brandPx}px, state ${statePx}px`);
const smallest = Math.min(...banners[0].metrics.map((m) => m.valuePx), ...banners[0].metrics.map((m) => m.labelPx), brandPx, statePx);
check('no text on the canvas is below 14px, the compression floor',
  smallest * 0.5 >= 14, `smallest ${smallest}px source = ${smallest * 0.5}px canvas`);
const longest = Math.max(...banners.map((b) => b.text.length));
check('banner text stays terse (no sentences, under 80 characters)',
  longest < 80 && banners.every((b) => b.metrics.every((m) => m.title.split(' ').length <= 2)),
  `longest banner is ${longest} chars: ${banners[0]?.text}`);
check('dev-only strings are gone from the overlay',
  banners.every((b) => !/CLI|TRANSCRIPT|STATE DB|BRIDGE|IPC|LATEST LOCAL|THIS COMPUTER|QUOTA/i.test(b.text)),
  banners[0]?.text);

// ---- chroma surface ----
const SHELL = '#app > div';
const bg = (sel) => client.eval(`getComputedStyle(document.querySelector(${JSON.stringify(sel)})).backgroundColor`);
check('default background is chroma green', (await bg(SHELL)) === 'rgb(0, 255, 0)', await bg(SHELL));
const surface = await client.eval(`getComputedStyle(document.querySelector('[data-agent="codex"]')).backgroundColor`);
check('banner surfaces are opaque (never see-through to the key)',
  !surface.includes('transparent') && !/[/,]\s*0(\.\d+)?\s*\)$/.test(surface), surface);

// ---- real telemetry ----
check('telemetry API answers with three agents', api.agents?.length === 3, JSON.stringify(api.agents?.map(a => a.key)));
const codex = banners[0]?.metrics ?? [];
check('codex row is populated from live telemetry',
  codex.length === 6 && codex.filter((m) => m.value === '—').length <= 1,
  JSON.stringify(codex.map(m => `${m.metric}=${m.value}`)));
check('30-day and session tokens are shown for every agent',
  banners.every((b) => b.metrics.some((m) => m.metric === 'session' && m.value !== '—') &&
    b.metrics.some((m) => m.metric === 'month' && m.value !== '—')));
check('turn state reads as a known state word',
  banners.every((b) => ['ACTIVE', 'IDLE', 'OFFLINE', 'WAITING', 'OPEN'].includes(b.state)),
  banners.map((b) => `${b.key}=${b.state}`).join(' '));

// ---- liveness: the DOM keeps up with the API ----
const SPARK = `document.querySelector('[data-agent="codex"] [data-metric="spark"] [data-role="value"]').textContent.trim()`;
const first = await client.eval(SPARK);
await sleep(2500);
const api2 = await client.eval(`fetch('/api/telemetry',{cache:'no-store'}).then(r=>r.json())`);
const second = await client.eval(SPARK);
check('overlay tracks the telemetry stream',
  api2.generated > api.generated && second === String(api2.agents[0].extras.spark.value),
  `1H TOK ${first} -> ${second}, api says ${api2.agents[0].extras.spark.value}`);

await client.shot(`${SHOT_DIR}/chyron-4k-green.png`);

// ---- key switching ----
await client.navigate('http://127.0.0.1:8790/?bg=magenta');
const magenta = await client.eval(
  `(()=>{const s=document.querySelector('#app > div');return {href:location.href,bg:s?getComputedStyle(s).backgroundColor:'no-shell',style:s&&s.getAttribute('style')}})()`,
);
check('?bg=magenta switches the key colour', magenta.bg === 'rgb(255, 0, 255)', JSON.stringify(magenta));
await client.eval(`window.dispatchEvent(new KeyboardEvent('keydown',{key:'g'}))`);
await sleep(300);
check('keyboard g returns to green', (await bg(SHELL)) === 'rgb(0, 255, 0)', await bg(SHELL));

// ---- single-banner mode (one browser source per banner in OBS) ----
await client.send('Emulation.setDeviceMetricsOverride', { width: SOURCE_WIDTH, height: SOURCE_HEIGHT, deviceScaleFactor: 1, mobile: false });
await client.navigate('http://127.0.0.1:8790/?agent=claude&bg=green');
const soloRows = await client.eval(READ);
const soloBox = await client.eval(
  `(()=>{const c=document.querySelector('[data-agent]').getBoundingClientRect();return {w:Math.round(c.width),h:Math.round(c.height),top:Math.round(c.top),bottom:Math.round(c.bottom)}})()`,
);
check('?agent= renders exactly one banner', soloRows.length === 1 && soloRows[0].key === 'claude',
  soloRows.map((b) => b.key).join(','));
check('solo banner keeps its block colour', soloRows[0]?.brandBg === 'rgb(217, 119, 87)', soloRows[0]?.brandBg);
check('solo banner and its shadow fit the OBS browser source',
  soloBox.w + 16 <= SOURCE_WIDTH && soloBox.h + 16 <= SOURCE_HEIGHT && soloBox.top >= 0 &&
    soloBox.bottom + 16 <= SOURCE_HEIGHT,
  `card ${soloBox.w}x${soloBox.h} at y=${soloBox.top}-${soloBox.bottom}, source ${SOURCE_WIDTH}x${SOURCE_HEIGHT}`);
check('solo banner is populated from live telemetry',
  soloRows[0]?.metrics.some((m) => m.metric === 'session' && m.value !== '—'),
  JSON.stringify(soloRows[0]?.metrics.map((m) => `${m.metric}=${m.value}`).join(' ')));

// ---- counting animation + tally sounds (manual feed, so frames are ours) ----
// Visibility decides whether the tally may sound at all, so frames pin it here
// and the gate is exercised deliberately in its own block further down.
const VISIBLE = { on_air: true, agents: { codex: true, claude: true, hermes: true } };
// The other agents keep ticking in real life, so their counters are pinned to
// constants: otherwise a live increment would click in the middle of a check.
const PINNED = { claude: { session: '5.0M', month: '50.0M' }, hermes: { session: '6.0M', month: '60.0M' } };
const frameFor = async (session, month, visibility = VISIBLE, driver = 'codex') => {
  const payload = await client.eval(`fetch('/api/telemetry',{cache:'no-store'}).then(r=>r.json())`);
  for (const agent of payload.agents) {
    if (agent.key === driver) {
      agent.metrics.session.value = session;
      agent.metrics.month.value = month;
      continue;
    }
    const frozen = PINNED[agent.key];
    if (frozen) {
      agent.metrics.session.value = frozen.session;
      agent.metrics.month.value = frozen.month;
    }
  }
  payload.visibility = visibility;
  return payload;
};
const pushed = (payload) =>
  client.eval(`window.__chyron.push(${JSON.stringify(payload)})`);
const counterText = (metric) =>
  client.eval(
    `document.querySelector('[data-agent="codex"] [data-metric="${metric}"] [data-role="counter"]').textContent.trim()`,
  );

await client.navigate('http://127.0.0.1:8790/?bg=green&feed=manual&sound=1');
check('counters only exist on the two capsules that grow',
  (await client.eval(`document.querySelectorAll('[data-role="counter"]').length`)) === 6 &&
    (await client.eval(`!!document.querySelector('[data-metric="context"] [data-role="counter"]')`)) === false,
  `${await client.eval(`document.querySelectorAll('[data-role="counter"]').length`)} counters for 3 banners x session/month`);

await pushed(await frameFor('10.0M', '100.0M'));
await sleep(900);
check('a settled counter renders exactly what the API sent',
  (await counterText('session')) === '10.0M' && (await counterText('month')) === '100.0M',
  `${await counterText('session')} / ${await counterText('month')}`);

const playsBefore = await client.eval(`window.__chyron.tally.plays`);
await pushed(await frameFor('20.0M', '100.0M'));
const samples = [];
for (let i = 0; i < 7; i += 1) {
  samples.push(await counterText('session'));
  await sleep(90);
}
await sleep(600);
const settled = await counterText('session');
check('the session counter rolls up through intermediate values',
  samples.some((value) => value !== '10.0M' && value !== '20.0M') && settled === '20.0M',
  `${samples.join(' -> ')} -> ${settled}`);
check('the counter animation is monotonic while rolling',
  samples.filter((v) => v.endsWith('M')).map((v) => Number.parseFloat(v)).every((v, i, all) => i === 0 || v >= all[i - 1]),
  samples.join(' '));
check('the 30-day counter does not move when its value is unchanged',
  (await counterText('month')) === '100.0M', await counterText('month'));

// The clicks ARE the drum: one per flap, not one per update. The 30-day counter
// is unchanged in this frame, so nothing else can be clicking.
const rollFlaps = Number(await client.eval(
  `document.querySelector('[data-agent="codex"] [data-metric="session"] [data-role="counter"]').dataset.flaps`));
const playsAfter = await client.eval(`window.__chyron.tally.plays`);
check('the sound follows the drum: one click per flap',
  rollFlaps > 1 && playsAfter - playsBefore === rollFlaps,
  `${rollFlaps} flaps, ${playsAfter - playsBefore} clicks (was ${playsBefore})`);
const audio = await client.eval(`({ state: window.__chyron.tally.state, error: window.__chyron.tally.error })`);
check('the tally plays without falling over', audio.error === null, JSON.stringify(audio));

// ---- turn-state colour coding: green working, red needing a human, grey idle ----
const stateChip = () => client.eval(`(() => {
  const chip = document.querySelector('[data-agent="codex"] [data-role="state"]');
  const style = getComputedStyle(chip);
  // The animation stays attached when it is not running, so ask what is playing.
  const running = chip.getAnimations().some((a) => a.playState === 'running');
  // Measure against the banner card - the frame the chip is drawn into. The pulse
  // is a transform, so overflowing the header row by a pixel is expected; leaving
  // the card is not.
  const frame = chip.closest('[data-agent]').getBoundingClientRect();
  const box = chip.getBoundingClientRect();
  const slack = {
    left: Math.round(frame.left - box.left),
    right: Math.round(box.right - frame.right),
    top: Math.round(frame.top - box.top),
    bottom: Math.round(box.bottom - frame.bottom),
  };
  return {
    text: chip.textContent.trim(),
    tone: chip.dataset.tone,
    background: style.backgroundColor,
    ink: style.color,
    animation: style.animationName,
    running,
    inside: Object.values(slack).every((gap) => gap <= 0),
    slack,
  };
})()`);

/** Which colour family a resolved rgb() value belongs to. */
const dominant = (value) => {
  const [r, g, b] = (value.match(/\d+/g) ?? []).map(Number);
  if (g > r + 30 && g > b + 30) return 'green';
  if (r > g + 40 && r > b + 40) return 'red';
  if (Math.max(r, g, b) - Math.min(r, g, b) < 18) return 'grey';
  return `other(${r},${g},${b})`;
};

const withActivity = async (value) => {
  // Keep the counter where it is: these frames are about the chip, and moving a
  // counter here would roll the drum (and click) in the middle of a check.
  const session = await client.eval(
    `document.querySelector('[data-agent="codex"] [data-metric="session"] [data-role="counter"]').textContent.trim()`);
  const payload = await frameFor(session, '100.0M');
  for (const agent of payload.agents) {
    if (agent.key === 'codex') agent.metrics.activity.value = value;
  }
  await pushed(payload);
  await sleep(240);
  return stateChip();
};

const chips = {
  active: await withActivity('ACTIVE'),
  waiting: await withActivity('WAITING'),
  failed: await withActivity('FAILED'),
  idle: await withActivity('IDLE'),
};
check('an active turn is a green chip with black ink',
  chips.active.tone === 'active' && dominant(chips.active.background) === 'green',
  `${chips.active.text} tone=${chips.active.tone} ${chips.active.background} on ${chips.active.ink}`);
check('the active chip pulses',
  chips.active.running && chips.active.animation.startsWith('turn-grow'),
  `animation=${chips.active.animation} running=${chips.active.running}`);
check('waiting on a human is a red chip',
  chips.waiting.tone === 'attention' && dominant(chips.waiting.background) === 'red',
  `${chips.waiting.text} tone=${chips.waiting.tone} ${chips.waiting.background}`);
check('the red chip pulses too', chips.waiting.running, `running=${chips.waiting.running}`);
check('a failure reads red even though the telemetry has no such state yet',
  chips.failed.tone === 'attention' && dominant(chips.failed.background) === 'red',
  `${chips.failed.text} tone=${chips.failed.tone} ${chips.failed.background}`);
check('idle is a grey chip that sits still',
  chips.idle.tone === 'idle' && dominant(chips.idle.background) === 'grey' &&
    !chips.idle.running && chips.idle.animation === 'none',
  `${chips.idle.text} tone=${chips.idle.tone} ${chips.idle.background} animation=${chips.idle.animation}`);

for (const quiet of ['OFFLINE', 'OPEN', '—']) {
  const chip = await withActivity(quiet);
  check(`"${quiet}" reads as idle rather than claiming work`,
    chip.tone === 'idle' && !chip.running, `tone=${chip.tone} running=${chip.running}`);
}

check('the tone colours carry black ink at readable contrast',
  Object.values(chips).every((chip) => contrast(chip.ink, chip.background) >= 4.5),
  Object.entries(chips).map(([k, c]) => `${k} ${contrast(c.ink, c.background).toFixed(1)}:1`).join(' '));

// The chip grows and shrinks; it must not grow out of the banner frame.
await withActivity('ACTIVE');
const boxes = [];
for (let i = 0; i < 12; i += 1) {
  boxes.push(await stateChip());
  await sleep(150);
}
const escaped = boxes.filter((b) => !b.inside);
check('the pulse stays inside the banner frame', escaped.length === 0,
  `${boxes.length} samples, ${escaped.length} outside` +
    (escaped.length ? ` slack=${JSON.stringify(escaped[0].slack)}` : ''));
await withActivity('IDLE');

// ---- the counter flaps like a departure board, it does not just count ----

const flapState = () => client.eval(`(() => {
  const root = document.querySelector('[data-agent="codex"] [data-metric="session"] [data-role="counter"]');
  const cells = [...root.querySelectorAll('.flap')];
  // animationName stays attached after a fill-mode:both animation ends, so ask
  // the Web Animations API whether anything is actually still running.
  const running = (el) => el.getAnimations().some((a) => a.playState === 'running');
  return {
    flaps: Number(root.dataset.flaps),
    digits: cells.length,
    ghosts: cells.filter((c) => /\\d/.test(c.textContent.trim())).length,
    rolling: cells.filter((c) => running(c.querySelector('.flap-face'))).length,
    // Keyframe names are rewritten by scoped styles (flap-out-36b83182), so match the prefix.
    ghostRolls: cells.filter((c) => (getComputedStyle(c, '::after').animationName || '').startsWith('flap-out')).length,
    text: root.textContent.trim(),
  };
})()`);

await pushed(await frameFor('20.8M', '100.0M'));
// A flap now lands inside its tick (42ms against a ~48ms cadence), so sampling
// once can catch the gap between two of them: sample across the roll instead.
const midSamples = [];
for (let i = 0; i < 5; i += 1) {
  midSamples.push(await flapState());
  await sleep(30);
}
const midRoll = midSamples[0];
check('every character of the value gets its own flap cell',
  midRoll.digits === '20.8M'.length && midRoll.ghosts === '20.8M'.replace(/\D/g, '').length,
  `${midRoll.digits} cells, ${midRoll.ghosts} digit cells for "20.8M"`);
check('flapping digits animate while the roll is on',
  midSamples.some((s) => s.rolling > 0),
  `animating in ${midSamples.filter((s) => s.rolling > 0).length} of ${midSamples.length} samples`);
check('the old digit rolls up and out (the ghost animates)', midRoll.ghostRolls >= 1, `${midRoll.ghostRolls} ghosts`);
check('the roll is reported for the tests',
  Math.max(...midSamples.map((s) => s.flaps)) >= 1,
  `data-flaps=${Math.max(...midSamples.map((s) => s.flaps))}`);
await sleep(1400);
const settledFlaps = await flapState();
check('the roll stops and the text is exactly what the API sent',
  settledFlaps.rolling === 0 && settledFlaps.text === '20.8M',
  `${settledFlaps.text}, ${settledFlaps.rolling} still animating`);

// A single display step must still rattle: one flap would be a blink.
const playsBeforeTiny = await client.eval(`window.__chyron.tally.plays`);
await pushed(await frameFor('20.9M', '100.0M'));
await sleep(90);
const tinyRoll = await flapState();
check('a one-step increment still rattles rather than blinking',
  tinyRoll.flaps >= 2, `data-flaps=${tinyRoll.flaps} for +0.1M`);
await sleep(1400);
check('...and every one of those flips clicks',
  (await client.eval(`window.__chyron.tally.plays`)) - playsBeforeTiny === tinyRoll.flaps,
  `${tinyRoll.flaps} flaps, ${(await client.eval(`window.__chyron.tally.plays`)) - playsBeforeTiny} clicks`);

// An unchanged reading must not touch the DOM at all: same cells, no animation.
const counterHtml = () => client.eval(
  `document.querySelector('[data-agent="codex"] [data-metric="session"] [data-role="counter"]').outerHTML`);
const beforeRepeat = await counterHtml();
await pushed(await frameFor('20.9M', '100.0M'));
await sleep(260);
check('an unchanged reading does not flap',
  (await counterHtml()) === beforeRepeat && (await flapState()).rolling === 0,
  'the counter is untouched');

// Two readings in quick succession: the second roll replaces the first, and the
// clicks never stack on the same beat (the old behaviour was one throttled
// ka-ching per update - now it is a rattle that has to stay orderly).
const playsBeforeBurst = await client.eval(`window.__chyron.tally.plays`);
await pushed(await frameFor('20.4M', '100.0M'));
await sleep(50);
await pushed(await frameFor('20.8M', '100.0M'));
await sleep(1200);
const burstPlays = await client.eval(`window.__chyron.tally.plays`);
const burstGaps = await client.eval(`window.__chyron.tally.gaps`);
check('a fast second reading still clicks rather than being swallowed',
  burstPlays > playsBeforeBurst, `plays ${playsBeforeBurst} -> ${burstPlays}`);
// Gaps between rolls are long; the cadence that matters is inside a roll.
const inRoll = burstGaps.filter((gap) => gap < 400);
check('clicks are spaced like flaps, never stacked on one beat',
  burstGaps.every((gap) => gap >= 20) && inRoll.length >= 3 && inRoll.every((gap) => gap <= 200),
  `in-roll gaps ${inRoll.join(', ')}ms`);
check('a snap downwards is not a tally (new session)',
  (await client.eval(`(async () => { const before = window.__chyron.tally.plays;
    window.__chyron.push(${JSON.stringify(await frameFor('1.0M', '100.0M'))});
    return window.__chyron.tally.plays === before; })()`)) === true);

// ---- a chyron that is not on screen makes no sound ----
// Frames are spaced past a whole roll, so each one's clicks land inside its own
// window; the counter baselines keep running while hidden, so there is nothing
// to catch up on when a chyron comes back.
const GAP = 1000;
const plays = () => client.eval(`window.__chyron.tally.plays`);
const tallyFor = async (session, month, visibility) => {
  await pushed(await frameFor(session, month, visibility));
  await sleep(GAP);
};

await tallyFor('50.0M', '500.0M', VISIBLE);
const audible = await plays();
await tallyFor('60.0M', '600.0M', { ...VISIBLE, on_air: false });
check('the scene is silent while it is off air',
  (await plays()) === audible,
  `plays ${audible} -> ${await plays()} across an off-air increment`);

await tallyFor('70.0M', '700.0M', { on_air: true, agents: { codex: false, claude: true, hermes: true } });
check('a hidden chyron is silent while the others are up',
  (await plays()) === audible,
  `plays ${audible} -> ${await plays()} while codex was hidden`);

await tallyFor('80.0M', '800.0M', { on_air: true, agents: { codex: false, claude: false, hermes: false } });
check('hiding every chyron silences the scene',
  (await plays()) === audible,
  `plays ${audible} -> ${await plays()}`);

await tallyFor('90.0M', '900.0M', VISIBLE);
const revealed = await plays();
check('showing a chyron again brings the tally back',
  revealed > audible,
  `plays ${audible} -> ${revealed} (a roll, one click per flap)`);

// Let that roll finish, then make sure nothing is still clicking behind it.
await sleep(1400);
const caughtUp = await client.eval(`window.__chyron.tally.plays`);
await sleep(1200);
check('no burst of catch-up tallies after a reveal',
  (await client.eval(`window.__chyron.tally.plays`)) === caughtUp,
  `${caughtUp} stays put`);

// Another agent's drum clicks too: the feed is not codex-only.
const playsBeforeHermes = await plays();
await pushed(await frameFor('30.0M', '300.0M', VISIBLE, 'hermes'));
await sleep(1400);
check('another agent rolling clicks as well',
  (await plays()) - playsBeforeHermes > 1,
  `plays ${playsBeforeHermes} -> ${await plays()} for a hermes roll`);

// Session and 30-day count the same tokens, so they usually move together: the
// two drums must not cancel each other out on the click floor. Both counters
// move by the same 10M here, so their plans are identical.
const playsBeforePair = await plays();
await pushed(await frameFor('100.0M', '910.0M'));
await sleep(1500);
const pairFlaps = Number(await client.eval(
  `document.querySelector('[data-agent="codex"] [data-metric="session"] [data-role="counter"]').dataset.flaps`));
const pairClicks = (await plays()) - playsBeforePair;
check('two counters moving together still both click',
  pairClicks >= pairFlaps * 1.8,
  `${pairFlaps} flaps each counter, ${pairClicks} clicks (one per flap, de-phased)`);

// Round-trip parity with the server's compacting, using the page's own helpers
// against the values the API is actually sending right now.
const parity = await client.eval(`(async () => {
  const live = await fetch('/api/telemetry',{cache:'no-store'}).then((r) => r.json());
  return live.agents.flatMap((agent) => ['session', 'month'].map((key) => {
    const text = agent.metrics[key]?.value ?? '—';
    const { value, prefix } = window.__chyron.parseCompact(text);
    return { text, round: value === null ? text : prefix + window.__chyron.formatCompact(value) };
  }));
})()`);
check('parsing then formatting a value gives the API string back',
  parity.every((row) => row.text === row.round),
  parity.map((r) => `${r.text}->${r.round}`).join(' '));

// The gate needs the real API to carry visibility, not just our manual frames.
const liveVisibility = await client.eval(
  `fetch('/api/telemetry',{cache:'no-store'}).then((r) => r.json()).then((d) => d.visibility)`);
check('the API tells the page what is on screen',
  typeof liveVisibility?.on_air === 'boolean' &&
    ['codex', 'claude', 'hermes'].every((agent) => typeof liveVisibility?.agents?.[agent] === 'boolean'),
  JSON.stringify(liveVisibility));

await client.navigate('http://127.0.0.1:8790/?bg=green&feed=manual');
await pushed(await frameFor('10.0M', '100.0M'));
await sleep(200);
await pushed(await frameFor('30.0M', '100.0M'));
await sleep(300);
check('no sound when the source was not asked for it',
  (await client.eval(`window.__chyron.tally.enabled`)) === false &&
    (await client.eval(`window.__chyron.tally.plays`)) === 0,
  `enabled=${await client.eval(`window.__chyron.tally.enabled`)} plays=${await client.eval(`window.__chyron.tally.plays`)}`);

// ---- audio-only source ----
await client.navigate('http://127.0.0.1:8790/?tally=1');
check('tally mode draws nothing at all',
  (await client.eval(`document.querySelectorAll('[data-agent]').length`)) === 0 &&
    (await client.eval(`!!document.querySelector('[data-role="silent"]')`)) === true &&
    (await client.eval(`window.__chyron.tally.enabled`)) === true);

// ---- off-air demo mode ----
await client.send('Emulation.setDeviceMetricsOverride', { width: 3840, height: 2160, deviceScaleFactor: 1, mobile: false });
await client.navigate('http://127.0.0.1:8790/?demo=1&bg=green');
const demoRows = await client.eval(READ);
check('demo mode fills all three rows without a server',
  demoRows.length === 3 &&
    demoRows.every((b) => b.state === 'DEMO' &&
      ['session', 'month'].every((k) => b.metrics.find((m) => m.metric === k)?.value !== '—') &&
      /^[A-Z]/.test(b.metrics.find((m) => m.metric === 'model')?.value ?? '')),
  JSON.stringify(demoRows.map((b) => b.metrics.map((m) => `${m.metric}=${m.value}`).join(' '))));
check('demo mode draws all three sparklines',
  (await client.eval(`document.querySelectorAll('[data-role="sparkline"]').length`)) === 3);
check('demo mode is labelled on screen',
  (await client.eval(`document.body.innerText.includes('DEMO')`)) === true);
await client.shot(`${SHOT_DIR}/chyron-4k-demo.png`);

check('no uncaught errors or console errors', client.errors.length === 0, client.errors.join(' | ') || 'clean');

console.log('\n--- console output ---');
console.log(client.logs.slice(-15).join('\n') || '(none)');

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
