
  // 分片 06：热门（TMDb 榜单海报墙）+ 关注/已在库。
  //
  // 它是第二条 UI 入口，与既有 5 片没有耦合，只借 01/02/03 已有的 helper
  // （el/call/api/pluginApi/toast/log/themeClass/currentItemId/openManualPanel）。
  // 入口必须用自己的 class 与自己的图标：01 的 enforceSingleEntry() 与 02 的
  // removeHeaderButton() 会把「header 里带 .material-icons.arrow_downward 的节点」
  // 当重复项删掉，复用它们会让两个入口互删。不要为了省事放宽那两个判据。
  var TRENDING_BTN_CLASS = 'jdl-trending-btn';
  var TRENDING_ICON = 'trending_up';
  var TRENDING_POSTER_BASE = 'https://image.tmdb.org/t/p/w300';
  var TRENDING_GUIDANCE_FALLBACK = '尚未配置 TMDb API Key：'
    + '在插件配置页的「热门」分组里填好后再打开本面板（榜单与海报都需要它）。';
  // 关注成功后卡片上的徽标：占位入库 / 只记进关注清单 / 库里已经有 / 从榜单回填的已关注
  var TRENDING_BADGE = { anchored: '已占位', listed: '仅记录', inLibrary: '已在库', watched: '已关注' };

  var trending = {
    open: false,
    node: null,
    body: null,
    config: null,
    groups: [],
    cards: [],          // [{ node, entry, badge, watch, watched, inLibrary, busy }]
    statusSent: false
  };

  // 右上角入口：只在非详情页挂（详情页已有「…」旁的资源入口，一屏只留一个）。
  // React 重渲染会冲掉节点，所以每次 mount() 都重新检查，缺了就重建。
  function mountTrendingButton() {
    var right = document.querySelector('.skinHeader .headerRight')
      || document.querySelector('.headerRight');
    if (!right) { return; }
    if (currentItemId()) { removeTrendingButton(); return; }
    var keep = null;
    Array.prototype.forEach.call(document.querySelectorAll('.' + TRENDING_BTN_CLASS), function (node) {
      if (!keep && node.parentElement === right) { keep = node; return; }
      node.remove();
    });
    if (keep) { return; }
    var button = buildTrendingHeaderButton(right);
    // 插在搜索图标之前；已经有下载入口时插到它之前——02 的 mountHeaderButton() 用
    // 「下一个兄弟是 .headerSearchButton」认自己那一版，插在中间会让它每轮重建一次。
    var anchor = right.querySelector('.jdl-header-btn') || right.querySelector('.headerSearchButton');
    if (anchor && anchor.parentElement === right) { right.insertBefore(button, anchor); }
    else { right.appendChild(button); }
    log('mounted trending button');
  }

  function removeTrendingButton() {
    Array.prototype.forEach.call(document.querySelectorAll('.' + TRENDING_BTN_CLASS), function (node) { node.remove(); });
  }

  // 图标按钮的形状照着原生搜索按钮克隆（尺寸/圆角/hover 一致），只换 class 与图标
  function buildTrendingHeaderButton(right) {
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
    button.classList.add(TRENDING_BTN_CLASS);
    button.type = 'button';
    button.title = '热门：TMDb 榜单（点卡片进搜索，可关注占位）';
    button.setAttribute('aria-label', '热门');
    button.setAttribute('data-jdl-trending', '1');
    var icon = button.querySelector('.material-icons');
    if (icon) {
      icon.className = 'material-icons ' + TRENDING_ICON;
      icon.textContent = '';
      icon.setAttribute('aria-hidden', 'true');
    } else {
      button.innerHTML = '<span class="material-icons ' + TRENDING_ICON + '" aria-hidden="true"></span>';
    }
    button.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      openTrending();
    });
    return button;
  }

  // 覆盖层骨架：外观基类照抄既有面板（.jdl-backdrop/.jdl-dialog），
  // 但用 data-jdl-trending-panel 而不是 data-jdl-panel —— 否则 openPanel()/closePanel()
  // 会把对方当成自己那个面板。
  function openTrending() {
    if (trending.open) { return; }
    trending.open = true;
    trending.config = null;
    trending.groups = [];
    trending.cards = [];
    trending.statusSent = false;

    var backdrop = el('div', 'jdl-backdrop jdl-trending');
    var theme = themeClass();
    if (theme) { backdrop.classList.add(theme); }
    backdrop.setAttribute('data-jdl-trending-panel', '1');

    var dialog = el('div', 'jdl-dialog jdl-trending__dialog');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', '热门');

    var head = el('div', 'jdl-head');
    var titles = el('div', 'jdl-head__titles');
    titles.appendChild(el('div', 'jdl-head__title', '热门'));
    titles.appendChild(el('div', 'jdl-head__sub', 'TMDb 榜单 · 点卡片进入搜索，关注可占位入库'));
    head.appendChild(titles);
    var close = el('button', 'jdl-iconbtn');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭');
    close.innerHTML = '<span class="material-icons">close</span>';
    close.addEventListener('click', closeTrending);
    head.appendChild(close);
    dialog.appendChild(head);

    var body = el('div', 'jdl-trending__body');
    body.appendChild(el('div', 'jdl-trending__notice', '正在读取热门榜单…'));
    dialog.appendChild(body);

    backdrop.appendChild(dialog);
    backdrop.addEventListener('click', function (event) {
      if (event.target === backdrop) { closeTrending(); }
    });
    document.body.appendChild(backdrop);
    trending.node = backdrop;
    trending.body = body;
    loadTrending();
  }

  function closeTrending() {
    trending.open = false;
    trending.cards = [];
    trending.groups = [];
    if (trending.node) { trending.node.remove(); }
    trending.node = null;
    trending.body = null;
  }

  // 先读插件配置（语言/榜单/anchor 开关/有没有 key），再取榜单。
  // 无 key 时不打榜单接口、不到控制台报错：只在面板里渲染后端给的 guidance（A1）。
  function loadTrending() {
    pluginApi('config').then(function (config) {
      trending.config = config || {};
      if (trendingKeyMissing(trending.config)) {
        renderTrendingNotice(trendingGuidance(trending.config));
        return;
      }
      var language = String(trendingValue(trending.config, ['tmdbLanguage', 'language']) || 'zh-CN');
      var lists = trendingListIds(trending.config);
      var query = 'trending?language=' + encodeURIComponent(language);
      if (lists) { query += '&lists=' + encodeURIComponent(lists); }
      return api(query).then(function (payload) {
        trending.groups = trendingGroups(payload);
        if (!trending.groups.length) {
          renderTrendingNotice('榜单是空的：后端没有返回可展示的条目。');
          return;
        }
        renderTrending();
        // 先按榜单回填「已关注」，再问「已在库」：两个都是只读请求
        applyWatched(payload);
        askLibraryStatus();
      }).catch(function (error) {
        // 后端不可达/接口报错：在面板里说清楚，不留白屏
        renderTrendingNotice('榜单读取失败：' + trendingError(error));
      });
    }).catch(function (error) {
      renderTrendingNotice('配置读取失败：' + trendingError(error));
    });
  }

  // 配置键名的大小写由插件 C# 侧的匿名对象决定，这里只按名字读一次，找不到就换下一个
  function trendingValue(config, names) {
    if (!config) { return undefined; }
    var i;
    for (i = 0; i < names.length; i++) {
      if (config[names[i]] !== undefined) { return config[names[i]]; }
    }
    var keys = Object.keys(config);
    for (i = 0; i < keys.length; i++) {
      for (var j = 0; j < names.length; j++) {
        if (keys[i].toLowerCase() === names[j].toLowerCase()) { return config[keys[i]]; }
      }
    }
    return undefined;
  }

  function trendingGuidance(config) {
    var guidance = trendingValue(config, ['guidance', 'keyGuidance']);
    if (typeof guidance === 'string' && guidance.trim()) { return guidance.trim(); }
    return TRENDING_GUIDANCE_FALLBACK;
  }

  function trendingKeyMissing(config) {
    var guidance = trendingValue(config, ['guidance', 'keyGuidance']);
    if (typeof guidance === 'string' && guidance.trim()) { return true; }
    var configured = trendingValue(config, ['tmdbApiKeyConfigured', 'hasTmdbApiKey', 'tmdbApiKeySet']);
    return configured === false || configured === 'false';
  }

  function anchorEnabled() {
    var value = trendingValue(trending.config, ['anchorEnabled', 'anchor']);
    return value === true || value === 'true';
  }

  // trendingLists 可能是 ['trending/movie/day'] 也可能是 'a,b' 或 [{id,name}]
  function trendingListIds(config) {
    var value = trendingValue(config, ['trendingLists', 'lists']);
    if (value == null) { return ''; }
    if (!Array.isArray(value)) { return String(value); }
    return value.map(function (item) {
      if (item && typeof item === 'object') { return item.id != null ? String(item.id) : ''; }
      return String(item == null ? '' : item);
    }).filter(function (id) { return !!id; }).join(',');
  }

  // 榜单分组：{lists|groups:[{id,name,items}]} / {items|results:[...]} / 直接数组。
  // 返回的都是 {id, name, entries}，entries 已经归一化成卡片需要的字段。
  function trendingGroups(payload) {
    if (Array.isArray(payload)) { return trendingGroups({ items: payload }); }
    if (!payload || typeof payload !== 'object') { return []; }
    var raw = payload.lists || payload.groups;
    if (Array.isArray(raw) && raw.length) {
      return raw.map(function (group, index) {
        var meta = group && typeof group === 'object' && !Array.isArray(group) ? group : {};
        var items = meta.items || meta.results || meta.titles;
        if (!items && Array.isArray(group)) { items = group; }
        var id = String(meta.id || meta.list_id || meta.key || ('list-' + index));
        return {
          id: id,
          name: String(meta.name || meta.title || meta.label || ('榜单 ' + (index + 1))),
          entries: (Array.isArray(items) ? items : [])
            .map(function (item) { return trendingEntry(item, id); })
            .filter(function (entry) { return !!entry; })
        };
      }).filter(function (group) { return group.entries.length > 0; });
    }
    var flat = payload.items || payload.results;
    if (Array.isArray(flat)) {
      return [{ id: 'trending', name: '热门', entries: flat
        .map(function (item) { return trendingEntry(item, 'trending'); })
        .filter(function (entry) { return !!entry; }) }];
    }
    return [];
  }

  function trendingEntry(raw, groupId) {
    if (!raw || typeof raw !== 'object') { return null; }
    var title = String(raw.title || raw.name || raw.original_title || '').trim();
    if (!title) { return null; }
    return {
      title: title,
      year: trendingYear(raw),
      // TMDb 的 movie → 'movie'，tv → 'episode'：既有的打分流程只认这两档
      kind: trendingKind(raw, groupId),
      tmdbId: raw.tmdb_id != null ? raw.tmdb_id : (raw.id != null ? raw.id : null),
      poster: String(raw.poster_path || raw.poster || '').trim(),
      rating: trendingRating(raw)
    };
  }

  function trendingYear(raw) {
    var year = raw.year != null && raw.year !== '' ? raw.year : raw.release_year;
    if (year == null || year === '') {
      var match = /^(\d{4})/.exec(String(raw.release_date || raw.first_air_date || ''));
      year = match ? match[1] : '';
    }
    return year == null ? '' : String(year);
  }

  function trendingKind(raw, groupId) {
    var kind = String(raw.kind || raw.media_type || raw.type || '').toLowerCase();
    if (kind === 'movie' || kind === 'film') { return 'movie'; }
    if (kind === 'tv' || kind === 'series' || kind === 'episode' || kind === 'show') { return 'episode'; }
    // 条目自己没带类型时按榜单 id 判（trending/tv/day、popular_tv …）
    return /(^|[^a-z])tv([^a-z]|$)/.test(String(groupId || '')) ? 'episode' : 'movie';
  }

  function trendingRating(raw) {
    var rating = raw.rating != null && raw.rating !== '' ? raw.rating : raw.vote_average;
    var value = Number(rating);
    return isFinite(value) && value > 0 ? value.toFixed(1) : '';
  }

  function renderTrending() {
    var body = trending.body;
    if (!body) { return; }
    body.innerHTML = '';
    trending.cards = [];
    trending.groups.forEach(function (group) {
      body.appendChild(el('div', 'jdl-trending__list', group.name));
      var grid = el('div', 'jdl-trending__grid');
      group.entries.forEach(function (entry) { grid.appendChild(buildTrendingCard(entry)); });
      body.appendChild(grid);
    });
  }

  function buildTrendingCard(entry) {
    var card = el('div', 'jdl-trending__card');
    card.setAttribute('data-jdl-trending-entry', entry.title);

    var poster = el('div', 'jdl-trending__poster');
    if (entry.poster) {
      var image = el('img', 'jdl-trending__img');
      image.alt = entry.title;
      image.loading = 'lazy';
      // CDN 图挂了不能留破图：退回色块占位
      image.addEventListener('error', function () { markPosterMissing(poster); });
      image.src = /^https?:/i.test(entry.poster) ? entry.poster : TRENDING_POSTER_BASE + entry.poster;
      poster.appendChild(image);
    } else {
      markPosterMissing(poster);
    }

    // 海报即入口：把片名/年份/类型带进既有的手动搜索面板（打分链一行都不用改）
    var pick = el('button', 'jdl-trending__pick');
    pick.type = 'button';
    pick.title = '搜索并打分：' + entry.title;
    pick.setAttribute('aria-label', '搜索并打分：' + entry.title);
    pick.appendChild(poster);
    pick.addEventListener('click', function () {
      closeTrending();
      openManualPanel({ title: entry.title, year: entry.year, kind: entry.kind });
    });
    card.appendChild(pick);

    var info = el('div', 'jdl-trending__info');
    info.appendChild(el('div', 'jdl-trending__name', entry.title));
    var meta = [];
    if (entry.year) { meta.push(entry.year); }
    if (entry.rating) { meta.push('\u2605 ' + entry.rating); }
    info.appendChild(el('div', 'jdl-trending__meta', meta.join(' \u00b7 ')));
    card.appendChild(info);

    var actions = el('div', 'jdl-trending__actions');
    var watch = el('button', 'jdl-btn jdl-trending__watch', '关注');
    watch.type = 'button';
    watch.addEventListener('click', function () { watchTrendingCard(record); });
    actions.appendChild(watch);
    // 空徽标由 CSS :empty 隐藏，省一次状态同步
    var badge = el('span', 'jdl-trending__badge');
    actions.appendChild(badge);
    card.appendChild(actions);

    var record = {
      node: card, entry: entry, badge: badge, watch: watch,
      watched: false, inLibrary: false, busy: false
    };
    trending.cards.push(record);
    return card;
  }

  function markPosterMissing(poster) {
    poster.innerHTML = '';
    poster.classList.add('jdl-trending__poster--missing');
    poster.appendChild(el('span', 'material-icons', 'image_not_supported'));
  }

  // 「已在库」一次性批量问：覆盖层打开、卡片渲染完之后只发一次
  function askLibraryStatus() {
    if (trending.statusSent || !trending.cards.length) { return; }
    trending.statusSent = true;
    var items = trending.cards.map(function (record) {
      return { title: record.entry.title, year: record.entry.year, kind: libraryKind(record.entry) };
    });
    pluginApi('trending/library-status', { type: 'POST', data: { items: items } })
      .then(function (payload) { applyLibraryStatus(payload); })
      .catch(function (error) {
        if (!trending.body) { return; }
        trending.body.appendChild(el('div', 'jdl-trending__notice',
          '已在库状态读不到（' + trendingError(error) + '）：卡片仍可关注。'));
      });
  }

  // 库里认 movie/series 两档（剧集在库内是 Series，哪怕榜单给的是 tv）
  function libraryKind(entry) {
    return entry.kind === 'movie' ? 'movie' : 'series';
  }

  function applyLibraryStatus(payload) {
    if (!payload) { return; }
    var rows = payload.items || payload.results || payload.status;
    if (Array.isArray(rows)) {
      rows.forEach(function (row, index) {
        if (row == null) { return; }
        if (typeof row === 'boolean') {
          if (row && trending.cards[index]) { markInLibrary(trending.cards[index]); }
          return;
        }
        var record = findTrendingCard(row) || trending.cards[index];
        if (record && trendingHit(row)) { markInLibrary(record); }
      });
      return;
    }
    // 也可能只回命中的 title 清单
    var hits = payload.inLibrary || payload.in_library || payload.titles;
    if (Array.isArray(hits)) {
      hits.forEach(function (hit) {
        var record = typeof hit === 'string' ? findByTitle(hit, '') : findTrendingCard(hit);
        if (record) { markInLibrary(record); }
      });
    }
  }

  // 只回了命中项（没带 in_library 之类的标记）时，一律按「命中」算
  function trendingHit(row) {
    var flag = row.in_library != null ? row.in_library
      : (row.inLibrary != null ? row.inLibrary : (row.exists != null ? row.exists : row.present));
    if (flag == null) { return true; }
    return flag === true || flag === 'true';
  }

  function findTrendingCard(row) {
    if (!row || typeof row !== 'object') { return null; }
    var title = row.title || row.name;
    if (!title) { return null; }
    return findByTitle(String(title), row.year);
  }

  function findByTitle(title, year) {
    var wanted = String(title).trim().toLowerCase();
    var wantedYear = year == null || year === '' ? '' : String(year);
    for (var i = 0; i < trending.cards.length; i++) {
      var entry = trending.cards[i].entry;
      if (String(entry.title).trim().toLowerCase() !== wanted) { continue; }
      if (wantedYear && entry.year && wantedYear !== entry.year) { continue; }
      return trending.cards[i];
    }
    return null;
  }

  function markInLibrary(record) {
    if (!record || record.inLibrary) { return; }
    record.inLibrary = true;
    record.node.classList.add('jdl-trending__card--in-library');
    record.watch.disabled = true;
    record.watch.textContent = '已在库';
    record.badge.textContent = TRENDING_BADGE.inLibrary;
  }

  // 关注：先记进关注清单（后端 /api/trending/watch）；
  // anchorEnabled 为真时才去写库内占位（POST /trending/anchor）——为假时绝不发这一枪。
  function watchTrendingCard(record) {
    if (!record || record.busy || record.watched || record.inLibrary) { return; }
    record.busy = true;
    record.watch.disabled = true;
    record.watch.textContent = '关注中…';
    var entry = record.entry;
    api('trending/watch', {
      type: 'POST',
      data: { title: entry.title, year: entry.year, kind: entry.kind, tmdb_id: entry.tmdbId }
    }).then(function (result) {
      if (anchorPlaced(result)) { markWatched(record, TRENDING_BADGE.anchored); return; }
      if (!anchorEnabled()) { markWatched(record, TRENDING_BADGE.listed); return; }
      return pluginApi('trending/anchor', {
        type: 'POST',
        data: { title: entry.title, year: entry.year, kind: entry.kind }
      }).then(function (anchored) {
        markWatched(record, anchorPlaced(anchored) ? TRENDING_BADGE.anchored : TRENDING_BADGE.listed);
      }).catch(function (error) {
        markWatched(record, TRENDING_BADGE.listed);
        toast('占位失败（' + trendingError(error) + '）：已记进关注清单');
      });
    }).catch(function (error) {
      record.busy = false;
      record.watch.disabled = false;
      record.watch.textContent = '关注';
      toast('关注失败：' + trendingError(error));
    });
  }

  function anchorPlaced(result) {
    if (!result || typeof result !== 'object') { return false; }
    if (result.anchored === true || result.placed === true) { return true; }
    var status = String(result.status || result.result || '');
    return status === 'anchored' || status === 'placed';
  }

  function markWatched(record, badge) {
    record.busy = false;
    record.watched = true;
    record.node.classList.add('jdl-trending__card--watched');
    record.watch.disabled = true;
    record.watch.textContent = '已关注';
    record.badge.textContent = badge;
  }

  // GET /api/trending 会把关注清单里的 tmdb_id 一并回给客户端（watched）。
  // 刷新页面或新会话后靠它把卡片直接恢复成「已关注」态：不回填的话外观会丢
  //（数据本身由后端幂等兜着，这里丢的只是外观）。
  // 这一路只读不写：一个 watch / anchor 请求都不发。
  // watched 缺失或不是数组时按空集合处理（老后端也不能崩）。
  // 徽标只能写「已关注」：watched 只说「在关注清单里」，「已占位」与「仅记录」
  // 的区别只有当场点关注的那条路径分得清。
  function applyWatched(payload) {
    var watched = payload && payload.watched;
    if (!Array.isArray(watched) || !watched.length) { return; }
    var ids = {};
    watched.forEach(function (item) {
      var id = item;
      if (item && typeof item === 'object') { id = item.tmdb_id != null ? item.tmdb_id : item.id; }
      if (id != null) { ids[String(id)] = true; }
    });
    trending.cards.forEach(function (record) {
      var tmdbId = record.entry.tmdbId;
      if (tmdbId != null && ids[String(tmdbId)]) { markWatched(record, TRENDING_BADGE.watched); }
    });
  }

  function renderTrendingNotice(text) {
    if (!trending.body) { return; }
    trending.body.innerHTML = '';
    trending.body.appendChild(el('div', 'jdl-trending__notice', text));
  }

  function trendingError(error) {
    if (!error) { return '未知错误'; }
    if (error.status) { return 'HTTP ' + error.status; }
    return error.message || error.statusText || '网络错误';
  }
})();
