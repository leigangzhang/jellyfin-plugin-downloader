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

    // 保存到哪个库（分类目录）：默认按条目类型，提交迅雷 / 后台监控时生效，
    // 落点 = 媒体库根/<分类>/片名 (年份)。
    var categorySelect = el('select', 'jdl-select');
    [
      ['Movies', '\u7535\u5f71'],
      ['TV Shows', '\u5267\u96c6'],
      ['Shows', '\u7efc\u827a'],
      ['Records', '\u7eaa\u5f55\u7247']
    ].forEach(function (pair) {
      var option = el('option', null, pair[1]);
      option.value = pair[0];
      categorySelect.appendChild(option);
    });
    var defaultCategory = (item.Manual && manualSpec().kind === 'movie') || item.Type === 'Movie'
      ? 'Movies'
      : 'TV Shows';
    categorySelect.value = defaultCategory;
    categorySelect.title = '\u4fdd\u5b58\u5230\u54ea\u4e2a\u5e93\uff08\u63d0\u4ea4\u8fc5\u96f7 / \u540e\u53f0\u76d1\u63a7\u65f6\u751f\u6548\uff09';
    controls.appendChild(categorySelect);

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
    // 阶段行默认不显示：没有真实进展时不占一行（有内容时由 setStage 显示）
    var stageNode = el('div', 'jdl-progress__stage');
    stageNode.style.display = 'none';
    progress.appendChild(stageNode);
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
      categorySelect: categorySelect,
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

  // 进度阶段行：有内容才显示，空字符串直接把这一行收起来
  function setStage(progressEl, text) {
    if (!progressEl) { return; }
    var node = progressEl.querySelector('.jdl-progress__stage');
    if (!node) { return; }
    node.textContent = text || '';
    node.style.display = text ? '' : 'none';
  }

  function finishProgress(text) {
    var panel = state.panel;
    if (!panel) { return; }
    panel.progress.classList.add('jdl-progress--done');
    setStage(panel.progress, text);
  }

