// 仪表盘页：热力图 / 调度器状态卡 / 时间线封面 / 收件箱初始化
  // 全局图表实例
  let dailyChartInstance = null

  // 海报缓存（命名空间随 Bangumi API / 图片反代配置变化而失效）
  const posterCache = {}
  let posterTrackTotal = 0
  const posterPending = new Set()
  const posterFailed = new Set()

  function posterStorageKey(subjectId) {
    const ns =
      typeof window.__DASHBOARD_DATA__.poster_cache_ns === 'string'
        ? window.__DASHBOARD_DATA__.poster_cache_ns
        : ''
    return 'poster_' + ns + '_' + subjectId
  }

  function pruneStalePosterCache() {
    const ns =
      typeof window.__DASHBOARD_DATA__.poster_cache_ns === 'string'
        ? window.__DASHBOARD_DATA__.poster_cache_ns
        : ''
    if (!ns) return
    const currentPrefix = 'poster_' + ns + '_'
    try {
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const key = localStorage.key(i)
        if (!key || !key.startsWith('poster_')) continue
        if (!key.startsWith(currentPrefix)) {
          localStorage.removeItem(key)
        }
      }
    } catch (e) {}
  }

  // 缓存数据用于 resize 重绘
  let cachedHeatmapData = null
  let cachedDailyStats = null
  let cachedLlmDaily = null
  let llmTokensChartInstance = null
  let llmQualityChartInstance = null

  // 时间线保留条数（与后端实时推送的 DASHBOARD_RECENT_LIMIT 对应）
  const DASHBOARD_TIMELINE_LIMIT = 6
  // 首屏静态加载是否已落地：落地前忽略 SSE 首帧，避免重复渲染
  let dashboardLoaded = false

  document.addEventListener('DOMContentLoaded', function () {
    pruneStalePosterCache()
    loadDashboardData().finally(function () {
      dashboardLoaded = true
      connectDashboardStream()
    })
    // 调度器状态卡（独立于仪表板数据，可并行加载）
    loadSchedulerStatus()

    // 主题变更时重新绘制图表
    if (window.themeManager) {
      themeManager.onChange(function () {
        if (cachedDailyStats) drawDailyChart(cachedDailyStats)
        if (cachedLlmDaily) drawLlmTrendCharts(cachedLlmDaily)
        if (cachedHeatmapData) renderHeatmap(cachedHeatmapData)
      })
    }

    // 热力图 tooltip：事件委托（只绑定 2 个监听器）
    var hmContainer = document.getElementById('heatmap-container')
    var hmTip = null
    function getHmTip() {
      if (!hmTip) {
        hmTip = document.createElement('div')
        hmTip.id = 'hm-tip'
        hmTip.style.cssText =
          'position:fixed;z-index:9999;pointer-events:none;opacity:1;display:none'
        hmTip.className = 'app-tooltip'
        hmTip.innerHTML = '<div class="tooltip-inner"></div>'
        document.body.appendChild(hmTip)
      }
      return hmTip
    }
    hmContainer.addEventListener('mouseover', function (e) {
      var cell = e.target.closest('.heatmap-cell')
      if (!cell) return
      var tipText = cell.getAttribute('data-tip')
      if (!tipText) return
      var el = getHmTip()
      el.querySelector('.tooltip-inner').textContent = tipText
      el.style.display = 'block'
      var rect = cell.getBoundingClientRect()
      el.style.left = rect.left + rect.width / 2 + 'px'
      el.style.top = rect.top - 8 + 'px'
      el.style.transform = 'translate(-50%, -100%)'
    })
    hmContainer.addEventListener('mouseout', function (e) {
      if (!e.relatedTarget || !hmContainer.contains(e.relatedTarget)) {
        if (hmTip) hmTip.style.display = 'none'
      }
    })

    // 窗口 resize 时重绘热力图和图表
    var resizeTimer
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer)
      resizeTimer = setTimeout(function () {
        if (cachedHeatmapData) renderHeatmap(cachedHeatmapData)
        if (cachedDailyStats) drawDailyChart(cachedDailyStats)
        if (cachedLlmDaily) drawLlmTrendCharts(cachedLlmDaily)
      }, 200)
    })
  })

  // ========== 记录详情 ==========

  function getStatusColor(status) {
    switch (status) {
      case 'success':
        return 'success'
      case 'error':
        return 'danger'
      case 'ignored':
        return 'warning'
      case 'retried':
        return 'success'
      default:
        return 'secondary'
    }
  }

  function getStatusText(status) {
    switch (status) {
      case 'success':
        return '成功'
      case 'error':
        return '失败'
      case 'ignored':
        return '已忽略'
      case 'retried':
        return '已重试'
      default:
        return status
    }
  }

  // getSourceColor 已在 app.js 全局定义（window.getSourceColor），此处不再重复

  async function loadDashboardData() {
    const statsRequest = apiFetch('/api/stats')
      .then(function (statsData) {
        if (statsData.status === 'success') {
          const stats = statsData.data
          animateStatNumber(
            document.getElementById('total-syncs'),
            stats.total_syncs
          )
          animateStatNumber(
            document.getElementById('success-rate'),
            stats.success_rate + '%'
          )
          animateStatNumber(
            document.getElementById('today-syncs'),
            stats.today_syncs
          )
          animateStatNumber(
            document.getElementById('error-syncs'),
            stats.error_syncs
          )
          cachedDailyStats = stats.daily_stats
          drawDailyChart(cachedDailyStats)
        }
      })
      .catch(function (error) {
        console.error('加载统计数据失败:', error)
        showAlert('加载数据失败', 'danger')
      })

    const recordsRequest = apiFetch('/api/records?limit=6&skip_count=true')
      .then(function (recordsData) {
        if (recordsData.status === 'success') {
          renderTimeline(recordsData.data.records)
        }
      })
      .catch(function (error) {
        console.error('加载同步记录失败:', error)
        showAlert('加载数据失败', 'danger')
      })

    const heatmapRequest = apiFetch('/api/stats/heatmap')
      .then(function (heatmapData) {
        if (heatmapData.status === 'success') {
          cachedHeatmapData = heatmapData.data
          renderHeatmap(cachedHeatmapData, true)
        }
      })
      .catch(function (error) {
        console.error('加载热力图失败:', error)
        showAlert('加载数据失败', 'danger')
      })

    // LLM 用量统计
    var _llmDetail = null
    const llmRequest = apiFetch('/api/llm/stats?scope=detailed')
      .then(function (d) {
        if (d.total_calls > 0 || d.error_count > 0) {
          document.getElementById('llm-stats-row').style.display = 'flex'
          document.getElementById('llm-total-calls').textContent =
            d.total_calls || 0
          document.getElementById('llm-total-tokens').textContent =
            formatNumber(d.total_tokens || 0)
          document.getElementById('llm-avg-latency').textContent =
            (d.avg_latency_ms || 0) + 'ms'
          document.getElementById('llm-error-count').textContent =
            d.error_count || 0
          // 有数据时显示详情图标
          if (
            (d.by_model && d.by_model.length) ||
            (d.by_job && d.by_job.length) ||
            (d.daily && d.daily.length)
          ) {
            _llmDetail = d
            document.getElementById('llm-detail-trigger').style.display = ''
          }
          if (d.daily && d.daily.length) {
            cachedLlmDaily = d.daily
            document.getElementById('llm-trend-row').style.display = 'flex'
            drawLlmTrendCharts(cachedLlmDaily)
          }
        }
      })
      .catch(function (e) {
        /* Silent - LLM not configured or not used */
      })

    // LLM 详情 tooltip
    ;(function () {
      var trigger = document.getElementById('llm-detail-trigger')
      var tooltip = document.getElementById('llm-detail-tooltip')
      var inner = document.getElementById('llm-detail-tooltip-inner')
      var hideTimer = null

      function buildDetailHTML(d) {
        var h = ''
        if (d.by_model && d.by_model.length) {
          h += '<div class="fw-bold mb-1">按模型</div>'
          h +=
            '<table class="w-100 small mb-2"><tr class="text-muted"><td>模型</td><td class="text-end">调用</td><td class="text-end">Token</td><td class="text-end">延迟</td></tr>'
          d.by_model.forEach(function (m) {
            h +=
              '<tr><td>' +
              escapeHtml(m.model) +
              '</td><td class="text-end">' +
              m.calls +
              '</td><td class="text-end">' +
              formatNumber(m.total_tokens) +
              '</td><td class="text-end">' +
              m.avg_latency_ms +
              'ms</td></tr>'
          })
          h += '</table>'
        }
        if (d.by_job && d.by_job.length) {
          h += '<div class="fw-bold mb-1">按任务</div>'
          h +=
            '<table class="w-100 small mb-2"><tr class="text-muted"><td>任务</td><td class="text-end">调用</td><td class="text-end">Token</td><td class="text-end">延迟</td></tr>'
          d.by_job.forEach(function (j) {
            h +=
              '<tr><td>' +
              escapeHtml(j.job_name) +
              '</td><td class="text-end">' +
              j.calls +
              '</td><td class="text-end">' +
              formatNumber(j.total_tokens) +
              '</td><td class="text-end">' +
              j.avg_latency_ms +
              'ms</td></tr>'
          })
          h += '</table>'
        }
        if (d.daily && d.daily.length) {
          h += '<div class="fw-bold mb-1">最近 ' + d.daily.length + ' 天</div>'
          h +=
            '<table class="w-100 small"><tr class="text-muted"><td>日期</td><td class="text-end">调用</td><td class="text-end">Token</td></tr>'
          d.daily.forEach(function (day) {
            h +=
              '<tr><td>' +
              day.date +
              '</td><td class="text-end">' +
              day.calls +
              '</td><td class="text-end">' +
              formatNumber(day.total_tokens) +
              '</td></tr>'
          })
          h += '</table>'
        }
        return h
      }

      trigger.addEventListener('mouseenter', function () {
        if (!_llmDetail) return
        clearTimeout(hideTimer)
        inner.innerHTML = buildDetailHTML(_llmDetail)
        tooltip.style.display = 'block'
        var rect = trigger.getBoundingClientRect()
        var left = rect.right + 8
        var top = rect.top
        if (left + 480 > window.innerWidth) left = rect.left - 488
        if (top + tooltip.offsetHeight > window.innerHeight)
          top = window.innerHeight - tooltip.offsetHeight - 8
        tooltip.style.left = left + 'px'
        tooltip.style.top = top + 'px'
      })

      trigger.addEventListener('mouseleave', function () {
        hideTimer = setTimeout(function () {
          tooltip.style.display = 'none'
        }, 200)
      })

      tooltip.addEventListener('mouseenter', function () {
        clearTimeout(hideTimer)
      })
      tooltip.addEventListener('mouseleave', function () {
        tooltip.style.display = 'none'
      })
    })()

    // 四个请求并行，等全部落地再启动实时流，避免首屏与推送两路同时渲染
    await Promise.all([
      statsRequest,
      recordsRequest,
      heatmapRequest,
      llmRequest,
    ])
  }

  // ========== 调度器状态卡 ==========

  /**
   * 从 /api/scheduler/status 加载调度器状态并渲染到状态卡。
   */
  async function loadSchedulerStatus() {
    const body = document.getElementById('scheduler-status-body')
    if (!body) return
    try {
      const data = await apiFetch('/api/scheduler/status', { method: 'GET' })
      if (data.status === 'success' && Array.isArray(data.data)) {
        renderSchedulerStatus(data.data)
      } else {
        body.innerHTML = '<p class="text-muted small mb-0">加载失败</p>'
      }
    } catch (e) {
      console.error('加载调度器状态失败:', e)
      body.innerHTML = '<p class="text-muted small mb-0">加载失败</p>'
    }
  }

  /**
   * 渲染调度器状态列表到 #scheduler-status-body。
   * 复用最近同步卡的 .sync-timeline / .tl-track / .tl-item 样式（不含海报）。
   * @param {Array} schedulers - [{scheduler_id, display_name, jobs: [{job_id, name, next_run_time, trigger}]}, ...]
   */
  function renderSchedulerStatus(schedulers) {
    const body = document.getElementById('scheduler-status-body')
    const summary = document.getElementById('scheduler-status-summary')
    if (!schedulers || schedulers.length === 0) {
      body.innerHTML =
        '<div class="sync-timeline"><div class="app-empty-state"><i class="bi bi-inbox app-empty-state__icon"></i><div>无已注册调度器</div></div></div>'
      if (summary) summary.classList.add('d-none')
      return
    }

    // 统计：已调度任务数 / 总任务数
    var totalJobs = 0
    var scheduledJobs = 0
    var nextRunTs = null
    schedulers.forEach(function (s) {
      (s.jobs || []).forEach(function (j) {
        totalJobs++
        if (j.next_run_time) {
          scheduledJobs++
          if (nextRunTs === null || j.next_run_time < nextRunTs) {
            nextRunTs = j.next_run_time
          }
        }
      })
    })
    if (summary) {
      var label = scheduledJobs + '/' + totalJobs + ' 个任务'
      if (nextRunTs !== null) {
        label += ' · 最近 ' + formatRelativeNextRun(nextRunTs)
      }
      summary.textContent = label
      summary.classList.remove('d-none')
    }

    // 所有 jobs 混在一个 tl-track 内，scheduler 名作为 .tl-source 标签区分；
    // 空 scheduler 不渲染（无运行中任务时不占位）。
    var itemsHtml = ''
    var itemIndex = 0
    schedulers.forEach(function (s) {
      var schedulerName = s.display_name || s.scheduler_id
      var jobs = s.jobs || []
      jobs.forEach(function (j) {
        itemsHtml += renderSchedulerJobHtml(j, schedulerName, itemIndex++)
      })
    })
    if (!itemsHtml) {
      itemsHtml =
        '<div class="app-empty-state"><i class="bi bi-inbox app-empty-state__icon"></i><div>无运行中任务</div></div>'
    }

    body.innerHTML = '<div class="sync-timeline"><div class="tl-track">' + itemsHtml + '</div></div>'
  }

  /**
   * 渲染单个调度任务为 .tl-item（无海报）。
   * 结构与最近同步卡一致：dot + body(title + meta + msg)。
   */
  function renderSchedulerJobHtml(j, schedulerName, index) {
    var isScheduled = !!j.next_run_time
    var dotClass = isScheduled ? 'tl-dot--success' : 'tl-dot--idle'
    var name = escapeHtml(j.name || j.job_id || '-')

    // scheduler 名作为来源标签（复用 .tl-source 风格）
    var sourceHtml =
      '<span class="tl-source tl-source--custom">' +
      escapeHtml(schedulerName) +
      '</span>'

    // trigger 作为等宽标签（复用 .tl-episode-text 风格）
    var triggerHtml = j.trigger
      ? '<span class="tl-episode-text"><i class="bi bi-calendar3 me-1"></i>' +
        escapeHtml(formatTrigger(j.trigger)) +
        '</span>'
      : ''

    // cron 表达式人类可读描述（如"每 15 分钟"、"每月1日 08:00"）
    var cronDescHtml = ''
    if (j.trigger && window.CronDesc) {
      var desc = window.CronDesc.describe(j.trigger, '')
      if (desc) {
        cronDescHtml =
          '<span class="tl-cron-desc"><i class="bi bi-translate me-1"></i>' +
          escapeHtml(desc) +
          '</span>'
      }
    }

    // 下次运行时间（复用 .tl-time 风格）
    var timeHtml
    if (isScheduled) {
      timeHtml =
        '<span class="tl-time"><span class="tl-relative-time">' +
        formatRelativeNextRun(j.next_run_time) +
        '</span> · ' +
        formatNextRun(j.next_run_time) +
        '</span>'
    } else {
      timeHtml =
        '<span class="tl-time"><span class="tl-relative-time">未调度</span></span>'
    }

    // 未调度时显示提示消息（复用 .tl-msg--warn 风格）
    var msgHtml = isScheduled
      ? ''
      : '<div class="tl-msg tl-msg--warn"><i class="bi bi-info-circle-fill me-1"></i>未调度</div>'

    return (
      '<div class="tl-item" style="animation-delay:' + index * 0.06 + 's">' +
      '<div class="tl-dot-wrap"><div class="tl-dot ' + dotClass + '" title="' + (isScheduled ? '已调度' : '未调度') + '"></div></div>' +
      '<div class="tl-body">' +
      '<div class="tl-title">' + name + '</div>' +
      '<div class="tl-meta">' +
      sourceHtml +
      triggerHtml +
      cronDescHtml +
      timeHtml +
      '</div>' +
      msgHtml +
      '</div>' +
      '</div>'
    )
  }

  /**
   * 格式化下次运行时间戳为简洁的中文日期时间。
   */
  function formatNextRun(ts) {
    if (!ts) return '未调度'
    var d = new Date(ts * 1000)
    if (isNaN(d.getTime())) return '未调度'
    return window.CronDesc.formatNext(d)
  }

  /**
   * 计算距下次运行的相对时间描述。
   */
  function formatRelativeNextRun(ts) {
    if (!ts) return '未调度'
    var d = new Date(ts * 1000)
    if (isNaN(d.getTime())) return '未调度'
    var diffMs = d.getTime() - Date.now()
    var past = diffMs < 0
    var sec = Math.floor(Math.abs(diffMs) / 1000)
    if (sec < 60) return past ? '刚刚' : '即将执行'
    var min = Math.floor(sec / 60)
    if (min < 60) return (past ? '已过期 ' : '') + min + ' 分钟' + (past ? '前' : '后')
    var hr = Math.floor(min / 60)
    if (hr < 24) return (past ? '已过期 ' : '') + hr + ' 小时' + (past ? '前' : '后')
    var day = Math.floor(hr / 24)
    return (past ? '已过期 ' : '') + day + ' 天' + (past ? '前' : '后')
  }

  // 将 trigger 字符串归一化为标准五段式 cron（用于 trigger 标签展示原始表达式）。
  // APScheduler 的 str(trigger) 形如 cron[month='*', day='*', day_of_week='*', hour='*', minute='*/3']，
  // 归一化为 */3 * * * *。非 cron[...] 格式原样返回。
  function formatTrigger(trigger) {
    if (!trigger) return ''
    if (!window.CronDesc) return trigger
    return window.CronDesc.normalize(trigger)
  }

  // ========== 时间线 ==========

  function formatRelativeTime(timestamp) {
    const now = Date.now()
    const diff = now - new Date(timestamp).getTime()
    const seconds = Math.floor(diff / 1000)
    if (seconds < 60) return '刚刚'
    const minutes = Math.floor(seconds / 60)
    if (minutes < 60) return minutes + '分钟前'
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return hours + '小时前'
    const days = Math.floor(hours / 24)
    if (days < 7) return days + '天前'
    const weeks = Math.floor(days / 7)
    return weeks + '周前'
  }

  // 时间线条目的状态/来源样式映射（整表渲染与实时增量共用）
  const statusMap = {
    success: { text: '成功', class: 'success', icon: 'bi-check-circle-fill' },
    error: { text: '失败', class: 'error', icon: 'bi-x-circle-fill' },
    ignored: { text: '忽略', class: 'ignored', icon: 'bi-skip-circle-fill' },
    retried: { text: '已重试', class: 'retried', icon: 'bi-arrow-repeat' },
  }

  const sourceClassMap = {
    plex: 'tl-source--plex',
    emby: 'tl-source--emby',
    jellyfin: 'tl-source--jellyfin',
    custom: 'tl-source--custom',
    feiniu: 'tl-source--feiniu',
    fongmi: 'tl-source--fongmi',
    test: 'tl-source--test',
  }

  function renderTimeline(records) {
    const container = document.getElementById('sync-timeline')
    if (!records || records.length === 0) {
      container.innerHTML =
        '<div class="app-empty-state"><i class="bi bi-inbox app-empty-state__icon"></i><div>暂无同步记录</div></div>'
      return
    }

    let html = '<div class="tl-track">'
    records.forEach(function (record, index) {
      html += buildTimelineItemHtml(record, index, '')
    })
    html += '</div>'

    container.innerHTML = html

    loadTimelinePosters(records)
  }

  // 单条时间线条目：整表渲染与实时增量插入共用同一套标记，避免两处走偏
  function buildTimelineItemHtml(record, index, extraClass) {
      const status = statusMap[record.status] || {
        text: record.status,
        class: 'error',
        icon: 'bi-question-circle',
      }
      const src = record.source.toLowerCase()
      const sourceClass = src.startsWith('retry-')
        ? 'tl-source--retry'
        : sourceClassMap[src] || 'tl-source--custom'

      const time = new Date(record.timestamp)
      const timeStr = time.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
      })
      const relativeTime = formatRelativeTime(record.timestamp)

      const titleText = escapeHtml(record.bgm_title || record.title)

      const isMovie = record.media_type === 'movie'
      let epHtml
      if (isMovie) {
        epHtml =
          '<span class="tl-episode-text tl-episode-movie"><i class="bi bi-film me-1"></i>剧场版</span>'
      } else {
        const epText =
          'S' +
          String(record.season).padStart(2, '0') +
          'E' +
          String(record.episode).padStart(2, '0')
        epHtml = '<span class="tl-episode-text">' + epText + '</span>'
      }

      const posterHtml = record.subject_id
        ? '<div class="timeline-poster timeline-poster--loading"><img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" data-sid="' + record.subject_id + '" data-idx="' + index + '" alt="" loading="lazy"></div>'
        : '<div class="timeline-poster timeline-poster--placeholder"><i class="bi bi-film"></i></div>'

      // 消息
      let msgHtml = ''
      if (record.message) {
        if (record.status === 'error') {
          msgHtml =
            '<div class="tl-msg tl-msg--error" title="' + escapeHtml(record.message) + '"><i class="bi bi-exclamation-triangle-fill me-1"></i>' +
            escapeHtml(record.message) +
            '</div>'
        } else if (record.status === 'success' || record.status === 'retried') {
          msgHtml =
            '<div class="tl-msg tl-msg--success" title="' + escapeHtml(record.message) + '"><i class="bi bi-check-circle-fill me-1"></i>' +
            escapeHtml(record.message) +
            '</div>'
        } else if (record.status === 'ignored') {
          msgHtml =
            '<div class="tl-msg tl-msg--warn" title="' + escapeHtml(record.message) + '"><i class="bi bi-info-circle-fill me-1"></i>' +
            escapeHtml(record.message) +
            '</div>'
        }
      }

      return (
        '<div class="tl-item tl-item--clickable' +
        (extraClass ? ' ' + extraClass : '') +
        '" style="animation-delay:' +
        index * 0.06 +
        's" data-record-id="' + record.id + '" onclick="showRecordDetail(' + record.id + ')">' +
        '<div class="tl-dot-wrap"><div class="tl-dot tl-dot--' + status.class + '"></div></div>' +
        posterHtml +
        '<div class="tl-body">' +
        '<div class="tl-title">' +
        titleText +
        '</div>' +
        '<div class="tl-meta">' +
        epHtml +
        '<span class="tl-status tl-status--' + status.class + '"><span class="tl-status-dot"></span>' +
        status.text +
        '</span>' +
        '<span class="tl-source ' + sourceClass + '">' +
        escapeHtml(record.source) +
        '</span>' +
        '<span class="tl-time"><span class="tl-relative-time">' +
        relativeTime +
        '</span> · ' +
        timeStr +
        '</span>' +
        '</div>' +
        msgHtml +
        '</div>' +
        '</div>'
    )
  }

  // 实时增量：队列式补间 —— 新记录从顶部滑入，原有条目整体下移一格，末尾的滑出
  function prependTimelineRecords(records) {
    const container = document.getElementById('sync-timeline')
    if (!container) return

    const track = container.querySelector('.tl-track')
    if (!track) {
      // 之前是空状态（暂无记录），有新记录时整表渲染
      renderTimeline(records)
      return
    }

    const existingIds = {}
    track.querySelectorAll('.tl-item').forEach(function (item) {
      existingIds[item.getAttribute('data-record-id')] = true
    })

    const fresh = records.filter(function (record) {
      return !existingIds[String(record.id)]
    })
    if (fresh.length === 0) return

    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)'
    ).matches

    // 插队之前先记下每条旧记录的位置，插完用位移补间拉回视觉连续
    const beforeTops = new Map()
    if (!reduceMotion) {
      track.querySelectorAll('.tl-item').forEach(function (item) {
        beforeTops.set(item, item.getBoundingClientRect().top)
      })
    }

    const scratch = document.createElement('div')
    scratch.innerHTML = fresh
      .map(function (record, index) {
        return buildTimelineItemHtml(record, index, 'tl-item--new')
      })
      .join('')

    const firstItem = track.firstElementChild
    while (scratch.firstElementChild) {
      track.insertBefore(scratch.firstElementChild, firstItem)
    }

    // 时间线只保留最近 6 条：多出来的先脱离文档流占住原位，再滑出淡出
    const items = Array.prototype.slice.call(
      track.querySelectorAll('.tl-item')
    )
    const overflow = items.slice(DASHBOARD_TIMELINE_LIMIT)
    const trackRect = track.getBoundingClientRect()
    overflow.forEach(function (item) {
      if (reduceMotion) {
        item.remove()
        return
      }
      const rect = item.getBoundingClientRect()
      item.style.position = 'absolute'
      item.style.left = rect.left - trackRect.left + 'px'
      item.style.top = rect.top - trackRect.top + 'px'
      item.style.width = rect.width + 'px'
      item.style.margin = '0'
      item.style.pointerEvents = 'none'
      const anim = item.animate(
        [
          { opacity: 1, transform: 'translateY(0)' },
          { opacity: 0, transform: 'translateY(18px)' },
        ],
        { duration: 260, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' }
      )
      anim.onfinish = function () {
        item.remove()
      }
    })

    if (!reduceMotion) {
      // 旧条目：从原位置补间到新位置（整体下移一格）
      track.querySelectorAll('.tl-item').forEach(function (item) {
        if (overflow.indexOf(item) !== -1) return
        const before = beforeTops.get(item)
        if (before == null) return
        const delta = before - item.getBoundingClientRect().top
        if (Math.abs(delta) < 1) return
        item.animate(
          [
            { transform: 'translateY(' + delta + 'px)' },
            { transform: 'translateY(0)' },
          ],
          { duration: 340, easing: 'cubic-bezier(0.22, 0.61, 0.36, 1)' }
        )
      })
    }

    // 高亮动画结束后摘掉标记类，避免元素越攒越多
    window.setTimeout(function () {
      track.querySelectorAll('.tl-item--new').forEach(function (item) {
        item.classList.remove('tl-item--new')
      })
    }, 2000)

    loadTimelinePosters(fresh)
  }

  // 数字滚动：首屏从 0 累加，实时更新从当前显示值补间过去（reduced-motion 直接落值）
  function animateStatNumber(el, value) {
    if (!el) return

    const nextText = String(value)
    const matched = nextText.match(/^(-?\d+(?:\.\d+)?)(.*)$/)
    if (!matched) {
      el.textContent = nextText
      return
    }

    const target = parseFloat(matched[1])
    const suffix = matched[2] || ''
    const decimals = (matched[1].split('.')[1] || '').length

    const current = String(el.textContent || '').match(/-?\d+(?:\.\d+)?/)
    const from = current ? parseFloat(current[0]) : 0

    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)'
    ).matches
    if (reduceMotion || from === target) {
      el.textContent = nextText
      return
    }

    if (el._statRaf) cancelAnimationFrame(el._statRaf)

    const duration = 620
    const startedAt = performance.now()
    function tick(now) {
      const progress = Math.min(1, (now - startedAt) / duration)
      const eased = 1 - Math.pow(1 - progress, 3)
      const currentValue = from + (target - from) * eased
      el.textContent =
        (decimals
          ? currentValue.toFixed(decimals)
          : String(Math.round(currentValue))) + suffix
      if (progress < 1) {
        el._statRaf = requestAnimationFrame(tick)
      } else {
        el.textContent = nextText
        el._statRaf = null
      }
    }
    el._statRaf = requestAnimationFrame(tick)
  }

  // 数字变化时轻弹一下（CSS 里控制，reduced-motion 下自动关闭）
  function setStatValue(id, value) {
    const el = document.getElementById(id)
    if (!el) return

    const next = String(value)
    if (el.textContent === next && !el._statRaf) return

    animateStatNumber(el, next)
    el.classList.remove('stats-value--updated')
    void el.offsetWidth
    el.classList.add('stats-value--updated')
  }

  // 应用一帧实时数据：统计卡片、趋势、热力图、最近同步
  function applyDashboardPayload(payload) {
    if (!payload) return

    if (payload.stats) {
      setStatValue('total-syncs', payload.stats.total_syncs)
      setStatValue('success-rate', payload.stats.success_rate + '%')
      setStatValue('today-syncs', payload.stats.today_syncs)
      setStatValue('error-syncs', payload.stats.error_syncs)
      cachedDailyStats = payload.stats.daily_stats
      drawDailyChart(cachedDailyStats)
    }

    if (payload.heatmap) {
      cachedHeatmapData = payload.heatmap
      renderHeatmap(cachedHeatmapData, true)
    }

    if (payload.records) {
      prependTimelineRecords(payload.records)
    }
  }

  // 首页实时推送：一条 SSE 连接，服务端只在数据变化时下发
  let dashboardStream = null

  function connectDashboardStream() {
    if (!window.EventSource || dashboardStream) return

    const source = new EventSource('/api/stats/stream')
    dashboardStream = source

    source.addEventListener('dashboard', function (event) {
      // 首屏数据由 loadDashboardData 负责，避免两路同时渲染
      if (!dashboardLoaded) return

      let payload = null
      try {
        payload = JSON.parse(event.data)
      } catch (e) {
        return
      }
      applyDashboardPayload(payload)
    })

    source.addEventListener('error', function () {
      if (source.readyState === EventSource.CLOSED) dashboardStream = null
    })

    window.addEventListener('beforeunload', function () {
      source.close()
      dashboardStream = null
    })
  }

  function removePosterHint() {
    const hint = document.getElementById('timeline-poster-hint')
    if (hint) hint.remove()
  }

  function showPosterHintBanner() {
    const timeline = document.getElementById('sync-timeline')
    if (!timeline || document.getElementById('timeline-poster-hint')) return

    const hint = document.createElement('div')
    hint.id = 'timeline-poster-hint'
    hint.className =
      'timeline-poster-hint alert alert-warning py-2 px-3 mb-3 small'
    hint.innerHTML =
      '番剧封面无法加载。请前往 <a href="' + appUrl('/config') + '">配置管理</a> 设置 <strong>HTTP 代理</strong>，或配置 <strong>Bangumi API / 图片反代</strong>。详见 <a href="#" id="timeline-poster-hint-announcement">收件箱公告</a>。'
    timeline.parentNode.insertBefore(hint, timeline)

    const announcementLink = document.getElementById(
      'timeline-poster-hint-announcement',
    )
    if (announcementLink) {
      announcementLink.addEventListener('click', function (e) {
        e.preventDefault()
        if (window.BangumiInbox && window.BangumiInbox.openAnnouncementById) {
          window.BangumiInbox.openAnnouncementById('20260616-bgm-image-proxy')
        }
      })
    }
  }

  function maybeShowPosterHint() {
    if (posterPending.size > 0 || posterTrackTotal === 0) return
    if (posterFailed.size < posterTrackTotal) return
    showPosterHintBanner()
  }

  function markPosterFailed(subjectId) {
    document
      .querySelectorAll('.timeline-poster img[data-sid="' + subjectId + '"]')
      .forEach(function (img) {
        const poster = img.closest('.timeline-poster')
        if (!poster) return
        poster.classList.remove('timeline-poster--loading')
        poster.classList.add('timeline-poster--placeholder')
        poster.innerHTML = '<i class="bi bi-film"></i>'
      })
  }

  function finishPoster(subjectId, success) {
    const key = String(subjectId)
    posterPending.delete(key)
    if (!success) {
      posterFailed.add(key)
      markPosterFailed(subjectId)
    }
    maybeShowPosterHint()
  }

  function loadTimelinePosters(records) {
    const subjectIds = []
    const seen = {}
    records.forEach(function (record) {
      if (!record.subject_id || seen[record.subject_id]) return
      seen[record.subject_id] = true
      subjectIds.push(record.subject_id)
    })
    if (subjectIds.length === 0) return

    posterTrackTotal = subjectIds.length
    posterPending.clear()
    posterFailed.clear()
    subjectIds.forEach(function (sid) {
      posterPending.add(String(sid))
    })
    removePosterHint()

    const needFetch = []
    subjectIds.forEach(function (sid) {
      if (posterCache[sid]) {
        applyPoster(sid, posterCache[sid])
        return
      }
      try {
        const cached = localStorage.getItem(posterStorageKey(sid))
        if (cached) {
          posterCache[sid] = cached
          applyPoster(sid, cached)
          return
        }
      } catch (e) {}
      needFetch.push(sid)
    })

    if (needFetch.length === 0) return

    const params = new URLSearchParams()
    needFetch.forEach(function (sid) {
      params.append('subject_ids', sid)
    })

    apiFetch('/api/bgm/subjects/posters?' + params.toString())
      .then(function (data) {
        if (data.status !== 'success') throw new Error('bad response')
        const posters = data.posters || {}
        needFetch.forEach(function (sid) {
          const url = posters[String(sid)]
          if (url) {
            posterCache[sid] = url
            try {
              localStorage.setItem(posterStorageKey(sid), url)
            } catch (e) {}
            applyPoster(sid, url)
          } else {
            finishPoster(sid, false)
          }
        })
      })
      .catch(function (error) {
        console.error('批量加载封面失败:', error)
        needFetch.forEach(function (sid) {
          finishPoster(sid, false)
        })
      })
  }

  // 修复：querySelectorAll 确保同一 subject_id 的所有记录都设置海报
  function applyPoster(subjectId, url) {
    document
      .querySelectorAll('.timeline-poster img[data-sid="' + subjectId + '"]')
      .forEach(function (img) {
        img.onload = function () {
          img.classList.add('loaded')
          const poster = img.closest('.timeline-poster')
          if (poster) poster.classList.remove('timeline-poster--loading')
          finishPoster(subjectId, true)
        }
        img.onerror = function () {
          finishPoster(subjectId, false)
        }
        img.src = url
      })
  }

  function formatNumber(num) {
    if (num == null) return '0'
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M'
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K'
    return String(num)
  }

  // ========== 热力图 ==========

  function renderHeatmap(data, animate) {
    const container = document.getElementById('heatmap-container')
    if (!data) {
      container.innerHTML =
        '<div class="text-center text-muted py-3">无数据</div>'
      return
    }

    const countMap = {}
    data.forEach(function (d) {
      countMap[d.date] = d.count
    })

    const today = new Date()
    const endDate = new Date(today)
    const startDate = new Date(today)
    startDate.setDate(startDate.getDate() - 364)

    const startDay = startDate.getDay()
    const gridStart = new Date(startDate)
    gridStart.setDate(gridStart.getDate() - startDay)

    // 生成所有格子数据
    const cells = []
    const d = new Date(gridStart)
    while (d <= endDate || d.getDay() !== 0) {
      const dateStr = d.toISOString().split('T')[0]
      const count = countMap[dateStr] || 0
      const level =
        count === 0 ? 0 : count <= 3 ? 1 : count <= 6 ? 2 : count <= 9 ? 3 : 4
      const isCurrentYear = d >= startDate && d <= endDate
      cells.push({
        date: dateStr,
        count: count,
        level: isCurrentYear ? level : 0,
        dim: !isCurrentYear,
      })
      d.setDate(d.getDate() + 1)
      if (cells.length > 400) break
    }

    // 计算自适应尺寸：根据容器宽度决定格子大小
    const containerWidth = container.offsetWidth - 16
    const totalCols = Math.ceil(cells.length / 7)
    const totalRows = 7
    const gap = 2

    let cellSize = Math.floor(
      (containerWidth - (totalCols - 1) * gap) / totalCols,
    )
    if (cellSize < 4) cellSize = 4
    if (cellSize > 14) cellSize = 14

    // 渲染网格（先构建 DOM，再批量设置动画延迟）
    let html =
      '<div class="heatmap-grid" style="grid-template-rows:repeat(' + totalRows + ',' + cellSize + 'px);grid-auto-columns:' + cellSize + 'px;gap:' + gap + 'px;">'

    cells.forEach(function (cell) {
      const opacity = cell.dim ? '0.3' : '1'
      html +=
        '<div class="heatmap-cell heatmap-cell--level-' + cell.level + '" style="opacity:' + opacity + ';width:' + cellSize + 'px;height:' + cellSize + 'px" data-tip="' + cell.date + ' · ' + cell.count + ' 次同步"></div>'
    })
    html += '</div>'

    requestAnimationFrame(function () {
      container.innerHTML = html
      var cellEls = container.querySelectorAll('.heatmap-cell')
      if (animate) {
        container.classList.add('heatmap-animate')
        for (var i = 0; i < cellEls.length; i++) {
          cellEls[i].style.animationDelay = Math.floor(i / 7) * 5 + 'ms'
        }
      } else {
        container.classList.remove('heatmap-animate')
      }
      updateHeatmapLegend()
    })
  }

  function updateHeatmapLegend() {
    const primary = themeManager ? themeManager.getPrimaryColor() : '#f09199'
    const rgb = hexToRgb(primary)
    if (!rgb) return
    const isDark =
      document.documentElement.getAttribute('data-theme') === 'dark'
    const opacities = isDark ? [0.12, 0.24, 0.38] : [0.25, 0.5, 0.75]
    const level4 = isDark
      ? 'rgba(' + rgb.r + ',' + rgb.g + ',' + rgb.b + ',0.54)'
      : primary

    const style = document.createElement('style')
    style.id = 'heatmap-dynamic-colors'
    style.textContent =
      '.heatmap-legend__cell--l1{background-color:rgba(' +
      rgb.r +
      ',' +
      rgb.g +
      ',' +
      rgb.b +
      ',' +
      opacities[0] +
      ')}' +
      '.heatmap-legend__cell--l2{background-color:rgba(' +
      rgb.r +
      ',' +
      rgb.g +
      ',' +
      rgb.b +
      ',' +
      opacities[1] +
      ')}' +
      '.heatmap-legend__cell--l3{background-color:rgba(' +
      rgb.r +
      ',' +
      rgb.g +
      ',' +
      rgb.b +
      ',' +
      opacities[2] +
      ')}' +
      '.heatmap-legend__cell--l4{background-color:' +
      level4 +
      '}'

    const existing = document.getElementById('heatmap-dynamic-colors')
    if (existing) existing.remove()
    document.head.appendChild(style)
  }

  function hexToRgb(hex) {
    if (!hex) return null
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
    if (result) {
      return {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16),
      }
    }
    const rgb = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(hex)
    if (rgb) {
      return { r: +rgb[1], g: +rgb[2], b: +rgb[3] }
    }
    return null
  }

  // ========== 图表 ==========

  function getChartColors() {
    const primary = themeManager ? themeManager.getPrimaryColor() : '#f09199'
    const rgb = hexToRgb(primary) || { r: 59, g: 130, b: 246 }
    return { primary, rgb }
  }

  // 本地日期字符串 YYYY-MM-DD。不能直接用 toISOString()：
  // 它返回 UTC 日期，与图表标签的 toLocaleDateString()（本地）不一致，
  // 非 UTC 时区会整体错一天。
  function localDateStr(date) {
    var y = date.getFullYear()
    var m = String(date.getMonth() + 1).padStart(2, '0')
    var d = String(date.getDate()).padStart(2, '0')
    return y + '-' + m + '-' + d
  }

  function buildLast7Days(daily) {
    var dates = []
    var rows = []
    for (var i = 6; i >= 0; i--) {
      var date = new Date()
      date.setDate(date.getDate() - i)
      var dateStr = localDateStr(date)
      dates.push(new Date(date).toLocaleDateString())
      var found = daily.find(function (item) {
        return item.date === dateStr
      })
      rows.push(found || null)
    }
    return { dates: dates, rows: rows }
  }

  function drawLlmTrendCharts(daily) {
    if (!Array.isArray(daily)) return
    var series = buildLast7Days(daily)
    var colors = getChartColors()
    var isDark = document.documentElement.getAttribute('data-theme') === 'dark'
    var gridColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.1)'
    var textColor = isDark ? '#909296' : '#6c757d'

    var tokens = series.rows.map(function (r) {
      return r ? r.total_tokens || 0 : 0
    })
    if (llmTokensChartInstance) {
      llmTokensChartInstance.destroy()
      llmTokensChartInstance = null
    }
    var tokensCanvas = document.getElementById('llmTokensChart')
    if (tokensCanvas) {
      llmTokensChartInstance = new Chart(tokensCanvas.getContext('2d'), {
        type: 'bar',
        data: {
          labels: series.dates,
          datasets: [
            {
              label: 'Tokens',
              data: tokens,
              backgroundColor:
                'rgba(' +
                colors.rgb.r +
                ',' +
                colors.rgb.g +
                ',' +
                colors.rgb.b +
                ',' +
                (isDark ? '0.55' : '0.7') +
                ')',
              borderRadius: 4,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 200 },
          plugins: { legend: { display: false } },
          scales: {
            y: {
              beginAtZero: true,
              grid: { color: gridColor },
              ticks: { color: textColor },
            },
            x: { grid: { color: gridColor }, ticks: { color: textColor } },
          },
        },
      })
    }

    var errorRate = series.rows.map(function (r) {
      if (!r || !r.calls) return 0
      return Math.round(((r.error_count || 0) / r.calls) * 1000) / 10
    })
    var latency = series.rows.map(function (r) {
      return r ? r.avg_latency_ms || 0 : 0
    })
    if (llmQualityChartInstance) {
      llmQualityChartInstance.destroy()
      llmQualityChartInstance = null
    }
    var qualityCanvas = document.getElementById('llmQualityChart')
    if (qualityCanvas) {
      llmQualityChartInstance = new Chart(qualityCanvas.getContext('2d'), {
        type: 'line',
        data: {
          labels: series.dates,
          datasets: [
            {
              label: '错误率 %',
              data: errorRate,
              borderColor: 'rgb(255, 99, 132)',
              backgroundColor: 'rgba(255, 99, 132, 0.15)',
              tension: 0.4,
              yAxisID: 'y',
            },
            {
              label: '平均延迟 ms',
              data: latency,
              borderColor: colors.primary,
              backgroundColor: 'rgba(0,0,0,0)',
              tension: 0.4,
              yAxisID: 'y1',
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 200 },
          plugins: {
            legend: {
              display: true,
              labels: { color: textColor, boxWidth: 12 },
            },
          },
          scales: {
            y: {
              beginAtZero: true,
              position: 'left',
              grid: { color: gridColor },
              ticks: {
                color: textColor,
                callback: function (value) {
                  return value + '%'
                },
              },
            },
            y1: {
              beginAtZero: true,
              position: 'right',
              grid: { drawOnChartArea: false },
              ticks: { color: textColor },
            },
            x: { grid: { color: gridColor }, ticks: { color: textColor } },
          },
        },
      })
    }
  }

  function buildChartData(dailyStats) {
    const { primary, rgb } = getChartColors()
    const isDark =
      document.documentElement.getAttribute('data-theme') === 'dark'
    const dates = []
    const counts = []
    for (var i = 6; i >= 0; i--) {
      var date = new Date()
      date.setDate(date.getDate() - i)
      var dateStr = localDateStr(date)
      dates.push(new Date(date).toLocaleDateString())
      var found = dailyStats.find(function (item) {
        return item.date === dateStr
      })
      counts.push(found ? found.count : 0)
    }
    return {
      dates: dates,
      counts: counts,
      primary: primary,
      rgb: rgb,
      isDark: isDark,
      gridColor: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.1)',
      textColor: isDark ? '#909296' : '#6c757d',
      pointBorder: isDark ? '#24262f' : 'white',
    }
  }

  function drawDailyChart(dailyStats) {
    var c = buildChartData(dailyStats)

    if (dailyChartInstance) {
      dailyChartInstance.destroy()
      dailyChartInstance = null
    }

    var ctx = document.getElementById('dailyChart').getContext('2d')
    dailyChartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: c.dates,
        datasets: [
          {
            label: '同步次数',
            data: c.counts,
            borderColor: c.primary,
            backgroundColor:
              'rgba(' +
              c.rgb.r +
              ',' +
              c.rgb.g +
              ',' +
              c.rgb.b +
              ',' +
              (c.isDark ? '0.06' : '0.1') +
              ')',
            tension: 0.4,
            fill: true,
            pointBackgroundColor: c.primary,
            pointBorderColor: c.pointBorder,
            pointBorderWidth: 2,
            pointRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 200 },
        plugins: { legend: { display: false } },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: c.gridColor },
            ticks: { color: c.textColor },
          },
          x: { grid: { color: c.gridColor }, ticks: { color: c.textColor } },
        },
      },
    })
  }
