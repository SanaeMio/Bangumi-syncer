// 番剧放送日历视图：调用 /api/airing-calendar 渲染未来 N 天放送日程
// 仅在 Archive 启用时由 dashboard.js 显示该卡片
  'use strict'

  // 当前状态
  var state = {
    days: 14, // 7 / 14 / 30
    onlyWatching: true,
    loading: false,
  }

  var ALLOWED_DAYS = [7, 14, 30]
  var WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']

  document.addEventListener('DOMContentLoaded', function () {
    var row = document.getElementById('airing-calendar-row')
    if (!row) return

    // 工具栏：天数切换
    var dayBtns = row.querySelectorAll('.airing-days-btn')
    dayBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var d = parseInt(btn.getAttribute('data-days'), 10)
        if (ALLOWED_DAYS.indexOf(d) === -1 || d === state.days) return
        state.days = d
        dayBtns.forEach(function (b) {
          b.classList.toggle('active', parseInt(b.getAttribute('data-days'), 10) === d)
        })
        loadAiringCalendar()
      })
    })

    // 工具栏：仅我在追
    var watchBtn = document.getElementById('airing-only-watching')
    if (watchBtn) {
      watchBtn.addEventListener('click', function () {
        state.onlyWatching = !state.onlyWatching
        watchBtn.setAttribute('data-active', state.onlyWatching ? 'true' : 'false')
        watchBtn.classList.toggle('active', state.onlyWatching)
        loadAiringCalendar()
      })
    }

    // 首次加载
    loadAiringCalendar()
  })

  /**
   * 拉取放送日历数据并渲染。
   */
  function loadAiringCalendar() {
    var container = document.getElementById('airing-calendar-container')
    var summary = document.getElementById('airing-calendar-summary')
    if (!container) return

    state.loading = true
    container.innerHTML =
      '<div class="text-center py-3 app-text-muted-block"><div class="spinner-border spinner-border-sm" role="status"></div></div>'
    if (summary) summary.classList.add('d-none')

    var params =
      'days=' + encodeURIComponent(state.days) +
      '&only_watching=' + (state.onlyWatching ? 'true' : 'false')

    apiFetch('/api/airing-calendar?' + params, { method: 'GET' })
      .then(function (data) {
        state.loading = false
        renderAiringCalendar(data)
      })
      .catch(function (err) {
        state.loading = false
        console.error('加载放送日历失败:', err)
        container.innerHTML =
          '<div class="text-center py-3 app-text-muted-block"><i class="bi bi-exclamation-triangle d-block mb-1"></i><span class="small">加载失败</span></div>'
      })
  }

  /**
   * 渲染整个日历视图。
   * @param {Object} data - /api/airing-calendar 响应
   */
  function renderAiringCalendar(data) {
    var row = document.getElementById('airing-calendar-row')
    var container = document.getElementById('airing-calendar-container')
    var summary = document.getElementById('airing-calendar-summary')
    if (!row || !container) return

    // Archive 未启用：隐藏整行
    if (!data.archive_enabled) {
      row.style.display = 'none'
      return
    }
    row.style.display = ''

    // Archive 启用但未导入
    if (data.status === 'archive_not_imported') {
      container.innerHTML =
        '<div class="text-center py-4 app-text-muted-block">' +
        '<i class="bi bi-database-exclamation d-block mb-2 fs-4"></i>' +
        '<span class="small">Archive 暂未导入数据，请先在配置页导入番组快照</span>' +
        '</div>'
      if (summary) summary.classList.add('d-none')
      return
    }

    // 空数据
    if (!data.days || data.days.length === 0 || data.total_episodes === 0) {
      var emptyMsg = state.onlyWatching
        ? '未来 ' + state.days + ' 天暂无在追番剧的放送日程'
        : '未来 ' + state.days + ' 天暂无放送数据'
      container.innerHTML =
        '<div class="text-center py-4 app-text-muted-block">' +
        '<i class="bi bi-calendar-x d-block mb-2 fs-4"></i>' +
        '<span class="small">' + escapeHtml(emptyMsg) + '</span>' +
        '</div>'
      if (summary) summary.classList.add('d-none')
      return
    }

    // 顶部摘要
    if (summary) {
      var label = data.total_episodes + ' 集放送'
      if (data.only_watching) label += ' · 仅在追'
      summary.textContent = label
      summary.classList.remove('d-none')
    }

    // 构造日历网格：按周（7 列）排列
    var html = buildCalendarGrid(data.days)
    container.innerHTML = html
  }

  /**
   * 构造日历网格 HTML。
   * 第一个格子从 today 的星期开始（前面补占位），每 7 天一行。
   * @param {Array} days - [{date, weekday, episodes: [...]}, ...]
   */
  function buildCalendarGrid(days) {
    if (!days.length) return ''
    var todayStr = days[0].date
    var firstWeekday = days[0].weekday // 0=周一 … 6=周日

    var html = '<div class="airing-grid">'

    // 表头：周一 … 周日
    for (var h = 0; h < 7; h++) {
      html +=
        '<div class="airing-grid__head">' +
        '<span class="airing-grid__wd">' + WEEKDAY_LABELS[h] + '</span>' +
        '</div>'
    }

    // 前置占位（对齐到周几）
    for (var p = 0; p < firstWeekday; p++) {
      html += '<div class="airing-grid__cell airing-grid__cell--placeholder"></div>'
    }

    // 日期格子
    for (var i = 0; i < days.length; i++) {
      html += buildDayCellHtml(days[i], todayStr)
    }

    // 末尾补齐到整行
    var totalCells = firstWeekday + days.length
    var tail = (7 - (totalCells % 7)) % 7
    for (var t = 0; t < tail; t++) {
      html += '<div class="airing-grid__cell airing-grid__cell--placeholder"></div>'
    }

    html += '</div>'
    return html
  }

  /**
   * 单日格子 HTML。
   * @param {Object} day - {date, weekday, episodes}
   * @param {string} todayStr - 今日日期 YYYY-MM-DD
   */
  function buildDayCellHtml(day, todayStr) {
    var isToday = day.date === todayStr
    var isWeekend = day.weekday >= 5
    var cellClass = 'airing-grid__cell'
    if (isToday) cellClass += ' airing-grid__cell--today'
    else if (isWeekend) cellClass += ' airing-grid__cell--weekend'
    if (!day.episodes || !day.episodes.length) cellClass += ' airing-grid__cell--empty'

    var html = '<div class="' + cellClass + '" data-date="' + escapeHtml(day.date) + '">'
    // 日期头
    html +=
      '<div class="airing-grid__date">' +
      '<span class="airing-grid__day-num">' + formatDayNum(day.date) + '</span>' +
      '<span class="airing-grid__day-wd">' + WEEKDAY_LABELS[day.weekday] + '</span>' +
      '</div>'

    // 放送列表
    if (day.episodes && day.episodes.length) {
      html += '<div class="airing-grid__eps">'
      // 单日最多展示 6 条，超出折叠
      var maxShow = 6
      var shown = day.episodes.slice(0, maxShow)
      var hidden = day.episodes.length - shown.length
      for (var i = 0; i < shown.length; i++) {
        html += buildEpisodeItemHtml(shown[i])
      }
      if (hidden > 0) {
        html +=
          '<div class="airing-grid__more" title="共 ' + day.episodes.length + ' 集">+' + hidden + '</div>'
      }
      html += '</div>'
    }

    html += '</div>'
    return html
  }

  /**
   * 单条放送章节 HTML。
   * @param {Object} ep - AiringEpisode
   */
  function buildEpisodeItemHtml(ep) {
    var typeClass =
      ep.subject_type === 6
        ? 'airing-grid__ep--real'
        : 'airing-grid__ep--anime'
    var sortText = ep.episode_sort != null ? '#' + ep.episode_sort : ''
    var name = ep.subject_name_cn || ep.subject_name || '未知条目'
    var epName = ep.episode_name_cn || ep.episode_name || ''
    var titleAttr = escapeHtml(name + (epName ? ' · ' + epName : ''))

    var html =
      '<div class="airing-grid__ep ' + typeClass + '" title="' + titleAttr + '">' +
      '<span class="airing-grid__ep-sort">' + escapeHtml(sortText) + '</span>' +
      '<span class="airing-grid__ep-name">' + escapeHtml(truncate(name, 14)) + '</span>' +
      '</div>'
    return html
  }

  /**
   * 从 YYYY-MM-DD 取日号（带 0 补齐的 DD）。
   */
  function formatDayNum(dateStr) {
    var parts = String(dateStr).split('-')
    return parts.length === 3 ? parts[2] : dateStr
  }

  /**
   * 截断字符串，超长加省略号。
   */
  function truncate(s, max) {
    if (!s) return ''
    return s.length > max ? s.slice(0, max) + '…' : s
  }
})();
