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
    var opened = state.panel;
    if (opened && opened.cacheTimer) { clearTimeout(opened.cacheTimer); opened.cacheTimer = null; }
    var panel = document.querySelector('[data-jdl-panel]');
    if (panel) { panel.remove(); }
    state.panel = null;
    document.removeEventListener('keydown', onKeyDown);
  }

  // 「纪录片」不是一个 Jellyfin 条目类型：纪录片库里的条目被 Jellyfin 认成 Series，
  // 而 /api/search 的 kind 决定后端取哪一组默认值（关不关单源、用哪组署名关键字、
  // 探针按什么策略算体积）。所以有效类型不能只看 item.Type，还要看目标库/用户选型：
  //   · 保存到哪个库 = Records（详情页/季页手动选）→ record
  //   · 手动面板「类型」选「纪录片」→ record
  // 电影仍是 movie、剧集仍是 episode（这两条不经过这里，params.kind 保持 searchParams 的结果）。
  function wantsRecordKind() {
    var panel = state.panel;
    if (!panel) { return false; }
    if (panel.form && panel.form.kind && panel.form.kind.value === 'record') { return true; }
    return !!(panel.categorySelect && panel.categorySelect.value === 'Records');
  }

  // 快照里的 kind 是后端探针口径（record 会被探针按 PROBE_KIND 写成 movie），
  // 而提交要发「本面板的有效类型」→ 读回快照时记下原始值，再按当前选择对齐。
  function rememberSnapshotKind(kind) {
    var panel = state.panel;
    if (panel) { panel.baseKind = kind; }
  }

  function syncSnapshotKind() {
    var panel = state.panel;
    var snapshot = state.snapshot;
    if (!panel || !snapshot || !snapshot.kind) { return; }
    var base = panel.baseKind || snapshot.kind;
    snapshot.kind = wantsRecordKind() ? 'record' : base;
  }

  function runSearch(item) {
    var current = state.panel;
    var params = searchParams(item);
    // 纪录片：把有效类型从 episode 改写成 record（后端按 record 关单源 + 换署名组）
    if (wantsRecordKind()) { params.kind = 'record'; }
    if (!params.title) {
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
      setStage(current.progress, '\u6b63\u5728\u8fde\u63a5\u540e\u7aef\u2026');
    }
    api('search', { type: 'POST', data: params }).then(function (result) {
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

  // 搜索停滞判定：阈值必须**大于后端看门狗**，让后端先以自己的话收尾（按符号查，不按行号）：
  //   * `STALL_TIMEOUT_SECS = 1200`（backend/console_server/jobs.py）是后端看门狗：job 心跳
  //     超时会被它收成终态，并带 error「搜索无进展（已超时）」经 /api/search/status 直接送到
  //     面板。正确分工是「后端先给结论」，所以前端阈值取 1200 + 60 余量 = 1260 —— 不能小于
  //     看门狗，否则会抢在后端前面判死：后台其实还活着时，用户先看到「无进展 / 重试」。
  //   * 前端这条只兜「后端进程还在、状态接口还活、却迟迟没有新行」的情形（连看门狗都没跑起来
  //     时的兜底），不是替代后端下结论。
  //   * 1260 也远大于最长的合法静默：单阶段上界 `SEARCH_TIMEOUT = 900`、搜池阶段
  //     `SEARCH_POOL_TIMEOUT = 600`（backend/console_server.py），而 run_cli_stream 逐行回调
  //     on_line，真在干活的阶段会持续刷心跳 ⇒ 合法长阶段不会被误报成停滞。
  var SEARCH_STALL_SECONDS = 1260;

  function searchElapsedSeconds() {
    var search = state.search;
    if (!search) { return 0; }
    return Math.max(0, Math.round((Date.now() - search.startedAt) / 1000));
  }

  // 阶段文案 + 已用时长：每轮轮询都刷一次，用户能看出「在跑」而不是「卡住」
  function renderSearchStage() {
    var panel = state.panel;
    var search = state.search;
    if (!panel || !search) { return; }
    setStage(panel.progress, search.stage + '\uff08\u5df2\u7528 ' + searchElapsedSeconds() + ' s\uff09');
  }

  function searchStalled() {
    var search = state.search;
    if (!search) { return false; }
    return (Date.now() - search.lastProgressAt) >= SEARCH_STALL_SECONDS * 1000;
  }

  function pollSearch(jobId, seen) {
    // 后端重启（Jellyfin 重启 / 配置页关了再开）会让 job 记录随进程消失，
    // 状态接口对旧 job 直接 404（unknown job）。这时候必须判定为「搜索已中断」
    // 并给出重试入口，否则面板会一直显示「正在获取…」——看起来像卡死。
    state.search = {
      job: jobId,
      seen: seen || 0,
      misses: 0,
      startedAt: Date.now(),
      lastProgressAt: Date.now(),
      stage: '\u6b63\u5728\u8fde\u63a5\u540e\u7aef\u2026'
    };
    renderSearchStage();
    state.timer = setInterval(searchTick, 800);
  }

  // 一轮轮询：状态查询 → 有新行就算「有进展」→ done 收尾 / 长期无进展判停滞
  function searchTick() {
    var search = state.search;
    if (!search) { return; }
    api('search/status?job=' + encodeURIComponent(search.job)).then(function (result) {
      if (!result) { return; }
      if (result.error) {
        if (/unknown\s*job/i.test(String(result.error))) {
          failSearch('\u641c\u7d22\u5df2\u4e2d\u65ad\uff08\u540e\u7aef\u91cd\u542f\u540e\u4efb\u52a1\u8bb0\u5f55\u4e22\u5931\uff09\uff0c\u70b9\u300c\u91cd\u8bd5\u300d\u91cd\u65b0\u83b7\u53d6', false);
          return;
        }
        search.misses += 1;
        if (search.misses >= 5) { failSearch('\u8fdb\u5ea6\u67e5\u8be2\u5931\u8d25\uff1a' + result.error, false); }
        return;
      }
      search.misses = 0;
      var panel = state.panel;
      if (!panel) { return; }
      var lines = result.lines || [];
      if (lines.length > search.seen) {
        panel.log.textContent = lines.join('\n');
        panel.log.scrollTop = panel.log.scrollHeight;
        search.seen = lines.length;
        var stage = (lines[lines.length - 1] || '').trim();
        if (stage) { search.stage = stage; }
        search.lastProgressAt = Date.now();
      }
      if (result.done) {
        clearInterval(state.timer);
        state.timer = null;
        state.search = null;
        if (result.error) { failSearch(result.error); return; }
        finishProgress('\u83b7\u53d6\u5b8c\u6210');
        state.slug = result.slug || state.slug;
        state.snapshot = result.snapshot || {};
        rememberSnapshotKind(state.snapshot.kind);
        syncSnapshotKind();
        var status = result.search_status;
        panel.summary.textContent = status && status !== 'ok'
          ? '\u641c\u7d22\u5b8c\u6210\uff08' + status + '\uff09'
          : '\u641c\u7d22\u5b8c\u6210';
        renderCandidates();
        startMonitoring();
        return;
      }
      // 未完成：先判停滞（无新行且已超阈值 → 给重试，不再无限静默轮询），再刷时长
      if (searchStalled()) {
        clearInterval(state.timer);
        state.timer = null;
        failSearch('\u641c\u7d22\u65e0\u8fdb\u5c55\uff08\u5df2\u7528 ' + searchElapsedSeconds()
          + ' s \u6ca1\u6709\u4efb\u4f55\u65b0\u8fdb\u5c55\uff09\uff0c\u70b9\u300c\u91cd\u8bd5\u300d\u91cd\u65b0\u83b7\u53d6', true);
        return;
      }
      renderSearchStage();
    }).catch(function () {
      // 状态接口 404 / 后端不可达都走这里：连续 6 次（约 5 秒）才判定中断，
      // 避免一次网络抖动就把搜索判死。
      if (!state.search) { return; }
      state.search.misses += 1;
      if (state.search.misses >= 6) {
        failSearch('\u641c\u7d22\u5df2\u4e2d\u65ad\uff08\u540e\u7aef\u4e0d\u53ef\u8fbe\u6216\u5df2\u91cd\u542f\uff09\uff0c\u70b9\u300c\u91cd\u8bd5\u300d\u91cd\u65b0\u83b7\u53d6', true);
      }
    });
  }

  function failSearch(message, offerStart) {
    var panel = state.panel;
    if (!panel) { return; }
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    state.search = null;   // 停止计时：这条路径已经把搜索判死，留着旧时钟只会误导
    panel.progress.classList.add('jdl-progress--done');
    setStage(panel.progress, message);
    panel.summary.textContent = message;
    panel.list.innerHTML = '';
    var empty = el('div', 'jdl-empty');
    empty.appendChild(el('div', 'jdl-empty__icon material-icons', 'cloud_off'));
    empty.appendChild(el('div', 'jdl-empty__text', message));
    if (offerStart) {
      var startBtn = el('button', 'jdl-btn jdl-btn--primary', '\u542f\u52a8\u540e\u7aef');
      startBtn.type = 'button';
      startBtn.addEventListener('click', function () {
        setStage(panel.progress, '\u6b63\u5728\u542f\u52a8\u540e\u7aef\u2026');
        startBackend(startBtn, null);
      });
      empty.appendChild(startBtn);
    }
    var retry = el('button', 'jdl-btn jdl-btn--ghost', '\u91cd\u8bd5');
    retry.type = 'button';
    retry.addEventListener('click', function () {
      setStage(panel.progress, '\u6b63\u5728\u8fde\u63a5\u540e\u7aef\u2026');
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

