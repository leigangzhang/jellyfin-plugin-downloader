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

