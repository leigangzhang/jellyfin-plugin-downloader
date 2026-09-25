(function () {
  'use strict';

  if (window.__jdlLoaded) { return; }
  window.__jdlLoaded = true;

  var JDL_BUILD = '2026-09-25h';
  window.__JDL_BUILD = JDL_BUILD;
  // 入口图标：单个向下的箭头。Material 的 `download` 字形带一条底横线，
  // 小尺寸下看着像「两个箭头」，这里统一用 `arrow_downward`（纯单箭头）。
  var ENTRY_ICON = 'arrow_downward';
  var API = '/JellyfinDownloader/api/';
  var BAND = { great: 75, good: 60 };
  var state = {
    config: { minScore: 60 },
    slug: '',
    snapshot: null,
    timer: null,
    monitorTimer: null,
    item: null,
    panel: null
  };

  // 「手动搜索」的表单模型：独立于 DOM，面板还没搭好也能读
  // （右上角入口常用：搜库里没有的剧集/电影，没有详情页可点）
  var manual = { title: '', alt: '', year: '', kind: 'episode', season: '', episode: '' };

  function log() {
    try {
      console.log.apply(console, ['[JDL]'].concat(Array.prototype.slice.call(arguments)));
    } catch (error) { /* ignore */ }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function call(relative, options) {
    options = options || {};
    var url = window.ApiClient && window.ApiClient.getUrl
      ? window.ApiClient.getUrl(relative)
      : relative;
    var request = { url: url, type: options.type || 'GET', dataType: 'json' };
    if (options.data !== undefined) {
      request.data = JSON.stringify(options.data);
      request.contentType = 'application/json';
    }
    return window.ApiClient.ajax(request);
  }

  function api(path, options) {
    return call(API + path, options);
  }

  function pluginApi(path, options) {
    return call('/JellyfinDownloader/' + path, options);
  }

  function toast(message) {
    var node = el('div', 'jdl-toast', message);
    document.body.appendChild(node);
    setTimeout(function () { node.classList.add('jdl-toast--in'); }, 10);
    setTimeout(function () {
      node.classList.remove('jdl-toast--in');
      setTimeout(function () { node.remove(); }, 250);
    }, 2600);
  }

  function fmtSize(gb) {
    if (gb == null || isNaN(gb)) { return ''; }
    return gb < 1 ? Math.round(gb * 1024) + ' MB' : gb.toFixed(2) + ' GB';
  }

  function fmtSpeed(bytesPerSecond) {
    if (!bytesPerSecond) { return ''; }
    return (bytesPerSecond / 1048576).toFixed(1) + ' MB/s';
  }

  function bandOf(score) {
    if (score >= BAND.great) { return 'jdl-score--great'; }
    if (score >= BAND.good) { return 'jdl-score--good'; }
    return 'jdl-score--ok';
  }

  function themeClass() {
    try {
      var background = window.getComputedStyle(document.body).backgroundColor || '';
      var match = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(background);
      if (match) {
        var luminance = (0.2126 * Number(match[1]) + 0.7152 * Number(match[2]) + 0.0722 * Number(match[3])) / 255;
        return luminance < 0.5 ? 'jdl-dark' : 'jdl-light';
      }
    } catch (error) { /* ignore */ }
    return '';
  }

  function whenReady(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  function waitFor(test, fn, tries) {
    tries = tries == null ? 400 : tries;
    if (test()) { fn(); return; }
    if (tries <= 0) { log('waitFor timed out'); return; }
    setTimeout(function () { waitFor(test, fn, tries - 1); }, 250);
  }

  // 取当前用户：10.11 上 getCurrentUser() 在页面刚加载时可能直接 reject，
  // 因此优先用 getCurrentUserId() + getUser(id)，并全部做容错。
  function resolveAdmin() {
    var client = window.ApiClient;
    return new Promise(function (resolve) {
      var settled = false;
      function finish(value) {
        if (!settled) { settled = true; resolve(value); }
      }

      try {
        var userId = client.getCurrentUserId && client.getCurrentUserId();
        if (userId && client.getUser) {
          client.getUser(userId).then(function (user) {
            finish(!!(user && user.Policy && user.Policy.IsAdministrator));
          }).catch(function () { finish(null); });
          return;
        }
      } catch (error) { /* try next */ }

      try {
        if (client.getCurrentUser) {
          Promise.resolve(client.getCurrentUser()).then(function (user) {
            finish(!!(user && user.Policy && user.Policy.IsAdministrator));
          }).catch(function () { finish(null); });
          return;
        }
      } catch (error) { /* give up */ }

      finish(null);
    });
  }

  function bootstrap(attempt) {
    attempt = attempt || 0;
    resolveAdmin().then(function (admin) {
      if (admin === null && attempt < 20) {
        setTimeout(function () { bootstrap(attempt + 1); }, 500);
        return;
      }
      if (admin === null) {
        log('admin check inconclusive -> continuing (server enforces admin)');
      } else {
        log('admin =', admin);
        if (!admin) { return; }
      }
      pluginApi('config').then(function (cfg) {
        if (cfg && cfg.minScore != null) { state.config = cfg; }
      }).catch(function (error) {
        log('config fetch failed', error && error.status);
      }).then(start);
    });
  }

  whenReady(function () {
    log('script loaded');
    waitFor(function () {
      return window.ApiClient && (window.ApiClient.getCurrentUserId || window.ApiClient.getCurrentUser);
    }, function () {
      log('ApiClient ready');
      bootstrap(0);
    });
  });

  function start() {
    // 版本横幅：页面上跑的是哪一版脚本，Console 里一眼能看出来
    //（脚本只在页面加载时执行一次，SPA 内部跳转不会重新注入）
    log('build', JDL_BUILD, '· 入口：详情页只用「…」旁的图标，其它页面用右上角图标');
    window.addEventListener('hashchange', scheduleMount);
    new MutationObserver(scheduleMount)
      .observe(document.body, { childList: true, subtree: true });
    // 兜底收敛：观察者只在 DOM 变化时触发，这里按时间再兜一层，
    // 保证「一屏只有一个资源入口」这件事最终一定成立。
    setInterval(enforceSingleEntry, 1500);
    scheduleMount();
  }

  // 一屏只允许一个插件入口：详情页留「…」旁边那个，其它页面留右上角那个。
  // 顺带清理只剩 class 能认出来的残骸（React 重建时可能把 data-* 弄丢）。
  function enforceSingleEntry() {
    var repaired = 0;
    var inlineNodes = Array.prototype.slice.call(
      document.querySelectorAll('.jdl-inline-btn, [data-jdl-button]'));
    var headerNodes = [];
    Array.prototype.forEach.call(document.querySelectorAll('.jdl-header-btn'), function (node) {
      headerNodes.push(node);
    });
    Array.prototype.forEach.call(document.querySelectorAll('.headerRight'), function (scope) {
      Array.prototype.forEach.call(scope.querySelectorAll('button, a'), function (node) {
        if (node.querySelector && node.querySelector('.material-icons.' + ENTRY_ICON)
          && headerNodes.indexOf(node) < 0) {
          headerNodes.push(node);
        }
      });
    });

    if (currentItemId()) {
      Array.prototype.forEach.call(headerNodes, function (node) { node.remove(); repaired += 1; });
      inlineNodes.slice(1).forEach(function (node) { node.remove(); repaired += 1; });
    } else {
      if (inlineNodes.length) {
        Array.prototype.forEach.call(inlineNodes, function (node) { node.remove(); repaired += 1; });
        var fab = document.querySelector('.jdl-fab, [data-jdl-floating]');
        if (fab) { fab.remove(); repaired += 1; }
      }
      if (headerNodes.length > 1) {
        headerNodes.slice(1).forEach(function (node) { node.remove(); repaired += 1; });
      }
    }
    if (repaired) {
      log('collapsed', repaired, 'duplicate entry node(s)');
      scheduleMount();
    }
    return repaired;
  }

  function currentItemId() {
    var match = /[?&]id=([0-9a-zA-Z-]{8,})/.exec(window.location.hash || '');
    return match ? match[1] : null;
  }

  var mountTimer = null;

  function scheduleMount() {
    if (mountTimer) { return; }
    mountTimer = setTimeout(function () {
      mountTimer = null;
      mount();
    }, 150);
  }

  function visibleDetailPage() {
    return document.querySelector('#itemDetailPage:not(.hide)')
      || document.querySelector('.itemDetailPage:not(.hide)')
      || document.querySelector('#itemDetailPage')
      || document.body;
  }

  function findActionContainer(root) {
    var selectors = ['.detailButtons', '.itemActionsBottom', '.mainDetailButtons', '.detailButtonsContainer'];
    for (var i = 0; i < selectors.length; i++) {
      var found = root.querySelector(selectors[i]);
      if (found) { return found; }
    }
    return null;
  }

  function mount() {
    mountHeaderButton();
    var id = currentItemId();
    if (!id) {
      state.lastLoggedId = 'none';
      removeButton();
      state.item = null;
      state.pendingId = null;
      return;
    }

    if (state.lastLoggedId !== id) {
      state.lastLoggedId = id;
      log('detail item id =', id);
    }

    // 按钮自带 data-jdl-item：只认「属于当前条目」的按钮，避免旧条目的按钮顶替
    var mounted = document.querySelector('[data-jdl-button], [data-jdl-floating]');
    if (mounted && mounted.getAttribute('data-jdl-item') === id) {
      return;
    }
    if (state.pendingId === id) { return; }
    state.pendingId = id;

    log('fetching item', id);
    fetchItem(id).then(function (item) {
      if (state.pendingId === id) { state.pendingId = null; }
      // 页面已切走：丢弃本次结果，否则会把上一个条目的按钮挂到新页面上
      if (currentItemId() !== id) {
        log('dropped stale mount for', id);
        return;
      }
      if (!item || !item.Type) {
        log('item payload has no Type', item ? Object.keys(item).slice(0, 6) : item);
        return;
      }
      if (item.Type !== 'Movie' && item.Type !== 'Series' && item.Type !== 'Season' && item.Type !== 'Episode') {
        log('skip item type', item.Type);
        removeButton();
        return;
      }
      state.item = item;
      removeButton();
      var box = findActionContainer(visibleDetailPage());
      if (!box) {
        log('no action container for', item.Type, '-> floating fallback');
        ensureFloating(item);
        return;
      }
      var old = box.querySelector('[data-jdl-button]');
      if (old) { old.remove(); }
      var button = buildButton(item);
      // 插在「…」（更多命令）之前；找不到就退回追加到操作行末尾
      var more = visibleDetailPage().querySelector('.btnMoreCommands');
      if (more && more.parentElement) {
        more.parentElement.insertBefore(button, more);
        log('mounted before more-commands');
      } else {
        box.appendChild(button);
        log('mounted inline into', box.className || box.tagName, '| buttons in row =', box.querySelectorAll('button').length);
      }
      if (!isVisible(button)) {
        log('inline button not visible -> floating fallback too');
        ensureFloating(item);
      }
    }).catch(function (error) {
      if (state.pendingId === id) { state.pendingId = null; }
      log('getItem failed', error && (error.status || error.message));
    });
  }

  function isVisible(node) {
    if (!node) { return false; }
    var rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && node.offsetParent !== null;
  }

  // 面板里的动作一律打在「面板自己的条目」上：手动搜索的面板打开时，
  // 详情页的 mount() 可能刚把 state.item 换成别的条目，不能让它串台。
  function panelTarget() {
    if (state.panel && state.panel.item) { return state.panel.item; }
    return state.item;
  }

  // 右上角入口（搜索 / 个人资料 图标旁）：搜「库里没有的剧集」，详情页点不到。
  // 直接克隆一个真的 header 图标按钮（搜按钮），保证尺寸/圆角/hover 与原生一致；
  // React 重渲染会把节点冲掉，所以每次 mount 都检查一遍。
  function mountHeaderButton() {
    var right = document.querySelector('.skinHeader .headerRight') || document.querySelector('.headerRight');
    if (!right) { return; }
    // 详情页已经有一个「…」旁边的获取资源图标了：只保留那一个，右上角不再重复
    // （判据用 URL 里的条目 id，不用「inline 按钮在不在」——那要等异步挂载完，
    //  中间那一小段会出现两个下载图标）
    if (currentItemId()) {
      removeHeaderButton();
      return;
    }
    // 同一页面只允许 1 个：先清掉残留副本（页面切换时 React 可能把旧 header 留在 DOM 里），
    // 恰好挂在本 header 里的那个先留着，等下面判断位置。
    var keep = null;
    Array.prototype.forEach.call(document.querySelectorAll('.jdl-header-btn'), function (node) {
      if (!keep && node.parentElement === right) { keep = node; return; }
      node.remove();
    });
    // 兜底：标记 class 被 React / 自定义元素改写过的节点，按「header 里带入口箭头」认
    // （Jellyfin 自己的 header 按钮没有这个箭头图标，所以这样认不会误伤）
    var iconButtons = [];
    Array.prototype.forEach.call(right.querySelectorAll('button, a'), function (node) {
      if (node.querySelector && node.querySelector('.material-icons.' + ENTRY_ICON)) { iconButtons.push(node); }
    });
    if (!keep && iconButtons.length) { keep = iconButtons[0]; }
    iconButtons.forEach(function (node) { if (node !== keep) { node.remove(); } });
    if (keep) {
      var next = keep.nextElementSibling;
      if (next && next.classList.contains('headerSearchButton')) { return; }
      keep.remove();
    }
    var seed = right.querySelector('.headerSearchButton')
      || right.querySelector('.headerUserButton')
      || right.querySelector('.headerCastButton');
    var button;
    if (seed) {
      button = seed.cloneNode(true);
      button.classList.remove('headerSearchButton', 'headerUserButton', 'headerCastButton', 'hide');
    } else {
      button = el('button', 'headerButton headerButtonRight');
    }
    button.classList.add('jdl-header-btn');
    button.setAttribute('data-jdl-header', '1');
    button.type = 'button';
    button.title = '\u641c\u7d22\u8d44\u6e90\u5e76\u6253\u5206\uff08\u5e93\u91cc\u6ca1\u6709\u4e5f\u884c\uff09';
    button.setAttribute('aria-label', '\u83b7\u53d6\u8d44\u6e90');
    var icon = button.querySelector('.material-icons');
    if (icon) {
      icon.className = 'material-icons ' + ENTRY_ICON;
      icon.textContent = '';
      icon.setAttribute('aria-hidden', 'true');
    } else {
      button.innerHTML = '<span class="material-icons ' + ENTRY_ICON + '" aria-hidden="true"></span>';
    }
    button.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      openManualPanel(prefillFromPage());
    });
    var search = right.querySelector('.headerSearchButton');
    if (search && search.parentElement === right) {
      right.insertBefore(button, search);
    } else {
      right.appendChild(button);
    }
    log('mounted header button');
  }

  // 删掉右上角入口的所有副本（含被 React 换掉后残留在旧 header 里的节点）
  function removeHeaderButton() {
    Array.prototype.forEach.call(document.querySelectorAll('.jdl-header-btn'), function (node) { node.remove(); });
    Array.prototype.forEach.call(document.querySelectorAll('.headerRight'), function (scope) {
      Array.prototype.forEach.call(scope.querySelectorAll('button, a'), function (node) {
        if (node.querySelector && node.querySelector('.material-icons.' + ENTRY_ICON)) { node.remove(); }
      });
    });
  }

  // 在详情页点右上角入口时，把当前条目的信息带进表单（省得手打）
  function prefillFromPage() {
    var item = state.item;
    if (!item || !item.Name || item.Manual) { return null; }
    var type = item.Type;
    if (type !== 'Movie' && type !== 'Series' && type !== 'Episode' && type !== 'Season') {
      return null;
    }
    var season = null;
    var episode = null;
    if (type === 'Episode') {
      season = item.ParentIndexNumber != null ? item.ParentIndexNumber : null;
      episode = item.IndexNumber != null ? item.IndexNumber : null;
    } else if (type === 'Season') {
      season = item.IndexNumber != null ? item.IndexNumber : null;
    }
    return {
      // 单集/季的 Name 不是剧名（「Season 1」/「第 3 集」），要用 SeriesName
      title: (type === 'Movie' || type === 'Series') ? item.Name : (item.SeriesName || item.Name),
      original_title: item.OriginalTitle || '',
      year: item.ProductionYear || '',
      kind: type === 'Movie' ? 'movie' : 'episode',
      season: season,
      episode: episode
    };
  }

  function openManualPanel(prefill) {
    // 手动搜索面板已开：不重建（改条件后点「搜索」即可）
    if (state.panel && state.panel.cacheOnly) {
      if (state.panel.form && state.panel.form.title) { state.panel.form.title.focus(); }
      return;
    }
    if (prefill) {
      manual.title = prefill.title || '';
      manual.alt = prefill.original_title || '';
      manual.year = prefill.year ? String(prefill.year) : '';
      manual.kind = prefill.kind === 'movie' ? 'movie' : 'episode';
      manual.season = prefill.season != null ? String(prefill.season) : '';
      manual.episode = prefill.episode != null ? String(prefill.episode) : '';
    }
    openPanel(manualItem());
    var titleInput = state.panel && state.panel.form && state.panel.form.title;
    if (titleInput) { titleInput.focus(); }
  }

  function ensureFloating(item) {
    var existing = document.querySelector('[data-jdl-floating]');
    if (existing) { existing.remove(); }
    var button = el('button', 'jdl-fab');
    button.setAttribute('data-jdl-floating', '1');
    button.setAttribute('data-jdl-item', item.Id || '');
    button.type = 'button';
    button.title = '获取资源';
    button.innerHTML = '<span class="material-icons ' + ENTRY_ICON + '" aria-hidden="true"></span><span class="jdl-fab__text">获取资源</span>';
    button.onclick = function (event) {
      event.preventDefault();
      event.stopPropagation();
      openPanel(item);
    };
    document.body.appendChild(button);
  }

  // 不依赖 ApiClient.getItem 的参数签名（旧版是 getItem(userId, itemId)），直接走 REST。
  function fetchItem(id) {
    var client = window.ApiClient;
    var userId = null;
    try {
      userId = client.getCurrentUserId ? client.getCurrentUserId() : null;
    } catch (error) {
      userId = null;
    }
    var path = userId
      ? '/Users/' + userId + '/Items/' + id
      : '/Items/' + id;
    return client.ajax({ url: client.getUrl(path), type: 'GET', dataType: 'json' });
  }

  function removeButton() {
    // 按 class 也认一遍：React 重建操作行时可能把 data-* 属性弄丢，
    // 只剩下面这层 class 还能认出「这是我们插的那个」。
    var nodes = document.querySelectorAll(
      '[data-jdl-button], [data-jdl-floating], .jdl-inline-btn, .jdl-fab');
    for (var i = 0; i < nodes.length; i++) { nodes[i].remove(); }
  }

  function buildButton(item) {
    // 纯图标按钮：与详情页其它图标按钮（含「…」）同款同尺寸
    var button = el('button', 'button-flat paper-icon-button-light jdl-inline-btn');
    button.setAttribute('data-jdl-button', '1');
    button.setAttribute('data-jdl-item', item.Id || '');
    button.type = 'button';
    button.title = '获取资源';
    button.setAttribute('aria-label', '获取资源');
    var icon = el('span', 'material-icons ' + ENTRY_ICON);
    icon.setAttribute('aria-hidden', 'true');
    button.appendChild(icon);
    button.onclick = function (event) {
      event.preventDefault();
      event.stopPropagation();
      openPanel(item);
    };
    return button;
  }

  function toInt(value) {
    var number = parseInt(value, 10);
    return isNaN(number) ? null : number;
  }

  // 表单 → 搜索规格
  function manualSpec() {
    return {
      title: String(manual.title || '').trim(),
      original_title: String(manual.alt || '').trim(),
      year: String(manual.year || '').trim(),
      kind: manual.kind === 'movie' ? 'movie' : 'episode',
      season: toInt(manual.season),
      episode: toInt(manual.episode)
    };
  }

  function manualSearchParams() {
    var spec = manualSpec();
    var params = { title: spec.title, kind: spec.kind, year: spec.year };
    if (spec.original_title) { params.original_title = spec.original_title; }
    if (spec.kind === 'episode') {
      if (spec.season != null) { params.season = spec.season; }
      if (spec.episode != null) { params.episode = spec.episode; }
    }
    params.sources = currentSources();
    params.strict_season = !state.panel || state.panel.strictSeason !== false;
    return params;
  }

  // 合成一个「假条目」，让面板沿用详情页那套渲染/动作逻辑
  function manualItem() {
    var spec = manualSpec();
    return {
      Id: 'jdl-manual',
      Manual: true,
      Type: spec.kind === 'movie' ? 'Movie' : 'Series',
      Name: spec.title,
      OriginalTitle: spec.original_title,
      ProductionYear: spec.year || null
    };
  }

  function manualSubtitle() {
    var spec = manualSpec();
    var bits = ['\u624b\u52a8\u641c\u7d22'];
    if (spec.kind === 'movie') {
      bits.push('\u7535\u5f71');
    } else if (spec.season != null && spec.episode != null) {
      bits.push('S' + ('0' + spec.season).slice(-2) + 'E' + ('0' + spec.episode).slice(-2));
    } else if (spec.season != null) {
      bits.push('\u7b2c ' + spec.season + ' \u5b63');
    } else {
      bits.push('\u5267\u96c6\u6574\u5b63');
    }
    return bits.join(' \u00b7 ') + '\uff08\u5e93\u91cc\u6ca1\u6709\u4e5f\u80fd\u641c\uff09';
  }

  function searchParams(item) {
    var params;
    if (item && item.Manual) { return manualSearchParams(); }
    var seasonPick = seasonScope();
    if (item.Type === 'Movie') {
      params = { title: item.Name, kind: 'movie', year: item.ProductionYear || '' };
    } else if (item.Type === 'Season') {
      // 季详情页：默认就是这一季
      params = { title: item.SeriesName || item.Name, kind: 'episode' };
      var seasonNumber = item.IndexNumber != null ? item.IndexNumber : (seasonPick === 'all' ? null : seasonPick);
      if (seasonNumber != null) { params.season = seasonNumber; }
      if (item.ProductionYear) { params.year = String(item.ProductionYear); }
    } else if (item.Type === 'Series') {
      params = { title: item.Name, kind: 'episode', year: item.ProductionYear || '' };
      if (seasonPick !== 'all') { params.season = seasonPick; }
    } else {
      params = { title: item.SeriesName || item.Name, kind: 'episode', year: '' };
      if (item.ParentIndexNumber != null) { params.season = item.ParentIndexNumber; }
      if (item.IndexNumber != null) { params.episode = item.IndexNumber; }
    }
    // 抓取范围 = 面板当前选中的类型（默认仅磁力）；改选后需点「重新获取」才生效
    params.sources = currentSources();
    // 英文原名：既当别名喂给搜索（提升召回），也用于「同名噪音」判定
    //（王冠 → 空王冠 / 罪恶王冠 / 9-nine-支配者的王冠）
    if (item.OriginalTitle && item.OriginalTitle !== params.title) {
      params.original_title = item.OriginalTitle;
    }
    // 严格按季：只用「第 N 季 / SNN」关键词检索，避免基础片名把整部剧
    //（其它季）和同名影片一起带回来。默认开启，可在面板里关掉。
    params.strict_season = !state.panel || state.panel.strictSeason !== false;
    return params;
  }

  function seasonScope() {
    if (!state.panel) { return 'all'; }
    var value = state.panel.seasonFilter;
    if (value === 'all' || value == null || value === '') { return 'all'; }
    var number = parseInt(value, 10);
    return isNaN(number) ? 'all' : number;
  }

  function currentSources() {
    var kind = state.panel ? (state.panel.kindFilter || 'magnet') : 'magnet';
    if (kind === 'all') { return 'both'; }
    if (kind === 'pan') { return 'pan'; }
    return 'magnet';
  }

  function subtitleFor(item) {
    var base = item.Name || '';
    if (item.ProductionYear) { base += ' (' + item.ProductionYear + ')'; }
    if (item.Type === 'Episode') {
      var season = String(item.ParentIndexNumber == null ? 0 : item.ParentIndexNumber);
      var number = String(item.IndexNumber == null ? 0 : item.IndexNumber);
      return (item.SeriesName || '') + ' · S' + ('0' + season).slice(-2) + 'E' + ('0' + number).slice(-2);
    }
    return base + (item.Type === 'Series' ? ' · 剧集整季' : ' · 电影');
  }

  // 表单改动 → 提示「按新条件搜索」（不自动开抓：搜索一次要几分钟）
  function markManualDirty() {
    var panel = state.panel;
    if (!panel || !panel.cacheOnly) { return; }
    panel.dirtySpec = true;
    if (panel.subEl) { panel.subEl.textContent = manualSubtitle(); }
    renderCandidates();
  }

  // 手动搜索表单：右上角入口用（搜库里没有的剧集/电影，没有详情页可点）
  function buildManualForm(item) {
    var node = el('div', 'jdl-form');
    var inputs = {};

    function field(label, name, opts) {
      opts = opts || {};
      var wrap = el('label', 'jdl-field' + (opts.wide ? ' jdl-field--wide' : '') + (opts.narrow ? ' jdl-field--narrow' : ''));
      wrap.appendChild(el('span', 'jdl-field__label', label));
      var input = el('input', 'jdl-input');
      input.type = 'text';
      input.value = manual[name] || '';
      if (opts.placeholder) { input.placeholder = opts.placeholder; }
      input.addEventListener('input', function () {
        manual[name] = input.value;
        markManualDirty();
      });
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          checkBackendThenSearch(panelTarget());
        }
      });
      wrap.appendChild(input);
      inputs[name] = input;
      node.appendChild(wrap);
      return input;
    }

    field('\u7247\u540d', 'title', { wide: true, placeholder: '\u5e93\u91cc\u6ca1\u6709\u4e5f\u53ef\u4ee5\uff0c\u5982\uff1a\u738b\u51a0' });
    field('\u522b\u540d / \u539f\u540d', 'alt', { wide: true, placeholder: '\u53ef\u9009\uff0c\u5982\uff1aThe Crown\uff08\u63d0\u9ad8\u53ec\u56de\uff09' });
    field('\u5e74\u4efd', 'year', { narrow: true, placeholder: '2016' });

    var kindWrap = el('label', 'jdl-field jdl-field--narrow');
    kindWrap.appendChild(el('span', 'jdl-field__label', '\u7c7b\u578b'));
    var kindSelect = el('select', 'jdl-select');
    [['episode', '\u5267\u96c6 / \u7efc\u827a'], ['movie', '\u7535\u5f71']].forEach(function (pair) {
      var option = el('option', null, pair[1]);
      option.value = pair[0];
      if (manual.kind === pair[0]) { option.selected = true; }
      kindSelect.appendChild(option);
    });
    kindWrap.appendChild(kindSelect);
    node.appendChild(kindWrap);
    inputs.kind = kindSelect;

    var seasonWrap = field('\u5b63', 'season', { narrow: true, placeholder: '\u5982 1' });
    var episodeWrap = field('\u96c6', 'episode', { narrow: true, placeholder: '\u5982 3' });
    var seasonField = seasonWrap.parentElement;
    var episodeField = episodeWrap.parentElement;

    function syncKind() {
      var isEpisode = manual.kind !== 'movie';
      seasonField.style.display = isEpisode ? '' : 'none';
      episodeField.style.display = isEpisode ? '' : 'none';
    }

    kindSelect.addEventListener('change', function () {
      manual.kind = kindSelect.value;
      syncKind();
      markManualDirty();
    });
    syncKind();

    node.appendChild(el('div', 'jdl-form__tip',
      '\u5e93\u91cc\u6ca1\u6709\u4e5f\u80fd\u641c\uff1b\u586b\u4e86\u300c\u5b63\u300d\u5c31\u53ea\u641c\u8fd9\u4e00\u5b63\uff0c\u7559\u7a7a\u5219\u641c\u6574\u5267\u3002'));

    return { node: node, inputs: inputs };
  }

  function openPanel(item) {
    var opened = document.querySelector('[data-jdl-panel]');
    if (opened) {
      // 同一个条目就不重复打开；不同条目则关掉旧面板，避免显示上一个条目的结果
      var open = state.panel && state.panel.item;
      if (open && item && open.Id === item.Id) { return; }
      closePanel();
    }
    log('openPanel', item.Type, item.Name, '-> search title =', searchParams(item).title);
    var backdrop = el('div', 'jdl-backdrop');
    backdrop.setAttribute('data-jdl-panel', '1');
    var theme = themeClass();
    if (theme) { backdrop.classList.add(theme); }
    var dialog = el('div', 'jdl-dialog');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');

    var head = el('div', 'jdl-head');
    var titles = el('div', 'jdl-head__titles');
    titles.appendChild(el('div', 'jdl-head__title', '获取资源'));
    var subEl = el('div', 'jdl-head__sub', item.Manual ? manualSubtitle() : subtitleFor(item));
    titles.appendChild(subEl);
    head.appendChild(titles);
    var close = el('button', 'jdl-iconbtn');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭');
    close.innerHTML = '<span class="material-icons">close</span>';
    close.addEventListener('click', closePanel);
    head.appendChild(close);
    dialog.appendChild(head);

    // 手动搜索表单：右上角入口用（库里没有的剧集/电影也能搜，没有详情页可点）
    var form = item.Manual ? buildManualForm(item) : null;
    if (form) { dialog.appendChild(form.node); }

    var backend = el('div', 'jdl-backend');
    backend.style.display = 'none';
    var backendText = el('span', 'jdl-backend__text', '\u540e\u7aef\u672a\u8fd0\u884c');
    backend.appendChild(backendText);
    var backendStart = el('button', 'jdl-btn jdl-btn--primary', '\u542f\u52a8\u540e\u7aef');
    backendStart.type = 'button';
    backendStart.addEventListener('click', function () { startBackend(backendStart, backendText); });
    backend.appendChild(backendStart);
    dialog.appendChild(backend);

    var bar = el('div', 'jdl-bar');
    var summary = el('div', 'jdl-bar__summary', '搜索中…');
    var controls = el('div', 'jdl-bar__controls');

    var seasonSelect = el('select', 'jdl-select');
    seasonSelect.style.display = 'none';
    seasonSelect.title = '选择季：只展示该季候选；点「重新获取」时按此季抓取';
    seasonSelect.addEventListener('change', function () {
      if (state.panel) {
        state.panel.seasonFilter = seasonSelect.value;
        state.panel.snapshotRetried = false;
      }
      // 换季 = 读该季自己的快照（互不覆盖），不触发搜索
      if (panelTarget()) { loadExisting(panelTarget()); } else { renderCandidates(); }
    });
    controls.appendChild(seasonSelect);

    // 只在选了具体某一季时才有意义的两个开关：
    //  严格按季 —— 搜索关键词只用季关键词（默认开）
    //  含整季包 —— 允许把「没标季号」的整剧包也算进这一季（默认关）
    var strictLabel = el('label', 'jdl-toggle');
    var strictBox = el('input');
    strictBox.type = 'checkbox';
    strictBox.checked = true;
    strictBox.addEventListener('change', function () {
      if (state.panel) { state.panel.strictSeason = strictBox.checked; }
      renderCandidates();
    });
    strictLabel.appendChild(strictBox);
    strictLabel.appendChild(el('span', null, '\u4e25\u683c\u6309\u5b63'));
    strictLabel.title = '\u5f00\uff1a\u53ea\u7528\u300c\u7b2c N \u5b63 / SNN\u300d\u5173\u952e\u8bcd\u641c\u7d22\uff08\u63a8\u8350\uff09\uff1b\u5173\uff1a\u540c\u65f6\u7528\u57fa\u7840\u7247\u540d\u641c\u7d22\uff0c\u53ec\u56de\u66f4\u591a\u4f46\u4f1a\u6df7\u5165\u5176\u5b83\u5b63';
    strictLabel.style.display = 'none';
    controls.appendChild(strictLabel);

    var packLabel = el('label', 'jdl-toggle');
    var packBox = el('input');
    packBox.type = 'checkbox';
    packBox.checked = false;
    packBox.addEventListener('change', function () {
      if (state.panel) { state.panel.includePacks = packBox.checked; }
      renderCandidates();
    });
    packLabel.appendChild(packBox);
    packLabel.appendChild(el('span', null, '\u542b\u6574\u5b63\u5305'));
    packLabel.title = '\u5f00\uff1a\u628a\u300c\u6ca1\u6807\u5b63\u53f7\u300d\u7684\u6574\u5267\u5305\u4e5f\u7b97\u8fdb\u8fd9\u4e00\u5b63\uff08\u53ef\u80fd\u662f\u771f\u6574\u5267\u5305\uff0c\u4e5f\u53ef\u80fd\u662f\u522b\u7684\u5b63\uff09';
    packLabel.style.display = 'none';
    controls.appendChild(packLabel);

    var threshold = el('select', 'jdl-select');
    [60, 65, 70, 75, 80].forEach(function (value) {
      var option = el('option', null, '\u2265 ' + value + ' \u5206');
      option.value = String(value);
      if (value === Math.round(state.config.minScore || 60)) { option.selected = true; }
      threshold.appendChild(option);
    });
    threshold.addEventListener('change', renderCandidates);
    controls.appendChild(threshold);
    var onlyEpisode = el('label', 'jdl-toggle');
    var onlyCheckbox = el('input');
    onlyCheckbox.type = 'checkbox';
    onlyCheckbox.checked = true;
    onlyCheckbox.addEventListener('change', renderCandidates);
    onlyEpisode.appendChild(onlyCheckbox);
    onlyEpisode.appendChild(el('span', null, '\u53ea\u770b\u672c\u96c6'));
    onlyEpisode.title = '\u53ea\u770b\u80fd\u8986\u76d6\u672c\u96c6\u7684\u5019\u9009\uff1a\u300c\u4ec5\u672c\u96c6\u300d\u6392\u5728\u524d\u9762\uff0c\u6574\u5305\u6392\u5728\u540e\u9762\uff08\u6574\u5305\u4e5f\u8986\u76d6\u672c\u96c6\uff09\uff1b\u4e0b\u65b9\u63d0\u793a\u6761\u53ef\u4e00\u952e\u53ea\u770b\u4ec5\u672c\u96c6';
    onlyEpisode.style.display = 'none';
    controls.appendChild(onlyEpisode);

    var segment = el('div', 'jdl-seg');
    var segAll = makeSegment('\u5168\u90e8', 'all');
    var segMagnet = makeSegment('\u78c1\u529b', 'magnet');
    var segPan = makeSegment('\u4e91\u76d8', 'pan');
    segment.appendChild(segAll.button);
    segment.appendChild(segMagnet.button);
    segment.appendChild(segPan.button);
    controls.appendChild(segment);

    var refetch = el('button', 'jdl-btn jdl-btn--primary',
      item.Manual ? '\u641c\u7d22' : '\u91cd\u65b0\u83b7\u53d6');
    refetch.type = 'button';
    refetch.title = item.Manual ? '\u6309\u5f53\u524d\u6761\u4ef6\u641c\u7d22\u5e76\u6253\u5206'
      : '\u91cd\u65b0\u641c\u7d22\u5e76\u6253\u5206';
    refetch.addEventListener('click', function () { checkBackendThenSearch(item); });
    controls.appendChild(refetch);

    bar.appendChild(summary);
    bar.appendChild(controls);
    dialog.appendChild(bar);

    // 「当前选择与上次抓取不同」的提示条：独立一行，点按钮即可按当前选择重抓
    var hint = el('div', 'jdl-hint');
    hint.style.display = 'none';
    dialog.appendChild(hint);

    // 「被过滤掉多少」的提示条：同名噪音 / 没标季号的整剧包
    var note = el('div', 'jdl-note');
    note.style.display = 'none';
    dialog.appendChild(note);

    var progress = el('div', 'jdl-progress');
    progress.appendChild(el('div', 'jdl-progress__bar'));
    progress.appendChild(el('div', 'jdl-progress__stage', '\u6b63\u5728\u8fde\u63a5\u540e\u7aef\u2026'));
    // 注意：局部变量不要叫 log/start/… —— 会遮蔽同名顶层函数（var 提升后调用即 TypeError）
    var logEl = el('pre', 'jdl-log');
    progress.appendChild(logEl);
    dialog.appendChild(progress);

    var list = el('div', 'jdl-list');
    dialog.appendChild(list);

    var tasks = el('div', 'jdl-tasks');
    tasks.style.display = 'none';
    dialog.appendChild(tasks);

    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);
    state.panel = {
      item: item,
      backdrop: backdrop, summary: summary, progress: progress, log: logEl,
      list: list, tasks: tasks, threshold: threshold, onlyEpisode: onlyCheckbox,
      backend: backend, refetch: refetch, kindFilter: 'magnet',
      segAll: segAll, segMagnet: segMagnet, segPan: segPan,
      seasonSelect: seasonSelect,
      strictSeason: true, strictBox: strictBox, strictLabel: strictLabel,
      includePacks: false, packBox: packBox, packLabel: packLabel,
      showAll: false, hidePacks: false,
      hint: hint, note: note,
      subEl: subEl,
      form: form ? form.inputs : null,
      // 手动搜索：只读缓存 + 等用户点「搜索」，绝不自动开抓
      cacheOnly: !!item.Manual,
      seasonFilter: item.Type === 'Season' && item.IndexNumber != null ? String(item.IndexNumber) : 'all'
    };

    document.addEventListener('keydown', onKeyDown);
    backdrop.addEventListener('mousedown', function (event) {
      if (event.target === backdrop) { closePanel(); }
    });
    // 手动搜索没有「这一部剧的季列表」可拉，季由表单里的「季」输入框决定
    if (!item.Manual) { loadSeasons(item); }
    loadExisting(item);
  }

  // 剧集/季 详情页：把该剧的季列表填进选择器
  function loadSeasons(item) {
    var panel = state.panel;
    if (!panel || !panel.seasonSelect) { return; }
    if (item.Type !== 'Series' && item.Type !== 'Season') { return; }
    var seriesId = item.Type === 'Season' ? item.SeriesId : item.Id;
    if (!seriesId) { return; }
    var client = window.ApiClient;
    var userId = null;
    try { userId = client.getCurrentUserId ? client.getCurrentUserId() : null; } catch (error) { userId = null; }
    var select = panel.seasonSelect;
    // 先把入口显示出来（至少可见「全部季」），避免"没有选季的入口"的错觉
    select.innerHTML = '';
    var placeholder = el('option', null, '\u5168\u90e8\u5b63');
    placeholder.value = 'all';
    select.appendChild(placeholder);
    select.value = 'all';
    select.style.display = '';

    var q = userId ? 'userId=' + userId + '&' : '';
    var attempts = [
      { name: 'Shows/Seasons', url: client.getUrl('/Shows/' + seriesId + '/Seasons' + (userId ? '?userId=' + userId : '')) },
      { name: 'Items(parentId)', url: client.getUrl('/Items?' + q + 'parentId=' + seriesId + '&includeItemTypes=Season&sortBy=SortName') },
      { name: 'Users/Items(parentId)', url: userId ? client.getUrl('/Users/' + userId + '/Items?parentId=' + seriesId + '&includeItemTypes=Season&sortBy=SortName') : '' },
      { name: 'Shows/Episodes(derive)', url: client.getUrl('/Shows/' + seriesId + '/Episodes' + (userId ? '?userId=' + userId : '')), derive: true }
    ];

    function fill(seasons) {
      select.innerHTML = '';
      var all = el('option', null, '\u5168\u90e8\u5b63');
      all.value = 'all';
      select.appendChild(all);
      var count = 0;
      seasons.forEach(function (season) {
        var number = season.IndexNumber != null ? season.IndexNumber : season.indexNumber;
        if (number == null) { return; }
        // IndexNumber 0 = Jellyfin 的 Specials（特别篇/花絮），别显示成「第 0 季」
        var label = number === 0 ? '\u7279\u522b\u7bc7' : '\u7b2c ' + number + ' \u5b63';
        var option = el('option', null, label);
        option.value = String(number);
        select.appendChild(option);
        count += 1;
      });
      select.value = String(panel.seasonFilter);
      if (!select.value) { select.value = 'all'; }
      log('seasons loaded', count, 'current =', select.value);
    }

    // 从剧集列表反推季号（有些版本没有 Seasons 路由时的兜底）
    function deriveFromEpisodes(items) {
      var seen = {};
      var out = [];
      (items || []).forEach(function (episode) {
        var number = episode.ParentIndexNumber != null ? episode.ParentIndexNumber : episode.parentIndexNumber;
        if (number == null || seen[number]) { return; }
        seen[number] = true;
        out.push({ IndexNumber: number });
      });
      out.sort(function (a, b) { return a.IndexNumber - b.IndexNumber; });
      return out;
    }

    function tryAt(index) {
      if (index >= attempts.length) {
        log('seasons: all attempts failed');
        toast('\u5b63\u5217\u8868\u52a0\u8f7d\u5931\u8d25\uff0c\u9ed8\u8ba4\u6309\u300c\u5168\u90e8\u5b63\u300d\u5904\u7406');
        return;
      }
      var attempt = attempts[index];
      if (!attempt.url) { tryAt(index + 1); return; }
      log('seasons attempt', index + 1, attempt.name, attempt.url);
      client.ajax({ url: attempt.url, type: 'GET', dataType: 'json' }).then(function (response) {
        var items = response && response.Items ? response.Items : response;
        var seasons = attempt.derive ? deriveFromEpisodes(items) : items;
        if (Array.isArray(seasons) && seasons.length) {
          log('seasons ok via', attempt.name, seasons.length);
          fill(seasons);
          return;
        }
        log('seasons empty via', attempt.name);
        tryAt(index + 1);
      }).catch(function (error) {
        log('seasons failed via', attempt.name, error && (error.status || error.message));
        tryAt(index + 1);
      });
    }

    // 首选插件端点：同源、已鉴权，服务端直接用 ILibraryManager 查季表
    pluginApi('seasons?seriesId=' + encodeURIComponent(seriesId))
      .then(function (list) {
        if (Array.isArray(list) && list.length) {
          log('seasons ok via plugin', list.length);
          fill(list.map(function (row) { return { IndexNumber: row.index, Name: row.name }; }));
          return;
        }
        log('plugin seasons empty -> client cascade');
        tryAt(0);
      })
      .catch(function (error) {
        log('plugin seasons failed', error && (error.status || error.message), '-> client cascade');
        tryAt(0);
      });
  }

  function makeSegment(label, kind) {
    var button = el('button', 'jdl-seg__item');
    button.type = 'button';
    button.title = '\u5c55\u793a\u8be5\u7c7b\u578b\uff1b\u70b9\u300c\u91cd\u65b0\u83b7\u53d6\u300d\u65f6\u6309\u6b64\u8303\u56f4\u6293\u53d6';
    button.appendChild(el('span', null, label));
    var count = el('span', 'jdl-seg__count', '0');
    button.appendChild(count);
    button.addEventListener('click', function () {
      if (state.panel) { state.panel.kindFilter = kind; }
      renderCandidates();
    });
    return { button: button, count: count };
  }

  function formatWhen(value) {
    if (!value) { return ''; }
    var match = /(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
    if (!match) { return ''; }
    var today = new Date();
    var sameDay = today.getFullYear() === Number(match[1])
      && (today.getMonth() + 1) === Number(match[2])
      && today.getDate() === Number(match[3]);
    return (sameDay ? '' : match[2] + '-' + match[3] + ' ') + match[4] + ':' + match[5];
  }

  // 打开面板：先展示上次获取过的资源；没有历史才自动跑一次。
  function loadExisting(item) {
    var panel = state.panel;
    var params = searchParams(item);
    if (!params.title) {
      // 手动搜索还没填片名：只显示表单，不发任何请求
      if (panel) {
        panel.awaitingFirstFetch = true;
        panel.summary.textContent = '\u8f93\u5165\u7247\u540d\u540e\u70b9\u300c\u641c\u7d22\u300d';
      }
      renderCandidates();
      return;
    }
    var url = 'snapshot?title=' + encodeURIComponent(params.title);
    if (params.season != null) { url += '&season=' + encodeURIComponent(params.season); }
    if (panel) { panel.summary.textContent = '\u6b63\u5728\u52a0\u8f7d\u5df2\u6709\u8d44\u6e90\u2026'; }
    api(url).then(function (snapshot) {
      snapshot = snapshot || {};
      var list = snapshot.candidates || [];
      state.snapshot = snapshot;
      state.slug = snapshot.slug || '';
      if (list.length) {
        log('showing cached candidates', list.length);
        if (panel) { panel.fromCache = true; }
        finishProgress('\u5df2\u52a0\u8f7d ' + (formatWhen(snapshot.generated_at) || '\u5386\u53f2') + ' \u7684\u7ed3\u679c');
        renderCandidates();
        startMonitoring();
      } else {
        log('no cached candidates -> first fetch');
        // 手动搜索：不自动开搜，等用户点「搜索」
        if (panel && panel.cacheOnly) {
          panel.awaitingFirstFetch = true;
          renderCandidates();
          return;
        }
        if (item.Type === 'Series') {
          // 多季剧集：不自动抓「全部季」，等用户选季（或保持全部季）后点「重新获取」
          if (panel) { panel.awaitingFirstFetch = true; }
          log('series page -> waiting for season pick + 重新获取');
          renderCandidates();
        } else {
          if (panel) { panel.summary.textContent = '\u6ca1\u6709\u5386\u53f2\u8bb0\u5f55\uff0c\u6b63\u5728\u9996\u6b21\u83b7\u53d6\u2026'; }
          checkBackendThenSearch(item);
        }
      }
    }).catch(function (error) {
      log('snapshot load failed', error && error.status);
      var retried = panel && panel.snapshotRetried;
      if (panel && !retried) {
        // 多数情况是后端还没起（Jellyfin 重启后按需启动）→ 只拉起后端再重读缓存，
        // 绝不因为"读快照失败"就自动发起搜索。
        panel.snapshotRetried = true;
        log('starting backend then retry snapshot (no search)');
        ensureBackendOnly(function () { loadExisting(item); });
        return;
      }
      if (panel && panel.cacheOnly) {
        panel.awaitingFirstFetch = true;
        renderCandidates();
        return;
      }
      if (item.Type === 'Series') {
        // 剧集页始终等用户选季 + 点「重新获取」
        if (panel) { panel.awaitingFirstFetch = true; }
        renderCandidates();
        return;
      }
      checkBackendThenSearch(item);
    });
  }

  // 只保证后端在跑，不发起任何搜索
  function ensureBackendOnly(done) {
    var panel = state.panel;
    pluginApi('backend/status').then(function (status) {
      if (status && status.running) {
        if (panel) { panel.backend.style.display = 'none'; }
        done();
        return;
      }
      if (panel) {
        panel.backend.style.display = '';
        panel.backend.querySelector('.jdl-backend__text').textContent = '\u540e\u7aef\u672a\u8fd0\u884c\uff0c\u6b63\u5728\u542f\u52a8\u2026';
      }
      pluginApi('backend/start', { type: 'POST', data: {} }).then(function (result) {
        if (panel) { panel.backend.style.display = 'none'; }
        if (result && result.ok) { toast(result.detail || '\u540e\u7aef\u5df2\u542f\u52a8'); }
        done();
      }).catch(function () { done(); });
    }).catch(function () { done(); });
  }

  function finishProgress(text) {
    var panel = state.panel;
    if (!panel) { return; }
    panel.progress.classList.add('jdl-progress--done');
    panel.progress.querySelector('.jdl-progress__stage').textContent = text || '';
  }

  function checkBackendThenSearch(item) {
    var panel = state.panel;
    pluginApi('backend/status').then(function (status) {
      if (status && status.running) {
        if (panel) { panel.backend.style.display = 'none'; }
        runSearch(item);
        return;
      }
      // 后端未运行：面板内自动拉起，再开始搜索
      if (!panel) { runSearch(item); return; }
      panel.backend.style.display = '';
      panel.backend.querySelector('.jdl-backend__text').textContent = '\u540e\u7aef\u672a\u8fd0\u884c\uff0c\u6b63\u5728\u81ea\u52a8\u542f\u52a8\u2026';
      startBackend(panel.backend.querySelector('button'), panel.backend.querySelector('.jdl-backend__text'), item);
    }).catch(function () {
      // 状态未知：直接试搜索，让服务端给出明确结果
      runSearch(item);
    });
  }

  function startBackend(button, textEl, item) {
    if (button) { button.disabled = true; }
    if (textEl) { textEl.textContent = '\u6b63\u5728\u542f\u52a8\u540e\u7aef\u2026'; }
    pluginApi('backend/start', { type: 'POST', data: {} }).then(function (result) {
      if (button) { button.disabled = false; }
      if (result && result.ok) {
        toast(result.detail || '\u540e\u7aef\u5df2\u542f\u52a8');
        var panel = state.panel;
        if (panel) { panel.backend.style.display = 'none'; }
        var target = item || panelTarget();
        setTimeout(function () { if (target) { runSearch(target); } }, 1500);
      } else if (textEl) {
        textEl.textContent = (result && result.detail) ? result.detail : '\u542f\u52a8\u5931\u8d25';
      }
    }).catch(function (error) {
      button.disabled = false;
      if (textEl) {
        textEl.textContent = '\u542f\u52a8\u5931\u8d25\uff1a' + (error && error.status ? error.status : '\u7f51\u7edc\u9519\u8bef');
      }
    });
  }

  function onKeyDown(event) {
    if (event.key === 'Escape') { closePanel(); }
  }

  function closePanel() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    if (state.monitorTimer) { clearInterval(state.monitorTimer); state.monitorTimer = null; }
    var panel = document.querySelector('[data-jdl-panel]');
    if (panel) { panel.remove(); }
    state.panel = null;
    document.removeEventListener('keydown', onKeyDown);
  }

  function runSearch(item) {
    var current = state.panel;
    if (!searchParams(item).title) {
      toast('\u5148\u586b\u7247\u540d');
      if (current && current.form && current.form.title) { current.form.title.focus(); }
      return;
    }
    if (current) {
      current.awaitingFirstFetch = false;
      current.fromCache = false;
      current.dirtySpec = false;
      if (current.subEl && current.cacheOnly) { current.subEl.textContent = manualSubtitle(); }
      current.summary.textContent = '\u6b63\u5728\u83b7\u53d6\u2026';
      current.progress.classList.remove('jdl-progress--done');
      current.progress.querySelector('.jdl-progress__stage').textContent = '\u6b63\u5728\u8fde\u63a5\u540e\u7aef\u2026';
    }
    api('search', { type: 'POST', data: searchParams(item) }).then(function (result) {
      if (!result || result.error) {
        failSearch(result && result.error ? result.error : '\u641c\u7d22\u8bf7\u6c42\u5931\u8d25');
        return;
      }
      if (!result.job_id) {
        failSearch('\u540e\u7aef\u672a\u8fd4\u56de\u4efb\u52a1\uff08\u8bf7\u786e\u8ba4\u540e\u7aef\u4e3a\u65b0\u7248\u5e76\u5df2\u91cd\u542f\uff09');
        return;
      }
      state.slug = result.slug || '';
      pollSearch(result.job_id, 0);
    }).catch(function () {
      failSearch('\u540e\u7aef\u4e0d\u53ef\u8fbe\uff08127.0.0.1:8123\uff09', true);
    });
  }

  function pollSearch(jobId, seen) {
    // 后端重启（Jellyfin 重启 / 配置页关了再开）会让 job 记录随进程消失，
    // 状态接口对旧 job 直接 404（unknown job）。这时候必须判定为「搜索已中断」
    // 并给出重试入口，否则面板会一直显示「正在获取…」——看起来像卡死。
    var misses = 0;
    state.timer = setInterval(function () {
      api('search/status?job=' + encodeURIComponent(jobId)).then(function (result) {
        if (!result) { return; }
        if (result.error) {
          if (/unknown\s*job/i.test(String(result.error))) {
            failSearch('\u641c\u7d22\u5df2\u4e2d\u65ad\uff08\u540e\u7aef\u91cd\u542f\u540e\u4efb\u52a1\u8bb0\u5f55\u4e22\u5931\uff09\uff0c\u70b9\u300c\u91cd\u8bd5\u300d\u91cd\u65b0\u83b7\u53d6', false);
            return;
          }
          misses += 1;
          if (misses >= 5) { failSearch('\u8fdb\u5ea6\u67e5\u8be2\u5931\u8d25\uff1a' + result.error, false); }
          return;
        }
        misses = 0;
        var panel = state.panel;
        if (!panel) { return; }
        var lines = result.lines || [];
        if (lines.length > seen) {
          panel.log.textContent = lines.join('\n');
          panel.log.scrollTop = panel.log.scrollHeight;
          seen = lines.length;
        }
        var stage = (lines[lines.length - 1] || '').trim();
        if (stage) { panel.progress.querySelector('.jdl-progress__stage').textContent = stage; }
        if (result.done) {
          clearInterval(state.timer);
          state.timer = null;
          if (result.error) { failSearch(result.error); return; }
          finishProgress('\u83b7\u53d6\u5b8c\u6210');
          state.slug = result.slug || state.slug;
          state.snapshot = result.snapshot || {};
          var status = result.search_status;
          panel.summary.textContent = status && status !== 'ok'
            ? '\u641c\u7d22\u5b8c\u6210\uff08' + status + '\uff09'
            : '\u641c\u7d22\u5b8c\u6210';
          renderCandidates();
          startMonitoring();
        }
      }).catch(function () {
        // 状态接口 404 / 后端不可达都走这里：连续 6 次（约 5 秒）才判定中断，
        // 避免一次网络抖动就把搜索判死。
        misses += 1;
        if (misses >= 6) {
          failSearch('\u641c\u7d22\u5df2\u4e2d\u65ad\uff08\u540e\u7aef\u4e0d\u53ef\u8fbe\u6216\u5df2\u91cd\u542f\uff09\uff0c\u70b9\u300c\u91cd\u8bd5\u300d\u91cd\u65b0\u83b7\u53d6', true);
        }
      });
    }, 800);
  }

  function failSearch(message, offerStart) {
    var panel = state.panel;
    if (!panel) { return; }
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    panel.progress.classList.add('jdl-progress--done');
    panel.progress.querySelector('.jdl-progress__stage').textContent = message;
    panel.summary.textContent = message;
    panel.list.innerHTML = '';
    var empty = el('div', 'jdl-empty');
    empty.appendChild(el('div', 'jdl-empty__icon material-icons', 'cloud_off'));
    empty.appendChild(el('div', 'jdl-empty__text', message));
    if (offerStart) {
      var startBtn = el('button', 'jdl-btn jdl-btn--primary', '\u542f\u52a8\u540e\u7aef');
      startBtn.type = 'button';
      startBtn.addEventListener('click', function () {
        panel.progress.querySelector('.jdl-progress__stage').textContent = '\u6b63\u5728\u542f\u52a8\u540e\u7aef\u2026';
        startBackend(startBtn, null);
      });
      empty.appendChild(startBtn);
    }
    var retry = el('button', 'jdl-btn jdl-btn--ghost', '\u91cd\u8bd5');
    retry.type = 'button';
    retry.addEventListener('click', function () {
      panel.progress.querySelector('.jdl-progress__stage').textContent = '\u6b63\u5728\u8fde\u63a5\u540e\u7aef\u2026';
      panel.progress.classList.remove('jdl-progress--done');
      panel.list.innerHTML = '';
      checkBackendThenSearch(panelTarget());
    });
    empty.appendChild(retry);
    panel.list.appendChild(empty);
  }

  // 当前生效的季（null = 全部季）：详情页看季下拉，手动搜索看表单里的「季」
  function activeSeason() {
    var panel = state.panel;
    if (!panel) { return null; }
    if (panel.form) {
      var spec = manualSpec();
      return spec.kind === 'episode' ? spec.season : null;
    }
    var picked = seasonScope();
    return picked === 'all' ? null : picked;
  }

  function currentSeasonKey() {
    var season = activeSeason();
    return season == null ? 'all' : String(season);
  }

  // 面板当前选中的季（null = 全部季）
  function wantedSeason() {
    return activeSeason();
  }

  // 一道筛选：阈值 → 只看本集 → 该季 → 同名噪音 → 没标季号的整剧包。
  // 返回 {kept, hiddenSeason, hiddenPack, hiddenNoise, singles, packs}；
  // 计数只统计 kept；singles/packs 只在单集页（season+episode 都有）时有意义。
  function candidatePool() {
    var snapshot = state.snapshot || {};
    var panel = state.panel;
    var minScore = panel ? parseFloat(panel.threshold.value) : state.config.minScore;
    var onlyEpisode = panel && panel.onlyEpisode && panel.onlyEpisode.checked;
    var hasEpisode = snapshot.season != null && snapshot.episode != null;
    var wanted = wantedSeason();
    var showAll = !!(panel && panel.showAll);
    var keepPacks = !!(panel && panel.includePacks);
    // 只看本集时的「隐藏整包」：整包（covers_episode 不为 false 但没有本集集号）
    var hidePacks = onlyEpisode && hasEpisode && !!(panel && panel.hidePacks);
    var pool = {
      kept: [], hiddenSeason: 0, hiddenPack: 0, hiddenNoise: 0, packsKept: 0,
      singles: 0, packs: 0, hiddenEpisodePack: 0, hasEpisode: hasEpisode
    };
    (snapshot.candidates || []).forEach(function (candidate) {
      if (typeof candidate.score !== 'number' || candidate.score < minScore) { return; }
      if (onlyEpisode && hasEpisode && candidate.covers_episode === false) { return; }
      var seasons = candidate.seasons || [];
      if (wanted != null) {
        if (seasons.length && seasons.indexOf(wanted) === -1) {
          pool.hiddenSeason += 1;
          return;
        }
        // 没标季号的候选：可能真是整剧包，也可能属于别的季 → 默认折叠
        if (!seasons.length) {
          pool.hiddenPack += 1;
          if (!keepPacks) { return; }
        }
      }
      // 同名噪音：《空王冠》《罪恶王冠》《9-nine-支配者的王冠》不是同一部片子
      if (candidate.title_match === false) {
        pool.hiddenNoise += 1;
        if (!showAll) { return; }
      }
      var isSingle = candidate.single_episode === true;
      var isPack = hasEpisode && !isSingle && candidate.covers_episode !== false;
      if (isPack && hidePacks) {
        pool.hiddenEpisodePack += 1;
        return;
      }
      if (wanted != null && !seasons.length) { pool.packsKept += 1; }
      if (hasEpisode && onlyEpisode) {
        if (isSingle) { pool.singles += 1; } else if (isPack) { pool.packs += 1; }
      }
      pool.kept.push(candidate);
    });
    // 单集页 + 只看本集：把「仅本集」排到整包前面（各自内部保持分数降序）
    if (hasEpisode && onlyEpisode) {
      var singles = [];
      var packs = [];
      pool.kept.forEach(function (candidate) {
        (candidate.single_episode === true ? singles : packs).push(candidate);
      });
      pool.kept = singles.concat(packs);
    }
    return pool;
  }

  function eligibleCandidates() {
    return candidatePool().kept;
  }

  function visibleCandidates(list) {
    var kindFilter = state.panel ? (state.panel.kindFilter || 'all') : 'all';
    return (list || eligibleCandidates()).filter(function (candidate) {
      var isPan = candidate.kind === 'pan';
      if (kindFilter === 'pan' && !isPan) { return false; }
      if (kindFilter === 'magnet' && isPan) { return false; }
      return true;
    });
  }

  function renderCandidates() {
    var panel = state.panel;
    if (!panel) { return; }
    var snapshot = state.snapshot || {};
    var all = snapshot.candidates || [];
    var hasEpisode = snapshot.season != null && snapshot.episode != null;
    if (panel.onlyEpisode) {
      panel.onlyEpisode.parentElement.style.display = hasEpisode ? '' : 'none';
    }
    var seasonPicked = activeSeason() != null;
    if (panel.strictLabel) { panel.strictLabel.style.display = seasonPicked ? '' : 'none'; }
    if (panel.packLabel) { panel.packLabel.style.display = seasonPicked ? '' : 'none'; }
    var pool = candidatePool();
    var eligible = pool.kept;
    var magnets = eligible.filter(function (c) { return c.kind !== 'pan'; }).length;
    var pans = eligible.length - magnets;
    var when = formatWhen(snapshot.generated_at);
    if (panel.segAll) {
      panel.segAll.count.textContent = String(eligible.length);
      panel.segMagnet.count.textContent = String(magnets);
      panel.segPan.count.textContent = String(pans);
      var active = panel.kindFilter || 'all';
      panel.segAll.button.classList.toggle('jdl-seg__item--active', active === 'all');
      panel.segMagnet.button.classList.toggle('jdl-seg__item--active', active === 'magnet');
      panel.segPan.button.classList.toggle('jdl-seg__item--active', active === 'pan');
    }
    var shown = visibleCandidates(eligible);
    var pickLabel = { magnet: '\u78c1\u529b', pan: '\u4e91\u76d8', all: '\u5168\u90e8' }[panel.kindFilter] || '';
    var pickSeason = seasonPicked
      ? '\u7b2c ' + currentSeasonKey() + ' \u5b63'
      : '';
    var wanted = [];
    if (snapshot.sources && snapshot.sources !== currentSources()) { wanted.push(pickLabel); }
    var snapshotSeason = snapshot.season != null ? String(snapshot.season) : 'all';
    if (snapshotSeason !== currentSeasonKey()) {
      wanted.push(pickSeason || '\u5168\u90e8\u5b63');
    }
    // 严格开关也影响抓取口径：切换后同样提示按新口径重抓（旧快照缺该字段时按默认「严格」算）
    if (seasonPicked && (snapshot.strict_season !== false) !== (panel.strictSeason !== false)) {
      wanted.push(panel.strictSeason !== false ? '\u4e25\u683c\u6309\u5b63' : '\u5bbd\u6cdb\u5173\u952e\u8bcd');
    }
    panel.summary.textContent = eligible.length
      ? (panel.fromCache
          ? (when ? '\u5df2\u52a0\u8f7d ' + when + ' \u7684\u7ed3\u679c' : '\u5df2\u52a0\u8f7d\u5386\u53f2\u7ed3\u679c')
          : '\u521a\u521a\u83b7\u53d6')
        + ' \u00b7 \u2265' + Math.round(panel.threshold.value) + ' \u5206 ' + shown.length + ' \u4e2a'
      : '\u6ca1\u6709\u5019\u9009';
    // 选择与上次抓取不一致时，独立提示条 + 一键重抓按钮（不再挤在状态行里）
    if (panel.hint) {
      if (panel.cacheOnly && panel.dirtySpec) {
        // 手动搜索：条件改了，但搜索一次要几分钟 → 只提示，不自动开抓
        panel.hint.style.display = '';
        panel.hint.innerHTML = '';
        panel.hint.appendChild(el('span', 'jdl-hint__text', '\u641c\u7d22\u6761\u4ef6\u5df2\u4fee\u6539\uff1a'));
        var goSearch = el('button', 'jdl-hint__action', '\u6309\u65b0\u6761\u4ef6\u641c\u7d22');
        goSearch.type = 'button';
        goSearch.addEventListener('click', function () { checkBackendThenSearch(panelTarget()); });
        panel.hint.appendChild(goSearch);
      } else if (wanted.length && eligible.length) {
        panel.hint.style.display = '';
        panel.hint.innerHTML = '';
        panel.hint.appendChild(el('span', 'jdl-hint__text', '\u5f53\u524d\u9009\u62e9\u4e0e\u4e0a\u6b21\u6293\u53d6\u4e0d\u540c\uff1a'));
        var goLabel = panel.cacheOnly
          ? '\u6309\u300c' + wanted.join(' \u00b7 ') + '\u300d\u641c\u7d22'
          : '\u6309\u300c' + wanted.join(' \u00b7 ') + '\u300d\u91cd\u65b0\u6293\u53d6';
        var go = el('button', 'jdl-hint__action', goLabel);
        go.type = 'button';
        go.addEventListener('click', function () { checkBackendThenSearch(panelTarget()); });
        panel.hint.appendChild(go);
      } else {
        panel.hint.style.display = 'none';
      }
    }
    renderNote(pool);
    panel.list.innerHTML = '';

    if (!all.length) {
      var noResult = el('div', 'jdl-empty');
      noResult.appendChild(el('div', 'jdl-empty__icon material-icons', 'search_off'));
      if (panel.awaitingFirstFetch) {
        noResult.appendChild(el('div', 'jdl-empty__text', panel.cacheOnly
          ? '\u586b\u4e0a\u7247\u540d\uff0c\u70b9\u300c\u641c\u7d22\u300d\u5f00\u59cb\u6293\u53d6\u5e76\u6253\u5206\u3002'
          : '\u8fd8\u6ca1\u6709\u83b7\u53d6\u8fc7\u3002\u9009\u597d\u5b63\uff08\u9ed8\u8ba4\u300c\u5168\u90e8\u5b63\u300d\uff09\u540e\u70b9\u300c\u91cd\u65b0\u83b7\u53d6\u300d\u5f00\u59cb\u6293\u53d6\u3002'));
        var go = el('button', 'jdl-btn jdl-btn--primary',
          panel.cacheOnly ? '\u641c\u7d22' : '\u6309\u5f53\u524d\u9009\u62e9\u6293\u53d6');
        go.type = 'button';
        go.addEventListener('click', function () { checkBackendThenSearch(panelTarget()); });
        noResult.appendChild(go);
        panel.list.appendChild(noResult);
        return;
      }
      var scopeNames = { magnet: '\u4ec5\u78c1\u529b', pan: '\u4ec5\u4e91\u76d8', both: '\u78c1\u529b+\u4e91\u76d8' };
      var fetchedScope = scopeNames[snapshot.sources] || '';
      noResult.appendChild(el('div', 'jdl-empty__text', fetchedScope
        ? '\u672c\u6b21\u6309\u300c' + fetchedScope + '\u300d\u6293\u53d6\uff0c\u6ca1\u6709\u627e\u5230\u8d44\u6e90\u3002'
        : '\u6ca1\u6709\u627e\u5230\u5019\u9009\u8d44\u6e90\uff0c\u53ef\u6362\u7247\u540d\u6216\u7a0d\u540e\u91cd\u8bd5\u3002'));
      var alternatives = [];
      if (snapshot.sources === 'pan') {
        alternatives = [['\u5168\u90e8', 'all'], ['\u78c1\u529b', 'magnet']];
      } else if (snapshot.sources === 'magnet') {
        alternatives = [['\u5168\u90e8', 'all'], ['\u4e91\u76d8', 'pan']];
      }
      alternatives.forEach(function (pair) {
        var jump = el('button', 'jdl-btn jdl-btn--ghost', '\u6539\u4e3a\u300c' + pair[0] + '\u300d\u91cd\u65b0\u83b7\u53d6');
        jump.type = 'button';
        jump.addEventListener('click', function () {
          if (state.panel) { state.panel.kindFilter = pair[1]; }
          checkBackendThenSearch(panelTarget());
        });
        noResult.appendChild(jump);
      });
      // 严格按季可能"太干净"了：这季确实没被标季号 → 一键放宽重抓
      if (seasonPicked && panel.strictSeason !== false) {
        var relaxStrict = el('button', 'jdl-btn jdl-btn--ghost', '\u5173\u95ed\u4e25\u683c\u6309\u5b63\u91cd\u6293');
        relaxStrict.type = 'button';
        relaxStrict.title = '\u540c\u65f6\u7528\u57fa\u7840\u7247\u540d\u641c\u7d22\uff0c\u53ec\u56de\u66f4\u591a\uff08\u4f46\u4f1a\u6df7\u5165\u5176\u5b83\u5b63\uff09';
        relaxStrict.addEventListener('click', function () {
          if (state.panel) {
            state.panel.strictSeason = false;
            if (state.panel.strictBox) { state.panel.strictBox.checked = false; }
          }
          checkBackendThenSearch(panelTarget());
        });
        noResult.appendChild(relaxStrict);
      }
      panel.list.appendChild(noResult);
      return;
    }

    if (!shown.length) {
      var below = el('div', 'jdl-empty');
      below.appendChild(el('div', 'jdl-empty__icon material-icons', 'filter_alt_off'));
      if (eligible.length) {
        var wanted = panel.kindFilter === 'pan' ? 'pan' : (panel.kindFilter === 'magnet' ? 'magnet' : '');
        var covered = (snapshot.sources || '') === 'both' || (snapshot.sources || '') === wanted;
        below.appendChild(el('div', 'jdl-empty__text', wanted && !covered
          ? '\u672c\u6b21\u6ca1\u6709\u6293\u53d6\u8be5\u7c7b\u578b\uff0c\u70b9\u300c\u91cd\u65b0\u83b7\u53d6\u300d\u6309\u5f53\u524d\u9009\u62e9\u6293\u53d6\u3002'
          : '\u5f53\u524d\u7c7b\u578b\u4e0b\u6ca1\u6709\u8fbe\u6807\u8d44\u6e90\uff0c\u53ef\u5207\u56de\u300c\u5168\u90e8\u300d\u3002'));
      } else {
        if (pool.hiddenEpisodePack) {
          // 点了「只看仅本集」但这轮没抓到单集 → 说清楚整包被折叠了多少
          below.appendChild(el('div', 'jdl-empty__text',
            '\u8fd9\u4e00\u8f6e\u6ca1\u6709\u300c\u4ec5\u672c\u96c6\u300d\u7684\u5019\u9009\uff08\u6574\u5305 ' + pool.hiddenEpisodePack
            + ' \u6761\u5df2\u6298\u53e0\uff09\uff0c\u53ef\u91cd\u65b0\u83b7\u53d6\u6216\u653e\u5f00\u6574\u5305\u3002'));
          var showPacks = el('button', 'jdl-btn jdl-btn--ghost', '\u663e\u793a\u6574\u5305');
          showPacks.type = 'button';
          showPacks.addEventListener('click', function () {
            panel.hidePacks = false;
            renderCandidates();
          });
          below.appendChild(showPacks);
          var refetchEp = el('button', 'jdl-btn jdl-btn--primary', '\u91cd\u65b0\u83b7\u53d6');
          refetchEp.type = 'button';
          refetchEp.addEventListener('click', function () { checkBackendThenSearch(state.item); });
          below.appendChild(refetchEp);
        } else if (pool.hiddenNoise || pool.hiddenPack) {
          // 达标候选全都因为「同名噪音 / 没标季号」被折叠了 → 直接给出放行入口
          var hiddenBits = [];
          if (pool.hiddenNoise) { hiddenBits.push('\u540c\u540d\u566a\u97f3 ' + pool.hiddenNoise + ' \u6761'); }
          if (pool.hiddenPack) { hiddenBits.push('\u672a\u6807\u5b63\u53f7 ' + pool.hiddenPack + ' \u6761'); }
          below.appendChild(el('div', 'jdl-empty__text',
            '\u8fbe\u6807\u5019\u9009\u90fd\u88ab\u8fc7\u6ee4\u4e86\uff08' + hiddenBits.join(' \u00b7 ') + '\uff09\u3002'));
          if (pool.hiddenNoise) {
            var showNoise = el('button', 'jdl-btn jdl-btn--ghost', '\u663e\u793a\u540c\u540d\u5019\u9009');
            showNoise.type = 'button';
            showNoise.addEventListener('click', function () {
              panel.showAll = true;
              renderCandidates();
            });
            below.appendChild(showNoise);
          }
          if (pool.hiddenPack) {
            var showPacks = el('button', 'jdl-btn jdl-btn--ghost', '\u542b\u6574\u5b63\u5305');
            showPacks.type = 'button';
            showPacks.addEventListener('click', function () {
              panel.includePacks = true;
              if (panel.packBox) { panel.packBox.checked = true; }
              renderCandidates();
            });
            below.appendChild(showPacks);
          }
        } else {
          below.appendChild(el('div', 'jdl-empty__text', '\u6ca1\u6709\u8fbe\u5230\u5f53\u524d\u9608\u503c\u7684\u8d44\u6e90\u3002'));
          var relax = el('button', 'jdl-btn jdl-btn--ghost', '\u964d\u4f4e\u9608\u503c');
          relax.type = 'button';
          relax.addEventListener('click', function () {
            panel.threshold.value = String(Math.max(0, Math.round(panel.threshold.value) - 5));
            renderCandidates();
          });
          below.appendChild(relax);
        }
      }
      panel.list.appendChild(below);
      return;
    }

    shown.forEach(function (candidate) { panel.list.appendChild(renderRow(candidate, snapshot)); });
  }

  // 提示条：单集页说明「仅本集 / 整包」各几条（可隐藏整包），
  // 以及被折叠掉多少（同名噪音 / 没标季号的整剧包）。
  // 折叠必须是**看得见**的，否则用户只会觉得"资源少了"。
  function renderNote(pool) {
    var panel = state.panel;
    if (!panel || !panel.note) { return; }
    var note = panel.note;
    note.innerHTML = '';
    var bits = [];
    var episodeScope = pool.hasEpisode && panel.onlyEpisode && panel.onlyEpisode.checked;
    if (episodeScope) {
      bits.push('\u4ec5\u672c\u96c6 ' + pool.singles + ' \u6761');
      if (pool.packs || pool.hiddenEpisodePack) {
        bits.push((panel.hidePacks ? '\u5df2\u9690\u85cf\u6574\u5305 ' : '\u6574\u5305 ')
          + (pool.packs + pool.hiddenEpisodePack) + ' \u6761');
      }
    }
    if (pool.hiddenNoise) {
      bits.push((panel.showAll ? '\u5df2\u663e\u793a\u540c\u540d\u566a\u97f3 ' : '\u5df2\u9690\u85cf\u540c\u540d\u566a\u97f3 ')
        + pool.hiddenNoise + ' \u6761');
    }
    if (pool.hiddenPack) {
      bits.push((panel.includePacks ? '\u5df2\u542b\u672a\u6807\u5b63\u53f7 ' : '\u5df2\u9690\u85cf\u672a\u6807\u5b63\u53f7 ')
        + pool.hiddenPack + ' \u6761');
    }
    if (!bits.length) { note.style.display = 'none'; return; }
    note.style.display = '';
    note.appendChild(el('span', 'jdl-note__text', bits.join(' \u00b7 ')));
    if (episodeScope && (pool.packs || pool.hiddenEpisodePack)) {
      note.appendChild(noteAction(
        panel.hidePacks ? '\u663e\u793a\u6574\u5305' : '\u53ea\u770b\u4ec5\u672c\u96c6',
        function () {
          panel.hidePacks = !panel.hidePacks;
          renderCandidates();
        }));
    }
    if (pool.hiddenNoise) {
      note.appendChild(noteAction(
        panel.showAll ? '\u53ea\u770b\u540c\u540d\u5339\u914d' : '\u663e\u793a\u540c\u540d\u5019\u9009',
        function () {
          panel.showAll = !panel.showAll;
          renderCandidates();
        }));
    }
    if (pool.hiddenPack) {
      note.appendChild(noteAction(
        panel.includePacks ? '\u4e0d\u542b\u6574\u5b63\u5305' : '\u542b\u6574\u5b63\u5305',
        function () {
          panel.includePacks = !panel.includePacks;
          if (panel.packBox) { panel.packBox.checked = panel.includePacks; }
          renderCandidates();
        }));
    }
  }

  function noteAction(label, handler) {
    var button = el('button', 'jdl-note__action', label);
    button.type = 'button';
    button.addEventListener('click', handler);
    return button;
  }

  function renderRow(candidate, snapshot) {
    var row = el('div', 'jdl-row');
    row.setAttribute('data-jdl-key', candidate.key || candidate.ih || '');

    var score = el('div', 'jdl-score ' + bandOf(candidate.score));
    score.appendChild(el('div', 'jdl-score__value', candidate.score != null ? candidate.score.toFixed(1) : '\u2014'));
    score.appendChild(el('div', 'jdl-score__bar'));
    row.appendChild(score);

    var main = el('div', 'jdl-main');
    var name = el('div', 'jdl-name');
    var chips = candidate.kind === 'pan'
      ? '<span class="jdl-chip jdl-chip--pan">\u4e91\u76d8</span>'
      : '<span class="jdl-chip">\u78c1\u529b</span>';
    var seasonList = candidate.seasons || [];
    if (seasonList.length) {
      chips += '<span class="jdl-chip">'
        + (seasonList.length === 1 ? 'S' + ('0' + seasonList[0]).slice(-2) : '\u591a\u5b63')
        + '</span>';
    }
    // 只有点「显示全部 / 含整季包」时才会看到这两种行，标出来免得误提交
    if (candidate.title_match === false) {
      chips += '<span class="jdl-chip jdl-chip--warn">\u540c\u540d?</span>';
    }
    if (wantedSeason() != null && !seasonList.length) {
      chips += '<span class="jdl-chip jdl-chip--warn">\u672a\u6807\u5b63\u53f7</span>';
    }
    // 单集页：区分「只含这一集」和「整包（覆盖本集）」，否则用户以为是单集
    if (snapshot.season != null && snapshot.episode != null) {
      if (candidate.single_episode === true) {
        chips += '<span class="jdl-chip jdl-chip--ep">\u4ec5\u672c\u96c6</span>';
      } else if (candidate.covers_episode !== false) {
        chips += '<span class="jdl-chip jdl-chip--pack">\u6574\u5305</span>';
      }
    }
    name.innerHTML = esc(candidate.name || '(\u672a\u547d\u540d)') + chips;
    main.appendChild(name);

    var metaBits = [candidate.source, candidate.resolution, fmtSize(candidate.main_video_gb)].filter(Boolean);
    if (candidate.seeds != null) {
      metaBits.push('\u79cd\u5b50 ' + candidate.seeds + '/' + (candidate.peers == null ? '\u2014' : candidate.peers));
    }
    main.appendChild(el('div', 'jdl-meta', metaBits.join(' \u00b7 ')));

    var dims = candidate.dimensions || {};
    main.appendChild(el('div', 'jdl-dims',
      '\u753b\u8d28 ' + Math.round(dims.quality || 0)
      + ' \u00b7 \u901f\u5ea6 ' + Math.round(dims.speed || 0)
      + ' \u00b7 \u4f53\u9a8c ' + Math.round(dims.experience || 0)
      + ' \u00b7 \u9002\u914d ' + Math.round(dims.fit || 0)));
    row.appendChild(main);

    var actions = el('div', 'jdl-actions');
    if (candidate.kind === 'pan') {
      if (candidate.share_url) {
        var cloudLabel = candidate.pass_code
          ? '\u6253\u5f00\u4e91\u76d8 (' + candidate.pass_code + ')'
          : '\u6253\u5f00\u4e91\u76d8';
        actions.appendChild(actionButton('open_in_new', cloudLabel, 'jdl-btn--ghost', function () {
          openCloud(candidate);
        }));
      }
    } else {
      actions.appendChild(actionButton('content_copy', '\u590d\u5236\u78c1\u529b', 'jdl-btn--ghost', function () {
        var magnet = 'magnet:?xt=urn:btih:' + (candidate.ih || '');
        (navigator.clipboard ? navigator.clipboard.writeText(magnet) : Promise.reject(new Error('no clipboard')))
          .then(function () { toast('\u5df2\u590d\u5236\u78c1\u529b'); })
          .catch(function () { toast('\u590d\u5236\u5931\u8d25'); });
      }));
      actions.appendChild(actionButton('monitor_heart', '\u540e\u53f0\u76d1\u63a7', 'jdl-btn--ghost', function () {
        submitAction(candidate, snapshot, 'watch', '\u5df2\u542f\u52a8\u540e\u53f0\u76d1\u63a7');
      }));
      actions.appendChild(actionButton('download', '\u63d0\u4ea4\u8fc5\u96f7', 'jdl-btn--primary', function () {
        submitAction(candidate, snapshot, 'submit', '\u5df2\u63d0\u4ea4\u8fc5\u96f7');
      }));
    }
    row.appendChild(actions);
    return row;
  }

  function actionButton(icon, label, variant, handler) {
    var button = el('button', 'jdl-btn ' + variant);
    button.type = 'button';
    button.innerHTML = '<span class="material-icons">' + icon + '</span><span class="jdl-btn__label">' + label + '</span>';
    button.addEventListener('click', function () { handler(button); });
    return button;
  }

  // 打开云盘：把提取码拼进 URL（并复制到剪贴板兜底），省去手动输入
  function openCloud(candidate) {
    var url = candidate.share_url || '';
    var code = candidate.pass_code || '';
    if (code && url && url.indexOf('pwd=') < 0) {
      url += (url.indexOf('?') >= 0 ? '&' : '?') + 'pwd=' + encodeURIComponent(code);
    }
    if (url) { window.open(url, '_blank', 'noopener'); }
    if (!code) { toast('\u5df2\u6253\u5f00\u4e91\u76d8'); return; }
    (navigator.clipboard ? navigator.clipboard.writeText(code) : Promise.reject(new Error('no clipboard')))
      .then(function () { toast('\u5df2\u6253\u5f00\u4e91\u76d8\uff0c\u63d0\u53d6\u7801 ' + code + ' \u5df2\u590d\u5236'); })
      .catch(function () { toast('\u5df2\u6253\u5f00\u4e91\u76d8\uff0c\u63d0\u53d6\u7801\uff1a' + code); });
  }

  function submitAction(candidate, snapshot, endpoint, successText) {
    var payload = {
      ih: candidate.ih,
      final_dir: snapshot.final_dir,
      kind: snapshot.kind,
      series_name: snapshot.query,
      year: snapshot.year
    };
    api(endpoint, { type: 'POST', data: payload }).then(function (result) {
      if (!result || result.error) { toast(result && result.error ? result.error : '\u8bf7\u6c42\u5931\u8d25'); return; }
      if (result.ok) {
        toast(successText);
        startMonitoring();
      } else {
        toast('\u5931\u8d25\uff08rc=' + result.rc + '\uff09');
      }
    }).catch(function () { toast('\u540e\u7aef\u4e0d\u53ef\u8fbe'); });
  }

  function startMonitoring() {
    if (!state.slug) { return; }
    if (state.monitorTimer) { return; }
    refreshTasks();
    state.monitorTimer = setInterval(refreshTasks, 5000);
  }

  function refreshTasks() {
    if (!state.slug) { return; }
    api('snapshot?slug=' + encodeURIComponent(state.slug)).then(function (snapshot) {
      if (!snapshot || snapshot.error) { return; }
      state.snapshot = snapshot;
      renderTasks(snapshot.tasks || []);
    }).catch(function () {});
  }

  function renderTasks(tasks) {
    var panel = state.panel;
    if (!panel) { return; }
    var active = tasks.filter(function (task) { return task.kind === 'bt' && task.pct < 99.5; });
    if (!active.length) { return; }
    panel.tasks.style.display = '';
    panel.tasks.innerHTML = '';
    panel.tasks.appendChild(el('div', 'jdl-tasks__title', '\u540e\u53f0\u4efb\u52a1'));
    active.slice(0, 3).forEach(function (task) {
      var item = el('div', 'jdl-task');
      item.appendChild(el('div', 'jdl-task__name', task.file_name || task.info_hash || ('\u4efb\u52a1 ' + task.taskid)));
      var bar = el('div', 'jdl-task__bar');
      var fill = el('i');
      fill.style.width = Math.max(0, Math.min(100, task.pct || 0)) + '%';
      bar.appendChild(fill);
      item.appendChild(bar);
      var bits = [(task.pct || 0).toFixed(1) + '%'];
      if (task.speed) { bits.push(fmtSpeed(task.speed)); }
      if (task.eta_hours != null) { bits.push('ETA ' + task.eta_hours.toFixed(1) + 'h'); }
      item.appendChild(el('div', 'jdl-task__meta', bits.join(' \u00b7 ')));
      panel.tasks.appendChild(item);
    });
  }
})();
