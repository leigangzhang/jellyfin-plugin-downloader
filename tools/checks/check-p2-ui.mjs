// Behaviour guard for the P2 UI fixes (evidence directory → repository, task t28).
//
// What it guards (54 checks, one failure each):
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
//   R8 提交/监控的进行中状态：点下去同一帧就 disabled 且文案含「提交中… 正在探测元数据」，
//      响应放行后恢复可点；失败 toast 带 rc 与耗时秒数，成功 toast 仍是既有文案。
//   R9 搜索停滞反馈：进行中阶段文案带「已用 N s」；超过阈值（1260 s）仍无任何新行且未 done
//      时判定「搜索无进展」并给「重试」入口且停掉轮询；done:true 的正常收尾不受影响。
//   R10 进行中旋转指示器（点下去就插 .jdl-spinner，响应后摘掉）+ 0 种子候选的一行提示
//      （候选已在 backend/probe/probe_round.py 交付前丢弃，面板只报「已过滤 N 条 0 种子候选」，
//      不列行、不给开关；N=0 时不显示）。
//   R11 进行中文案如实且带「已用」：提交写「探测元数据 25 s + 测速最长 180 s（已用 …）」，
//      监控写自己的口径；已用随时钟增长，响应到达后连同 spinner 一起还原。
//
// How it works: the six Web shards are concatenated in the controller's ScriptSegments
// order and run in node:vm against a minimal DOM stub; a ApiClient.ajax stub captures the
// payload the panel really posts. No browser, no Jellyfin, no network.
//
// How to run (from the repository root):
//   node tools/checks/check-p2-ui.mjs                 # green iff all 54 checks hold
//   node tools/checks/check-p2-ui.mjs --mutate        # revert thirteen fixed behaviours, one
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
  'inject_05_render.js',
  'inject_06_trending.js'
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
    searchTick: searchTick,
    renderCandidates: renderCandidates,
    setConfig: function (c) { state.config = c; },
    setManual: function (m) { for (var k in m) { manual[k] = m[k]; } },
    setPanel: function (p) { state.panel = p; },
    setSlug: function (s) { state.slug = s; },
    setSnapshot: function (s) { state.snapshot = s; },
    getSnapshot: function () { return state.snapshot; },
    setSearch: function (s) { state.search = s; },
    // 只有守卫用：把按钮的忙碌起点往回拨 seconds 秒，再重渲染一次文案，
    // 这样不必真的 sleep 一秒就能断言「已用」在增长。
    setBusyElapsed: function (button, seconds) {
      if (button && button.__jdlBusy) {
        button.__jdlBusy.startedAt = Date.now() - seconds * 1000;
        renderBusyLabel(button);
      }
    },
    getSearch: function () { return state.search; },
    getTimer: function () { return state.timer; }
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
  const node = {
    tagName: tag, children: [], style: {}, attributes: {}, className: '', textContent: '',
    // innerHTML 在面板里既当「读名字」（renderRow 用 esc(name) + chips）又当「清空列表」
    // （panel.list.innerHTML = ''）：桩必须两头都像样，否则行数会一直累加。
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v == null ? '' : v); if (this._html === '') { this.children.length = 0; } },
    title: '', value: '', checked: false, type: '', dataset: {},
    // disabled 在 DOM 里是 reflected 属性：getAttribute('disabled') 也要跟着变，
    // 否则「点一下就禁用」这种断言在桩里看不见。
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); c.parentElement = this; return c; },
    insertBefore(c, ref) {
      const at = ref ? this.children.indexOf(ref) : -1;
      if (at < 0) { this.children.push(c); } else { this.children.splice(at, 0, c); }
      c.parentElement = this; return c;
    },
    removeChild(c) {
      const at = this.children.indexOf(c);
      if (at >= 0) { this.children.splice(at, 1); c.parentElement = null; }
      return c;
    },
    get firstChild() { return this.children[0] || null; },
    setAttribute(k, v) { this.attributes[k] = v; if (k === 'disabled') { this.disabled = true; } },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; },
    removeAttribute(k) { delete this.attributes[k]; if (k === 'disabled') { this.disabled = false; } },
    remove() {}, focus() {},
    // 记住监听器并允许 click() 触发：面板的「显示/收起」开关只有点了才看得出接线对不对。
    _handlers: {},
    addEventListener(type, fn) {
      (this._handlers[type] = this._handlers[type] || []).push(fn);
    },
    click() {
      (this._handlers.click || []).forEach((fn) => fn({ preventDefault() {}, stopPropagation() {}, target: this }));
    },
    // 只支持 ".class" 选择器（够面板用）：按类名递归找第一个后代。
    querySelector(sel) {
      const want = String(sel || '').replace(/^\./, '');
      const walk = (parent) => {
        for (const child of parent.children || []) {
          if (String(child.className || '').split(/\s+/).indexOf(want) >= 0) { return child; }
          const deeper = walk(child);
          if (deeper) { return deeper; }
        }
        return null;
      };
      return want ? walk(this) : null;
    },
    querySelectorAll() { return []; }
  };
  return node;
}

function load(bundleSrc, snapshotReply, options) {
  const src = bundleSrc.replace(/\}\)\(\);\s*$/, EXPORT_HOOK + '})();');
  const calls = [];
  const toasts = [];
  const held = [];                     // R8：被挂住、等放行的 ajax
  const hold = !!(options && options.hold);
  const heldReply = (options && options.reply) || { ok: true, rc: 0 };
  const body = makeNode('body');
  body.appendChild = function (child) { toasts.push(child); child.parentElement = body; return child; };
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
      // R8：把响应挂住，模拟「闸门正在跑」的 25 s 窗口。
      if (hold) {
        return new Promise((resolve) => { held.push(() => resolve(heldReply)); });
      }
      // R9：按 URL 回不同的东西（搜索状态 vs 别的一律成功）。
      if (options && options.respond) {
        return Promise.resolve(options.respond(req));
      }
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
  return { t: sandbox.__jdlTest, calls, toasts, loadError, heldCount: () => held.length, release: () => held.splice(0).forEach((fn) => fn()) };
}

async function runChecks(bundleSrc) {
  const failures = [];
  const ok = (name, cond, detail) => { if (!cond) { failures.push(name + (detail ? ' :: ' + detail : '')); } };
  const tick = () => new Promise((resolve) => setImmediate(resolve));
  const { t, calls, loadError } = load(bundleSrc);
  if (loadError) {
    failures.push('LOAD :: the Web shards did not load: ' + loadError.message);
    return { failures, checks: 50 };
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

  // ---- R8 提交/监控的进行中状态：点击同一帧就禁用+换文案，响应后恢复 ----
  // 真机复现：元数据闸门跑了 26.3 s 才回 rc=3，期间界面毫无反应。
  const busyButton = (label) => {
    // actionButton 用 innerHTML 生成 <span class="material-icons">+<span class="jdl-btn__label">；
    // 桩不解析 innerHTML，这里搭一份等价结构。
    const button = makeNode('button');
    button.className = 'jdl-btn jdl-btn--primary';
    const text = makeNode('span');
    text.className = 'jdl-btn__label';
    text.textContent = label;
    button.appendChild(text);
    return { button, text };
  };
  const lastToastText = (doc) => (doc.toasts.length
    ? String(doc.toasts[doc.toasts.length - 1].textContent || '')
    : '');

  {
    // 提交：响应挂住 → 断言「点击瞬间」的进行中状态
    const doc = load(bundleSrc, null, { hold: true, reply: { ok: false, rc: 3 } });
    doc.t.setPanel({ categorySelect: { value: 'Records' }, trustMagnet: false, form: null, item: docItem });
    const ui = busyButton('提交迅雷');
    doc.t.submitAction({ ih: 'F'.repeat(40) }, docSnapshot('record'), 'submit', '已提交迅雷', ui.button);
    ok('R8-submit-busy-disabled-on-click', ui.button.disabled === true, 'disabled=' + ui.button.disabled);
    ok('R8-submit-busy-label-on-click',
      ui.text.textContent.indexOf('提交中') >= 0 && ui.text.textContent.indexOf('元数据') >= 0,
      'label=' + ui.text.textContent);
    ok('R8-submit-inflight', doc.heldCount() === 1, 'held=' + doc.heldCount());
    doc.release();
    await tick(); await tick();
    ok('R8-submit-restored-after-reply',
      ui.button.disabled === false && ui.text.textContent === '提交迅雷',
      'disabled=' + ui.button.disabled + ' label=' + ui.text.textContent);
    const failToast = lastToastText(doc);
    ok('R8-submit-failure-toast-has-rc-and-seconds',
      failToast.indexOf('rc=3') >= 0 && failToast.indexOf('用时') >= 0 && failToast.indexOf(' s') >= 0,
      failToast);
  }
  {
    // 提交成功：文案仍是既有那句
    const doc = load(bundleSrc, null, { hold: true, reply: { ok: true, rc: 0 } });
    doc.t.setPanel({ categorySelect: { value: 'TV Shows' }, trustMagnet: false, form: null, item: docItem });
    doc.t.setSlug('kind-test');
    const ui = busyButton('提交迅雷');
    doc.t.submitAction({ ih: 'G'.repeat(40) }, docSnapshot('episode'), 'submit', '已提交迅雷', ui.button);
    doc.release();
    await tick(); await tick();
    ok('R8-submit-success-toast', lastToastText(doc) === '已提交迅雷', lastToastText(doc));
  }
  {
    // 后台监控：同一套进行中状态（它通常很快，但同样不得静默）
    const doc = load(bundleSrc, null, { hold: true, reply: { ok: true, rc: 0 } });
    doc.t.setPanel({ categorySelect: { value: 'Records' }, trustMagnet: false, form: null, item: docItem });
    doc.t.setSlug('kind-test');
    const ui = busyButton('后台监控');
    doc.t.submitAction({ ih: 'H'.repeat(40) }, docSnapshot('record'), 'watch', '已启动后台监控', ui.button);
    ok('R8-watch-busy-on-click',
      ui.button.disabled === true && ui.text.textContent.indexOf('启动中') >= 0,
      'disabled=' + ui.button.disabled + ' label=' + ui.text.textContent);
    doc.release();
    await tick(); await tick();
    ok('R8-watch-restored-and-toast',
      ui.button.disabled === false && lastToastText(doc) === '已启动后台监控',
      'disabled=' + ui.button.disabled + ' toast=' + lastToastText(doc));
  }

  // ---- R9 搜索停滞反馈：已用时长 + 长期无进展给重试 ----
  // 早期阶段（check/pool/probe）可以几分钟不产出行，job 还可能是永不完成的空壳；
  // 没有这两条时面板会一直显示「正在连接后端…」，看起来就是卡死。
  const collectText = (node) => {
    let out = String(node.textContent || '') + ' ' + String(node.innerHTML || '');
    for (const child of node.children || []) { out += ' ' + collectText(child); }
    return out;
  };
  const searchUi = () => {
    const progress = makeNode('div');
    const stage = makeNode('div');
    stage.className = 'jdl-progress__stage';
    progress.appendChild(stage);
    return {
      stage,
      panel: {
        progress, list: makeNode('div'), summary: makeNode('div'), log: makeNode('pre'),
        kindFilter: 'magnet', seasonFilter: 'all', cacheOnly: false, form: null, item: docItem,
        categorySelect: { value: 'TV Shows' }
      }
    };
  };
  const searchRespond = () => (req) => (String(req.url).indexOf('search/status') >= 0
    ? { done: false, lines: [], error: '' }
    : { ok: true, rc: 0 });
  {
    // ① 进行中：阶段文案带「已用 37 s」，且未到阈值不判停滞
    const doc = load(bundleSrc, null, { respond: searchRespond() });
    const ui = searchUi();
    doc.t.setPanel(ui.panel);
    doc.t.setSearch({ job: 'j1', seen: 0, misses: 0, startedAt: Date.now() - 37000, lastProgressAt: Date.now(), stage: '正在连接后端…' });
    doc.t.searchTick();
    await tick(); await tick();
    ok('R9-elapsed-shown', ui.stage.textContent.indexOf('已用 37 s') >= 0, ui.stage.textContent);
    ok('R9-not-stalled-before-threshold',
      collectText(ui.panel.list).indexOf('无进展') < 0 && ui.panel.list.children.length === 0,
      'stage=' + ui.stage.textContent + ' list=' + ui.panel.list.children.length);
  }
  {
    // ② 超过阈值（1260 s）仍无任何新行 → failSearch + 「重试」，并停掉轮询
    const doc = load(bundleSrc, null, { respond: searchRespond() });
    const ui = searchUi();
    doc.t.setPanel(ui.panel);
    doc.t.setSearch({ job: 'j1', seen: 0, misses: 0, startedAt: Date.now() - 1300000, lastProgressAt: Date.now() - 1300000, stage: '正在连接后端…' });
    doc.t.searchTick();
    await tick(); await tick();
    const text = collectText(ui.panel.list);
    ok('R9-stall-enters-retry', text.indexOf('无进展') >= 0 && text.indexOf('重试') >= 0, text.slice(0, 160));
    ok('R9-stall-stops-polling', doc.t.getTimer() === null && doc.t.getSearch() === null,
      'timer=' + doc.t.getTimer() + ' search=' + doc.t.getSearch());
  }
  {
    // ③ 正常完成路径不受影响（done:true 照旧收尾，不误报无进展）
    const doc = load(bundleSrc, null, {
      respond: (req) => (String(req.url).indexOf('search/status') >= 0
        ? { done: true, lines: ['完成：6 个候选'], error: '', slug: 'kind-test', search_status: 'ok', snapshot: docSnapshot('record') }
        : (String(req.url).indexOf('snapshot') >= 0 ? docSnapshot('record') : { ok: true, rc: 0 }))
    });
    const ui = searchUi();
    doc.t.setPanel(ui.panel);
    doc.t.setSearch({ job: 'j1', seen: 0, misses: 0, startedAt: Date.now() - 5000, lastProgressAt: Date.now() - 5000, stage: '打分 + 校验 metadata…' });
    doc.t.searchTick();
    await tick(); await tick();
    // 注意：done 分支里 renderCandidates() 会按候选数改写 summary，所以「收尾了」要看
    // 阶段行 + 快照已落 + 轮询已停，而不是看 summary 文案。
    ok('R9-done-path-unaffected',
      String(ui.stage.textContent).indexOf('获取完成') >= 0
        && collectText(ui.panel.list).indexOf('无进展') < 0
        && doc.t.getSearch() === null && doc.t.getTimer() === null,
      'stage=' + ui.stage.textContent + ' search=' + doc.t.getSearch() + ' list=' + collectText(ui.panel.list).slice(0, 80));
  }

  // ---- R10 旋转的进行中指示器 + 0 种子候选默认隐藏 ----
  const findButton = (node, label) => {
    for (const child of node.children || []) {
      if (String(child.tagName) === 'button' && String(child.textContent) === label) { return child; }
      const deeper = findButton(child, label);
      if (deeper) { return deeper; }
    }
    return null;
  };
  {
    // ① 点击同一帧插入 .jdl-spinner（且按钮 disabled、文案保留）
    const doc = load(bundleSrc, null, { hold: true, reply: { ok: false, rc: 3 } });
    doc.t.setPanel({ categorySelect: { value: 'Records' }, trustMagnet: false, form: null, item: docItem });
    const ui = busyButton('提交迅雷');
    doc.t.submitAction({ ih: 'I'.repeat(40) }, docSnapshot('record'), 'submit', '已提交迅雷', ui.button);
    ok('R10-spinner-on-click',
      !!ui.button.querySelector('.jdl-spinner') && ui.button.disabled === true,
      'spinner=' + !!ui.button.querySelector('.jdl-spinner') + ' disabled=' + ui.button.disabled);
    ok('R10-spinner-keeps-busy-label', ui.text.textContent.indexOf('提交中') >= 0, ui.text.textContent);
    doc.release();
    await tick(); await tick();
    ok('R10-spinner-gone-after-reply',
      !ui.button.querySelector('.jdl-spinner') && ui.button.disabled === false
        && ui.text.textContent === '提交迅雷',
      'spinner=' + !!ui.button.querySelector('.jdl-spinner') + ' disabled=' + ui.button.disabled
        + ' label=' + ui.text.textContent);
  }
  {
    // ② 0 种子候选已在后端交付前丢弃（不进快照）：面板只报一行条数，不列行、不给开关
    const listPanel = () => {
      const progress = makeNode('div');
      const stage = makeNode('div');
      stage.className = 'jdl-progress__stage';
      progress.appendChild(stage);
      return {
        progress,
        list: makeNode('div'), summary: makeNode('div'), log: makeNode('pre'),
        note: makeNode('div'), hint: makeNode('div'), tasks: makeNode('div'),
        onlyEpisode: null, strictLabel: null, packLabel: null,
        segAll: null, segMagnet: null, segPan: null,
        kindFilter: 'magnet', seasonFilter: 'all', cacheOnly: false, form: null, item: docItem,
        categorySelect: { value: 'TV Shows' },
        showAll: false, includePacks: false, hidePacks: false, fromCache: false, dirtySpec: false
      };
    };
    const cand = (name, seeds) => ({ ih: name, name: name, score: 70, kind: 'magnet', seeds: seeds, title_match: true });
    const rejectedZero = (n) => Array.from({ length: n }, (_, i) => ({
      ih: 'dead-' + i, name: '零种子-' + i, reason: 'dead_swarm_filtered', error: ''
    }));
    const rows = (panel) => panel.list.children.filter((c) => String(c.className || '').indexOf('jdl-row') >= 0).length;

    const doc = load(bundleSrc);
    const panel = listPanel();
    doc.t.setPanel(panel);
    doc.t.setSnapshot({
      slug: 'zero-seed', kind: 'record', year: '2024', query: '里斯本丸沉没', final_dir: '/x/y',
      candidates: [cand('有种-A', 5), cand('有种-B', 2)],
      rejected: rejectedZero(2)
    });
    doc.t.renderCandidates();
    const noteText = collectText(panel.note).replace(/\s+/g, ' ').trim();
    ok('R10-zero-seed-notice-shown',
      noteText.indexOf('已过滤 2 条 0 种子候选') >= 0
        && rows(panel) === 2
        && !findButton(panel.note, '显示零种子候选'),
      'note=' + noteText + ' rows=' + rows(panel));

    const clean = load(bundleSrc);
    const cleanPanel = listPanel();
    clean.t.setPanel(cleanPanel);
    clean.t.setSnapshot({
      slug: 'no-filter', kind: 'record', year: '2024', query: '里斯本丸沉没', final_dir: '/x/y',
      candidates: [cand('有种-A', 5)],
      rejected: [{ ih: 'x', name: 'y', reason: 'metadata unavailable', error: '' }]
    });
    clean.t.renderCandidates();
    ok('R10-zero-seed-notice-absent-when-zero',
      collectText(cleanPanel.note).indexOf('已过滤') < 0,
      collectText(cleanPanel.note).replace(/\s+/g, ' ').trim() || '(note 空)');
  }

  // ---- R11 进行中文案：如实的时间预期 + 每秒刷新的「已用」 ----
  // 真机反馈：点了提交后超过 1 分钟仍写「约 25 s」，而迅雷里任务早已建好（提交是
  // 「元数据 25 s + 测速最长 180 s」两段，队长实测一次完整提交 195.6 s）。
  {
    const doc = load(bundleSrc, null, { hold: true, reply: { ok: false, rc: 3 } });
    doc.t.setPanel({ categorySelect: { value: 'Records' }, trustMagnet: false, form: null, item: docItem });
    const ui = busyButton('提交迅雷');
    doc.t.submitAction({ ih: 'J'.repeat(40) }, docSnapshot('record'), 'submit', '已提交迅雷', ui.button);
    ok('R11-submit-label-honest-and-elapsed',
      ui.text.textContent.indexOf('测速最长 180 s') >= 0
        && ui.text.textContent.indexOf('探测元数据 25 s') >= 0
        && ui.text.textContent.indexOf('已用') >= 0,
      ui.text.textContent);
    ok('R11-submit-label-no-stale-25-only',
      ui.text.textContent.indexOf('\u7ea6 25 s') < 0,
      ui.text.textContent);
    doc.t.setBusyElapsed(ui.button, 72);          // 拨到「已用 1 分 12 秒」
    ok('R11-elapsed-grows-with-the-clock',
      ui.text.textContent.indexOf('已用 1 \u5206 12 \u79d2') >= 0,
      ui.text.textContent);
    doc.release();
    await tick(); await tick();
    ok('R11-label-restored-after-reply',
      ui.button.disabled === false && ui.text.textContent === '提交迅雷'
        && ui.text.textContent.indexOf('已用') < 0,
      'disabled=' + ui.button.disabled + ' label=' + ui.text.textContent);
  }
  {
    // 后台监控的耗时口径与提交不同：不带「测速最长 180 s」，但同样要有「已用」
    const doc = load(bundleSrc, null, { hold: true, reply: { ok: true, rc: 0 } });
    doc.t.setPanel({ categorySelect: { value: 'Records' }, trustMagnet: false, form: null, item: docItem });
    doc.t.setSlug('kind-test');
    const ui = busyButton('后台监控');
    doc.t.submitAction({ ih: 'K'.repeat(40) }, docSnapshot('record'), 'watch', '已启动后台监控', ui.button);
    ok('R11-watch-label-elapsed-without-submit-wording',
      ui.text.textContent.indexOf('已用') >= 0
        && ui.text.textContent.indexOf('测速最长 180 s') < 0
        && ui.text.textContent.indexOf('挂后台监控') >= 0,
      ui.text.textContent);
    doc.release();
    await tick(); await tick();
    ok('R11-watch-restored', ui.button.disabled === false && ui.text.textContent === '后台监控',
      ui.text.textContent);
  }

  return { failures, checks: 54 };
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
    '      renderTasks(snapshot.tasks || []);'],
  ['提交/监控的进行中状态被拆掉',
    '    var idleLabel = setBusy(button, busyLabelFor(endpoint));\n',
    "    var idleLabel = '';\n"],
  ['响应到达后的恢复可点被拆掉',
    '      clearBusy(button, idleLabel);\n      if (!result || result.error)',
    '      if (!result || result.error)'],
  ['搜索停滞判定被拆掉',
    '      if (searchStalled()) {',
    '      if (false) {'],
  ['已用时长的显示被拆掉',
    '  function searchElapsedSeconds() {\n    var search = state.search;',
    '  function searchElapsedSeconds() {\n    return 0;\n    var search = state.search;'],
  ['提交按钮的旋转指示器被拆掉',
    "    if (button.querySelector && !button.querySelector('.jdl-spinner')) {",
    '    if (false) {'],
  ['已过滤提示行被拆掉',
    '    if (zeroSeedFiltered(state.snapshot)) {',
    '    if (false) {'],
  ['进行中文案的「已用」后缀被拆掉',
    '  function renderBusyLabel(button) {\n    var info = button && button.__jdlBusy;',
    '  function renderBusyLabel(button) {\n    if (true) { return; }\n    var info = button && button.__jdlBusy;'],
  ['「已用」时长不再随时钟增长',
    '    var elapsed = Math.max(0, (Date.now() - info.startedAt) / 1000);',
    '    var elapsed = 0;']
];

const EXPECT = {
  '阈值档位选择器回来了': 'R1-no-dial-array',
  'Season 副标题退回「电影」': 'R2-season-not-movie',
  '类型→库联动被拆掉': 'R3-manual-spec-record',
  '详情页/手动面板的纪录片类型改写被拆掉': 'R7-search-record-when-records',
  '监控轮询回写快照时的 kind 对齐被拆掉': 'R7-poll-keeps-record',
  '提交/监控的进行中状态被拆掉': 'R8-submit-busy-disabled-on-click',
  '响应到达后的恢复可点被拆掉': 'R8-submit-restored-after-reply',
  '搜索停滞判定被拆掉': 'R9-stall-enters-retry',
  '已用时长的显示被拆掉': 'R9-elapsed-shown',
  '提交按钮的旋转指示器被拆掉': 'R10-spinner-on-click',
  '已过滤提示行被拆掉': 'R10-zero-seed-notice-shown',
  '进行中文案的「已用」后缀被拆掉': 'R11-submit-label-honest-and-elapsed',
  '「已用」时长不再随时钟增长': 'R11-elapsed-grows-with-the-clock'
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
  console.log('检查项 54，失败 1');
  console.log('FAIL  LOAD :: ' + (error && error.message ? error.message : String(error)));
  console.log(MUTATE ? 'MUTATION CHECK FAIL' : 'P2 UI CHECK FAIL');
  process.exit(1);
}
