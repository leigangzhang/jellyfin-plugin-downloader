  // 一道筛选：阈值 → 只看本集 → 该季 → 同名噪音 → 没标季号的整剧包。
  // 返回 {kept, hiddenSeason, hiddenPack, hiddenNoise, singles, packs}；
  // 计数只统计 kept；singles/packs 只在单集页（season+episode 都有）时有意义。
  // 注意：0 种子候选**不在这里过滤** —— 它们在 backend/probe/probe_round.py 组装交付候选时
  // 就被丢弃了（使用方口径：这类候选永远用不到，不进快照），面板只在提示行里报一行条数。
  function candidatePool() {
    var snapshot = state.snapshot || {};
    var panel = state.panel;
    var minScore = configuredMinScore();
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

  // 「0 种子候选」（后端判定的 dead-swarm）在交付前就被丢弃（backend/probe/probe_round.py），
  // 快照 candidates 里已经没有这些行；后端把它们记进快照 rejected（reason='dead_swarm_filtered'），
  // 这里只数条数，用来在提示行报一句「已过滤 N 条 0 种子候选」——不列行、不给开关。
  function zeroSeedFiltered(snapshot) {
    var count = 0;
    ((snapshot && snapshot.rejected) || []).forEach(function (entry) {
      if (entry && entry.reason === 'dead_swarm_filtered') { count += 1; }
    });
    return count;
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

  // 只读阈值文案：面板不再有档位选择器，这里显示当前实际生效的阈值（配置 MinScore）
  function renderThresholdNote() {
    var panel = state.panel;
    if (!panel || !panel.thresholdNote) { return; }
    panel.thresholdNote.textContent = '\u2265 ' + Math.round(configuredMinScore()) + ' \u5206';
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
    // 状态行的阈值与实际筛选口径必须同源：都取 configuredMinScore()。
    renderThresholdNote();
    var folded = pool.hiddenSeason + pool.hiddenPack + pool.hiddenNoise + pool.hiddenEpisodePack;
    panel.summary.textContent = eligible.length
      ? (panel.fromCache
          ? (when ? '\u5df2\u52a0\u8f7d ' + when + ' \u7684\u7ed3\u679c' : '\u5df2\u52a0\u8f7d\u5386\u53f2\u7ed3\u679c')
          : '\u521a\u521a\u83b7\u53d6')
        + ' \u00b7 \u2265' + Math.round(configuredMinScore()) + ' \u5206 ' + shown.length + ' \u4e2a'
      : (folded
          ? '\u6ca1\u6709\u5019\u9009\uff08' + folded + ' \u6761\u5df2\u6298\u53e0\uff09'
          : '\u6ca1\u6709\u5019\u9009');
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
          // 达标候选全都因为「同名噪音 / 没标季号 / 0 种子」被折叠了 → 直接给出放行入口
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
          below.appendChild(el('div', 'jdl-empty__text',
            '\u6ca1\u6709\u8fbe\u5230\u5f53\u524d\u9608\u503c\uff08\u2265' + Math.round(configuredMinScore())
            + ' \u5206\uff09\u7684\u8d44\u6e90\uff1b\u9608\u503c\u7531\u63d2\u4ef6\u914d\u7f6e\u51b3\u5b9a\uff0c\u9762\u677f\u4e0d\u518d\u63d0\u4f9b\u6863\u4f4d\u3002'));
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
    // 0 种子候选已经在后端交付前被丢弃（不进快照）→ 这里只报一行条数，不列行、不给开关
    if (zeroSeedFiltered(state.snapshot)) {
      bits.push('\u5df2\u8fc7\u6ee4 ' + zeroSeedFiltered(state.snapshot) + ' \u6761 0 \u79cd\u5b50\u5019\u9009');
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
      actions.appendChild(actionButton('monitor_heart', '\u540e\u53f0\u76d1\u63a7', 'jdl-btn--ghost', function (button) {
        submitAction(candidate, snapshot, 'watch', '\u5df2\u542f\u52a8\u540e\u53f0\u76d1\u63a7', button);
      }));
      actions.appendChild(actionButton('download', '\u63d0\u4ea4\u8fc5\u96f7', 'jdl-btn--primary', function (button) {
        submitAction(candidate, snapshot, 'submit', '\u5df2\u63d0\u4ea4\u8fc5\u96f7', button);
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

  // 提交载荷里的年份：快照没有年份时回落到当前表单/条目。
  // 旧版本只发 snapshot.year —— 手动搜索实测恒为 ""，导致 final_dir 少了「 (年份)」。
  function submitYear(snapshot) {
    var panel = state.panel;
    if (snapshot && snapshot.year) { return snapshot.year; }
    if (panel && panel.form) { return manualSpec().year || ''; }
    if (panel && panel.item && panel.item.ProductionYear) { return String(panel.item.ProductionYear); }
    return '';
  }

  // 进行中文案为什么不是「约 25 s」——提交这段路是**两段**，25 s 只是第一段：
  //   * 元数据探测：backend/media_download_lib/torrents.py:27
  //     `probe_torrent_files(info_hash, timeout=25)` → aria2c `--bt-stop-timeout=25`（:57）；
  //     拿不到 metadata 就 RESOURCE_REJECTED（真机 26.3 s 回 rc=3）。
  //   * 测速外推 ETA：backend/download/submit_xunlei.py:566-569 的 `--eta-probe-seconds`
  //     **默认 180**。任务在测速**之前**就已建好，所以「迅雷里已有任务 + 面板还在转」是
  //     正常中途态（队长真机实测一次完整提交 195.6 s）。
  // 这两个值后端目前**没有任何接口下发**（/JellyfinDownloader/config 只有 minScore /
  // StagingRoot / MediaRoot…；快照与 /api/status 也没有），而 C# 不在本任务范围 —— 所以
  // 这里硬编码一份，出处就是上面两行。**后端改这两个值时必须同步改这里**（更彻底的做法是
  // 后端加字段下发、前端优先读后端值，那要另开一条跨 C#/backend 的任务）。
  // 已用时长是纯客户端 Date.now() 计时，与后端无关。
  var BUSY_LABEL = {
    submit: '\u63d0\u4ea4\u4e2d\u2026 \u63a2\u6d4b\u5143\u6570\u636e 25 s + \u6d4b\u901f\u6700\u957f 180 s',
    watch: '\u542f\u52a8\u4e2d\u2026 \u6302\u540e\u53f0\u76d1\u63a7\uff08\u901a\u5e38 1\u20132 \u79d2\uff09'
  };

  function busyLabelFor(endpoint) {
    return endpoint === 'watch' ? BUSY_LABEL.watch : BUSY_LABEL.submit;
  }

  // 「已用」文案：<60 s 用秒，>=60 s 用「N 分 M 秒」（用户要的是「还在正常跑」的判据）
  function formatElapsed(seconds) {
    var total = Math.max(0, Math.round(seconds));
    if (total < 60) { return total + ' \u79d2'; }
    return Math.floor(total / 60) + ' \u5206 ' + (total % 60) + ' \u79d2';
  }

  function renderBusyLabel(button) {
    var info = button && button.__jdlBusy;
    if (!info) { return; }
    var label = button.querySelector ? button.querySelector('.jdl-btn__label') : null;
    if (!label) { return; }
    var elapsed = Math.max(0, (Date.now() - info.startedAt) / 1000);
    label.textContent = info.text + '\uff08\u5df2\u7528 ' + formatElapsed(elapsed) + '\uff09';
  }

  // 返回按钮原来的文案（恢复时用）；按钮是 innerHTML 造的，
  // 所以文案节点只能靠 .jdl-btn__label 找。
  function setBusy(button, text) {
    if (!button) { return ''; }
    var label = button.querySelector ? button.querySelector('.jdl-btn__label') : null;
    var idle = '';
    if (label) {
      if (label.getAttribute && label.getAttribute('data-jdl-idle-label') == null) {
        label.setAttribute('data-jdl-idle-label', label.textContent || '');
      }
      idle = (label.getAttribute && label.getAttribute('data-jdl-idle-label')) || '';
    }
    button.disabled = true;
    if (button.classList) { button.classList.add('jdl-btn--busy'); }
    // 可见的旋转指示器：只有变灰+换文案还不够「看得出在转」。
    // 节点插在文案前面；prefers-reduced-motion: reduce 时由 CSS 降级成静态指示。
    if (button.querySelector && !button.querySelector('.jdl-spinner')) {
      var spinner = el('span', 'jdl-spinner');
      spinner.setAttribute('aria-hidden', 'true');
      if (button.insertBefore) { button.insertBefore(spinner, button.firstChild || null); }
      else if (button.appendChild) { button.appendChild(spinner); }
    }
    // 已用时长：每秒刷新一次（文案里已经写明两段时间预期，用户据此判断是否还需要等）
    if (button.__jdlBusy && button.__jdlBusy.timer) { clearInterval(button.__jdlBusy.timer); }
    button.__jdlBusy = {
      text: text,
      startedAt: Date.now(),
      timer: setInterval(function () { renderBusyLabel(button); }, 1000)
    };
    renderBusyLabel(button);
    return idle;
  }

  function clearBusy(button, idleLabel) {
    if (!button) { return; }
    if (button.__jdlBusy) {
      if (button.__jdlBusy.timer) { clearInterval(button.__jdlBusy.timer); }
      button.__jdlBusy = null;
    }
    var label = button.querySelector ? button.querySelector('.jdl-btn__label') : null;
    if (label && idleLabel) { label.textContent = idleLabel; }
    // 响应到达 → 停转并摘掉指示器
    var spinner = button.querySelector ? button.querySelector('.jdl-spinner') : null;
    if (spinner) {
      if (button.removeChild) { button.removeChild(spinner); } else { spinner.remove(); }
    }
    button.disabled = false;
    if (button.classList) { button.classList.remove('jdl-btn--busy'); }
  }

  function submitAction(candidate, snapshot, endpoint, successText, button) {
    var panel = state.panel;
    var payload = {
      ih: candidate.ih,
      final_dir: snapshot.final_dir,
      kind: snapshot.kind,
      series_name: snapshot.query,
      year: submitYear(snapshot),
      category: panel && panel.categorySelect ? panel.categorySelect.value : ''
    };
    // trust-magnet 逃生开关：只在勾选时带上参数，默认（关闭）与旧行为逐字一致
    if (panel && panel.trustMagnet) { payload.trust_magnet = true; }
    // 点击的同一帧内就进入进行中（同步代码，不等任何 await）
    var startedAt = Date.now();
    var idleLabel = setBusy(button, busyLabelFor(endpoint));
    var elapsedSeconds = function () { return ((Date.now() - startedAt) / 1000).toFixed(1); };
    api(endpoint, { type: 'POST', data: payload }).then(function (result) {
      clearBusy(button, idleLabel);
      if (!result || result.error) { toast(result && result.error ? result.error : '\u8bf7\u6c42\u5931\u8d25'); return; }
      if (result.ok) {
        toast(successText);
        startMonitoring();
      } else {
        // 失败时把闸门实际花了多久写出来（本地计时，不用后端字段）
        toast('\u5931\u8d25\uff08rc=' + result.rc + '\uff0c\u7528\u65f6 ' + elapsedSeconds() + ' s\uff09');
      }
    }).catch(function () {
      clearBusy(button, idleLabel);
      toast('\u540e\u7aef\u4e0d\u53ef\u8fbe');
    });
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
      // 监控轮询每 5 秒把整份快照换掉：后端快照的 kind 是**探针口径**（record 会被写成
      // movie），不在这里对齐的话，「提交第 1 条 → 开始监控 → 再提交第 2 条」时第 2 条
      // 的 /api/submit 会退回 movie（目标库仍是 Records，落点不受影响）。
      rememberSnapshotKind(snapshot.kind);
      syncSnapshotKind();
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
