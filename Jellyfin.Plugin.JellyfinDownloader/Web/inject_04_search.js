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
      setStage(current.progress, '\u6b63\u5728\u8fde\u63a5\u540e\u7aef\u2026');
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
        if (stage) { setStage(panel.progress, stage); }
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

