(function () {
  'use strict';

  if (window.__jdlLoaded) { return; }
  window.__jdlLoaded = true;

  var JDL_BUILD = '2026-09-25i';
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

  // 阈值只有一个来源：插件配置的 MinScore（默认 60）。
  // 面板不再暴露档位选择器 —— 旧版本的 select 只有 60/65/70/75/80 五档，
  // 而「降低阈值」按钮做的是 value-5（=55）→ 没有对应 option → select 变空值、
  // 阈值实际失效（状态行显示「≥0 分」、候选一次全放）。用户决定：控件不调整、
  // 页面不显示档位、固定取配置值，所以这里只读、不再可改。
  function configuredMinScore() {
    var value = Number(state.config && state.config.minScore);
    return isFinite(value) && value > 0 ? value : 60;
  }

  // 类型 → 默认目标库。「保存到哪个库」的默认值只由这一张表决定，
  // 手动搜索面板切「类型」时按它联动（用户手动改过之后不再覆盖）。
  function defaultCategoryForKind(kind) {
    if (kind === 'movie') { return 'Movies'; }
    if (kind === 'record') { return 'Records'; }
    return 'TV Shows';
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
    // 热门入口（06）。它自己的挂载函数收在 06 里，这里只负责每轮 mount 都叫它一次：
    // React 重渲染会冲掉 header 节点，靠 mount 重建才不会被冲没。
    mountTrendingButton();
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

