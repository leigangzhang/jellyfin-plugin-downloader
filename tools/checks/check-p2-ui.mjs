// Behaviour guard for the P2 UI fixes (evidence directory → repository, task t28).
//
// What it guards (32 checks, one failure each):
//   R1 threshold is read-only: no 60/65/70/75/80 dial, nothing reads panel.threshold,
//      the note node carries jdl-threshold-note, and configuredMinScore() falls back to
//      60 for a missing, zero, or non-numeric config value.
//   R2 subtitleFor(): a Season says 剧集, not 电影; Series/Episode/Movie wording intact.
//   R3 kind → target library link: movie→Movies, record→Records, episode→TV Shows; a
//      manual library pick (categoryTouched) is never overwritten.
//   R4 submit payload: trust_magnet ships only when the toggle is on, and the category
//      still travels with it.
//   R5 a year typed in the manual form reaches the payload when the snapshot has none.
//   R6 the manual "类型" select still offers the record (纪录片) option.
//   R7 纪录片（Jellyfin 认成 Series）走详情页入口：目标库 = Records 时 /api/search 与
//      /api/submit 的有效类型都是 record，且监控轮询（refreshTasks 重写整份快照，
//      后端快照 kind 是探针口径 movie）之后提交第 2 条仍是 record；TV Shows 路径不变。
//
// How it works: the five Web shards are concatenated in the controller's ScriptSegments
// order and run in node:vm against a minimal DOM stub; a ApiClient.ajax stub captures the
// payload the panel really posts. No browser, no Jellyfin, no network.
//
// How to run (from the repository root):
//   node tools/checks/check-p2-ui.mjs                 # green iff all 32 checks hold
//   node tools/checks/check-p2-ui.mjs --mutate        # revert five fixed behaviours, one
//                                                     # at a time, and require each to go red
//   node tools/checks/check-p2-ui.mjs --web-dir DIR   # check another copy of the shards
//
// Exit codes: 0 all checks hold (or all mutations went red), 1 a check failed (or a
// mutation stayed green), 2 usage error.
//
// This file is not run on its own in CI: tools/check-invariants.py runs it as part of the
// declared check set, and its --self-test runs `--mutate`, so a UI regression fails the
// same command the rest of the kit's guards fail.
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '..', '..');

function parseArgs(argv) {
  let webDir = null;
  let mutate = false;
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--mutate') { mutate = true; }
    else if (arg === '--web-dir') { webDir = argv[index + 1]; index += 1; }
    else if (arg === '--help' || arg === '-h') {
      console.log('usage: node tools/checks/check-p2-ui.mjs [--mutate] [--web-dir DIR]');
      process.exit(0);
    } else if (arg.startsWith('--')) {
      console.error('unknown option: ' + arg);
      process.exit(2);
    } else { webDir = arg; }
  }
  if (webDir == null) {
    webDir = process.env.JDL_WEB_DIR
      || path.join(REPO_ROOT, 'Jellyfin.Plugin.JellyfinDownloader', 'Web');
  }
  return { webDir: path.resolve(webDir), mutate };
}

const { webDir: WEB, mutate: MUTATE } = parseArgs(process.argv.slice(2));
const SHARDS = [
  'inject_01_core.js',
  'inject_02_entry.js',
  'inject_03_panel.js',
  'inject_04_search.js',
  'inject_05_render.js'
];

const EXPORT_HOOK = `
  window.__jdlTest = {
    subtitleFor: subtitleFor,
    configuredMinScore: configuredMinScore,
    defaultCategoryForKind: defaultCategoryForKind,
    manualSpec: manualSpec,
    manualSubtitle: manualSubtitle,
    syncManualCategoryDefault: syncManualCategoryDefault,
    buildManualForm: buildManualForm,
    submitAction: submitAction,
    runSearch: runSearch,
    refreshTasks: refreshTasks,
    setConfig: function (c) { state.config = c; },
    setManual: function (m) { for (var k in m) { manual[k] = m[k]; } },
    setPanel: function (p) { state.panel = p; },
    setSlug: function (s) { state.slug = s; },
    setSnapshot: function (s) { state.snapshot = s; },
    getSnapshot: function () { return state.snapshot; }
  };
`;

function readBundle() {
  return SHARDS.map(function (f) {
    const file = path.join(WEB, f);
    if (!fs.existsSync(file)) {
      // A missing shard is a red guard, not a crash: the panel cannot be checked at all.
      throw new Error('missing shard ' + f + ' under ' + WEB
        + '（分片改名或挪走时，这条检查必须先红，而不是假装通过）');
    }
    return fs.readFileSync(file, 'utf8');
  }).join('');
}

function makeNode(tag) {
  return {
    tagName: tag, children: [], style: {}, attributes: {}, className: '', textContent: '',
    innerHTML: '', title: '', value: '', checked: false, type: '', dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); c.parentElement = this; return c; },
    setAttribute(k, v) { this.attributes[k] = v; },
    removeAttribute() {}, remove() {}, focus() {},
    addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }
  };
}

function load(bundleSrc, snapshotReply) {
  const src = bundleSrc.replace(/\}\)\(\);\s*$/, EXPORT_HOOK + '})();');
  const calls = [];
  const body = makeNode('body');
  const sandbox = {
    console: { log() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    MutationObserver: function () { this.observe = function () {}; },
    document: {
      readyState: 'loading', body,
      addEventListener() {}, removeEventListener() {}, createElement: makeNode,
      querySelector() { return null; }, querySelectorAll() { return []; }
    }
  };
  sandbox.window = sandbox;
  sandbox.window.ApiClient = {
    getUrl: (p) => p,
    ajax: (req) => {
      calls.push(req);
      // 只有 R7 的监控轮询需要「后端回一份快照」；其余检查沿用原来的空回复。
      if (snapshotReply && String(req.url).indexOf('snapshot') >= 0) {
        return Promise.resolve(snapshotReply());
      }
      return Promise.resolve({ ok: true, rc: 0 });
    }
  };
  vm.createContext(sandbox);
  let loadError = null;
  try {
    vm.runInContext(src, sandbox, { filename: 'jdl-bundle.js' });
  } catch (error) {
    // A bundle that will not even run — a renamed export, a shard that no longer defines
    // the function under test — is the loudest regression this guard can see, so it is
    // reported as one failure instead of escaping as a stack trace.
    loadError = error;
  }
  return { t: sandbox.__jdlTest, calls, loadError };
}

async function runChecks(bundleSrc) {
  const failures = [];
  const ok = (name, cond, detail) => { if (!cond) { failures.push(name + (detail ? ' :: ' + detail : '')); } };
  const tick = () => new Promise((resolve) => setImmediate(resolve));
  const { t, calls, loadError } = load(bundleSrc);
  if (loadError) {
    failures.push('LOAD :: the Web shards did not load: ' + loadError.message);
    return { failures, checks: 32 };
  }

  // ---- acceptance 1：不再暴露阈值档位，阈值只取配置 ----
  ok('R1-no-dial-array', !/\[\s*60\s*,\s*65\s*,\s*70\s*,\s*75\s*,\s*80\s*\]/.test(bundleSrc),
    '仍存在 60/65/70/75/80 档位数组');
  ok('R1-no-panel-threshold-read', !/panel\.threshold\b/.test(bundleSrc), '仍在读 panel.threshold');
  ok('R1-has-readonly-note', /jdl-threshold-note/.test(bundleSrc), '缺少只读阈值文案节点');
  ok('R1-min-score-default', t.configuredMinScore() === 60, 'default=' + t.configuredMinScore());
  t.setConfig({ minScore: 75 });
  ok('R1-min-score-from-config', t.configuredMinScore() === 75, 'config 75 -> ' + t.configuredMinScore());
  t.setConfig({ minScore: 0 });
  ok('R1-min-score-zero-falls-back', t.configuredMinScore() === 60, 'config 0 -> ' + t.configuredMinScore());
  t.setConfig({ minScore: 'abc' });
  ok('R1-min-score-nan-falls-back', t.configuredMinScore() === 60, "config 'abc' -> " + t.configuredMinScore());
  t.setConfig({ minScore: 60 });

  // ---- acceptance 2：Season 不再写成「电影」 ----
  const seasonSub = t.subtitleFor({ Type: 'Season', Name: '第 2 季', ProductionYear: 2025, SeriesName: '王冠' });
  ok('R2-season-not-movie', seasonSub.indexOf('电影') === -1, seasonSub);
  ok('R2-season-says-series', seasonSub.indexOf('剧集') >= 0, seasonSub);
  const seriesSub = t.subtitleFor({ Type: 'Series', Name: '王冠', ProductionYear: 2016 });
  ok('R2-series-intact', seriesSub.indexOf('剧集整季') >= 0 && seriesSub.indexOf('电影') === -1, seriesSub);
  const movieSub = t.subtitleFor({ Type: 'Movie', Name: '一战再战', ProductionYear: 2025 });
  ok('R2-movie-intact', movieSub.indexOf('电影') >= 0, movieSub);
  const epSub = t.subtitleFor({ Type: 'Episode', SeriesName: '王冠', ParentIndexNumber: 2, IndexNumber: 3 });
  ok('R2-episode-intact', epSub.indexOf('S02E03') >= 0, epSub);

  // ---- acceptance 3：类型 → 目标库联动，手动改过不再覆盖 ----
  ok('R3-map-movie', t.defaultCategoryForKind('movie') === 'Movies');
  ok('R3-map-record', t.defaultCategoryForKind('record') === 'Records');
  ok('R3-map-episode', t.defaultCategoryForKind('episode') === 'TV Shows');
  t.setManual({ title: '泰坦尼克号', kind: 'movie' });
  ok('R3-manual-spec-movie', t.manualSpec().category === 'Movies', t.manualSpec().category);
  ok('R3-manual-spec-movie-kind', t.manualSpec().kind === 'movie', t.manualSpec().kind);
  t.setManual({ kind: 'record' });
  ok('R3-manual-spec-record', t.manualSpec().category === 'Records', t.manualSpec().category);
  ok('R3-manual-record-searches-as-episode', t.manualSpec().kind === 'episode', t.manualSpec().kind);
  ok('R3-manual-record-subtitle', t.manualSubtitle().indexOf('纪录片') >= 0, t.manualSubtitle());
  const select = { value: 'TV Shows' };
  t.setPanel({ categorySelect: select, categoryTouched: false });
  t.setManual({ kind: 'movie' });
  t.syncManualCategoryDefault();
  ok('R3-link-follows-kind', select.value === 'Movies', 'value=' + select.value);
  t.setPanel({ categorySelect: select, categoryTouched: true });   // 用户手动改过库
  t.setManual({ kind: 'episode' });
  t.syncManualCategoryDefault();
  ok('R3-link-respects-touched', select.value === 'Movies', '被覆盖成 ' + select.value);

  // ---- acceptance 4：trust-magnet 载荷（默认不发，勾选才发 true） ----
  const snapshot = { final_dir: '/Volumes/XIAOMI SSD2/Media/Records/致真理 (2023)', kind: 'episode', query: '致真理', year: '2023' };
  const candidate = { ih: 'A'.repeat(40) };
  t.setPanel({ categorySelect: { value: 'Records' }, trustMagnet: false });
  t.submitAction(candidate, snapshot, 'submit', '已提交');
  const off = calls[calls.length - 1];
  const offBody = JSON.parse(off.data);
  ok('R4-default-off-no-param', !('trust_magnet' in offBody), JSON.stringify(offBody));
  ok('R4-category-in-payload', offBody.category === 'Records', JSON.stringify(offBody));
  t.setPanel({ categorySelect: { value: 'Records' }, trustMagnet: true });
  t.submitAction(candidate, snapshot, 'submit', '已提交');
  const onBody = JSON.parse(calls[calls.length - 1].data);
  ok('R4-on-sends-true', onBody.trust_magnet === true, JSON.stringify(onBody));

  // ---- §6/F2：快照没年份时，手动表单里填的年份必须进载荷 ----
  t.setManual({ year: '2020', kind: 'episode', title: '凡人修仙传' });
  t.setPanel({ categorySelect: { value: 'TV Shows' }, trustMagnet: false, form: {} });
  t.submitAction(candidate, { final_dir: '/x/y', kind: 'episode', query: '凡人修仙传', year: '' }, 'submit', 'ok');
  const yearBody = JSON.parse(calls[calls.length - 1].data);
  ok('R5-year-from-manual-form', yearBody.year === '2020', JSON.stringify(yearBody));

  // ---- 手动面板「类型」下拉真的有 纪录片 档（纪录片 → Records 的入口） ----
  t.setManual({ kind: 'episode' });
  const form = t.buildManualForm({ Manual: true });
  const kindValues = form.inputs.kind.children.map((o) => o.value);
  ok('R6-kind-options', kindValues.join(',') === 'episode,movie,record', kindValues.join(','));

  // ---- R7 纪录片（Jellyfin 认成 Series）走详情页入口 ----
  // 只按 item.Type 判定会把纪录片写成 episode（t23 的 P1a 就白做了），
  // 所以有效类型还要看「保存到哪个库」；监控轮询换掉整份快照后也不能丢。
  const docItem = { Id: 'i-doc', Type: 'Series', Name: '里斯本丸沉没', ProductionYear: '2024' };
  const docPanel = (category) => ({
    categorySelect: { value: category }, kindFilter: 'magnet', seasonFilter: 'all',
    cacheOnly: false, fromCache: false, dirtySpec: false, trustMagnet: false,
    item: docItem, form: null,
    summary: makeNode('div'), progress: makeNode('div'), list: makeNode('div'), subEl: makeNode('div')
  });
  const docSnapshot = (kind) => ({
    slug: 'kind-test', kind: kind, year: '2024', query: '里斯本丸沉没', candidates: [], tasks: [],
    final_dir: '/Volumes/XIAOMI SSD2/Media/Records/里斯本丸沉没 (2024)'
  });

  {
    const doc = load(bundleSrc);
    doc.t.setPanel(docPanel('Records'));
    doc.t.runSearch(docItem);
    const search = JSON.parse(doc.calls[doc.calls.length - 1].data);
    ok('R7-search-record-when-records', search.kind === 'record', 'kind=' + search.kind);
    await tick();
  }
  {
    const doc = load(bundleSrc);
    doc.t.setPanel(docPanel('TV Shows'));
    doc.t.runSearch(docItem);
    const search = JSON.parse(doc.calls[doc.calls.length - 1].data);
    ok('R7-search-episode-when-tvshows', search.kind === 'episode', 'kind=' + search.kind);
    await tick();
  }
  {
    // 第 1 条提交 → 监控轮询（后端快照 kind=movie）→ 第 2 条提交仍必须发 record
    const doc = load(bundleSrc, () => docSnapshot('movie'));
    doc.t.setPanel(docPanel('Records'));
    doc.t.setSlug('kind-test');
    doc.t.setSnapshot(docSnapshot('record'));
    doc.t.submitAction({ ih: 'D'.repeat(40) }, doc.t.getSnapshot(), 'submit', 'ok');
    const first = JSON.parse(doc.calls[doc.calls.length - 1].data);
    ok('R7-submit-record-before-poll', first.kind === 'record' && first.category === 'Records',
      JSON.stringify(first));
    doc.t.refreshTasks();
    await tick(); await tick();
    ok('R7-poll-keeps-record', doc.t.getSnapshot().kind === 'record', 'kind=' + doc.t.getSnapshot().kind);
    doc.t.submitAction({ ih: 'E'.repeat(40) }, doc.t.getSnapshot(), 'submit', 'ok');
    const second = JSON.parse(doc.calls[doc.calls.length - 1].data);
    ok('R7-submit-record-after-poll', second.kind === 'record' && second.category === 'Records',
      JSON.stringify(second));
  }
  {
    const doc = load(bundleSrc, () => docSnapshot('episode'));
    doc.t.setPanel(docPanel('TV Shows'));
    doc.t.setSlug('kind-test');
    doc.t.setSnapshot(docSnapshot('episode'));
    doc.t.refreshTasks();
    await tick(); await tick();
    ok('R7-poll-keeps-episode', doc.t.getSnapshot().kind === 'episode',
      'kind=' + doc.t.getSnapshot().kind);
  }

  return { failures, checks: 32 };
}

const MUTATIONS = [
  ['阈值档位选择器回来了', 'var thresholdNote = el(\'span\', \'jdl-threshold-note\', \'\');',
    'var threshold = el(\'select\', \'jdl-select\');\n    [60, 65, 70, 75, 80].forEach(function (v) { threshold.appendChild(el(\'option\', null, v)); });\n    var thresholdNote = el(\'span\', \'jdl-threshold-note\', \'\');'],
  ['Season 副标题退回「电影」', "    return base + ' · 剧集';\n  }",
    "    return base + (item.Type === 'Series' ? ' · 剧集整季' : ' · 电影');\n  }"],
  ['类型→库联动被拆掉', 'category: defaultCategoryForKind(manual.kind),', "category: 'TV Shows',"],
  ['详情页/手动面板的纪录片类型改写被拆掉', "    if (wantsRecordKind()) { params.kind = 'record'; }\n", ''],
  ['监控轮询回写快照时的 kind 对齐被拆掉',
    '      rememberSnapshotKind(snapshot.kind);\n      syncSnapshotKind();\n      renderTasks(snapshot.tasks || []);',
    '      renderTasks(snapshot.tasks || []);']
];

const EXPECT = {
  '阈值档位选择器回来了': 'R1-no-dial-array',
  'Season 副标题退回「电影」': 'R2-season-not-movie',
  '类型→库联动被拆掉': 'R3-manual-spec-record',
  '详情页/手动面板的纪录片类型改写被拆掉': 'R7-search-record-when-records',
  '监控轮询回写快照时的 kind 对齐被拆掉': 'R7-poll-keeps-record'
};

// A shard that cannot be read is a failure of this guard, not a crash of the command that
// runs it: tools/check-invariants.py must print a red line for the check, not a stack trace.
try {
  if (MUTATE) {
    const base = readBundle();
    let bad = 0;
    for (const [name, from, to] of MUTATIONS) {
      if (base.indexOf(from) < 0) { console.log('SKIP  ' + name + '（锚点未找到）'); bad += 1; continue; }
      const mutated = base.replace(from, to);
      const { failures } = await runChecks(mutated);
      const want = EXPECT[name];
      const hit = failures.some((f) => f.indexOf(want) === 0);
      console.log((hit ? 'RED-OK  ' : 'RED-FAIL') + '  ' + name + ' → ' + want
        + ' | failures=' + failures.length + (hit ? '' : ' ' + JSON.stringify(failures)));
      if (!hit) { bad += 1; }
    }
    console.log(bad === 0 ? 'MUTATION CHECK PASS（每条检查都能变红）' : 'MUTATION CHECK FAIL');
    process.exit(bad === 0 ? 0 : 1);
  }

  const { failures, checks } = await runChecks(readBundle());
  console.log('检查项 ' + checks + '，失败 ' + failures.length);
  for (const f of failures) { console.log('FAIL  ' + f); }
  console.log(failures.length === 0 ? 'P2 UI CHECK PASS' : 'P2 UI CHECK FAIL');
  process.exit(failures.length === 0 ? 0 : 1);
} catch (error) {
  console.log('检查项 32，失败 1');
  console.log('FAIL  LOAD :: ' + (error && error.message ? error.message : String(error)));
  console.log(MUTATE ? 'MUTATION CHECK FAIL' : 'P2 UI CHECK FAIL');
  process.exit(1);
}
