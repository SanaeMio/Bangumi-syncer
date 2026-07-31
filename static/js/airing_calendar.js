// 我在追的番剧放送清单：调用 /api/airing-calendar 渲染未来 30 天在追番剧的放送日程
// 仅在 Archive 启用时显示该卡片（由 API 返回的 archive_enabled 控制）
;(function () {
  'use strict'

  // 当前状态：固定 only_watching=true，仅展示在追番剧的放送日程
  var state = {
    days: 30, // 固定 30 天
    subjectType: 0, // 0=全部, 2=动画, 6=三次元
    loading: false,
    firstLoad: true, // 首次加载强制刷新在看列表缓存
  }

  document.addEventListener('DOMContentLoaded', function () {
    var row = document.getElementById('airing-calendar-row')
    if (!row) return

    // 工具栏：类型切换
    var typeBtns = row.querySelectorAll('.airing-type-btn')
    typeBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var t = parseInt(btn.getAttribute('data-type'), 10)
        if (t === state.subjectType) return
        state.subjectType = t
        typeBtns.forEach(function (b) {
          b.classList.toggle('active', parseInt(b.getAttribute('data-type'), 10) === t)
        })
        loadAiringCalendar()
      })
    })

    // 工具栏：刷新在看列表缓存
    var refreshBtn = document.getElementById('airing-refresh')
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        state.firstLoad = true
        loadAiringCalendar()
      })
    }

    // 首次加载
    loadAiringCalendar()
  })

  /**
   * 拉取放送数据并渲染。
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
      '&only_watching=true' +
      '&subject_type=' + encodeURIComponent(state.subjectType)
    // 首次加载或手动刷新时强制刷新在看列表缓存
    if (state.firstLoad) {
      params += '&refresh=true'
      state.firstLoad = false
    }

    apiFetch('/api/airing-calendar?' + params, { method: 'GET' })
      .then(function (data) {
        state.loading = false
        renderAiringList(data)
      })
      .catch(function (err) {
        state.loading = false
        console.error('加载在追番剧放送失败:', err)
        container.innerHTML =
          '<div class="text-center py-3 app-text-muted-block"><i class="bi bi-exclamation-triangle d-block mb-1"></i><span class="small">加载失败</span></div>'
      })
  }

  /**
   * 渲染在追番剧清单。
   * @param {Object} data - /api/airing-calendar 响应
   */
  function renderAiringList(data) {
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

    // 降级提示：API 未能获取在看列表（无 Bangumi 账号或调用失败），不展示全部放送
    if (!data.only_watching) {
      container.innerHTML =
        '<div class="text-center py-4 app-text-muted-block">' +
        '<i class="bi bi-exclamation-circle d-block mb-2 fs-4"></i>' +
        '<span class="small">无法获取在看列表，请检查 Bangumi 账号配置</span>' +
        '</div>'
      if (summary) summary.classList.add('d-none')
      return
    }

    // 空数据
    if (!data.days || data.days.length === 0 || data.total_episodes === 0) {
      var emptyMsg = '未来 ' + state.days + ' 天暂无在追番剧的放送日程'
      container.innerHTML =
        '<div class="text-center py-4 app-text-muted-block">' +
        '<i class="bi bi-calendar-x d-block mb-2 fs-4"></i>' +
        '<span class="small">' + escapeHtml(emptyMsg) + '</span>' +
        '</div>'
      if (summary) summary.classList.add('d-none')
      return
    }

    // 聚合：按 subject_id 分组
    var groups = aggregateBySubject(data.days)
    if (groups.length === 0) {
      container.innerHTML =
        '<div class="text-center py-4 app-text-muted-block">' +
        '<i class="bi bi-calendar-x d-block mb-2 fs-4"></i>' +
        '<span class="small">未来 ' + state.days + ' 天暂无在追番剧的放送日程</span>' +
        '</div>'
      if (summary) summary.classList.add('d-none')
      return
    }

    // 顶部摘要
    if (summary) {
      summary.textContent = groups.length + ' 部在追 · ' + data.total_episodes + ' 集'
      summary.classList.remove('d-none')
    }

    // 渲染列表
    container.innerHTML = buildListHtml(groups)
  }

  /**
   * 将按日期分组的 episodes 扁平化并按 subject_id 聚合。
   * @param {Array} days - [{date, weekday, episodes: [...]}, ...]
   * @returns {Array} [{subject_id, subject_name, subject_name_cn, subject_type, episodes: [{airdate, ep_sort, ep_name, ep_name_cn}]}]
   *                    按「最近一次放送日期升序」排序
   */
  function aggregateBySubject(days) {
    var map = {}
    var order = []
    for (var i = 0; i < days.length; i++) {
      var d = days[i]
      if (!d.episodes || !d.episodes.length) continue
      for (var j = 0; j < d.episodes.length; j++) {
        var ep = d.episodes[j]
        var key = ep.subject_id
        if (!map[key]) {
          map[key] = {
            subject_id: ep.subject_id,
            subject_name: ep.subject_name || '',
            subject_name_cn: ep.subject_name_cn || '',
            subject_type: ep.subject_type || 0,
            episodes: [],
          }
          order.push(key)
        }
        map[key].episodes.push({
          airdate: ep.airdate,
          ep_sort: ep.episode_sort,
          ep_name: ep.episode_name || '',
          ep_name_cn: ep.episode_name_cn || '',
        })
      }
    }
    // 转数组并按最近放送日期升序（最早的放送排前面）
    var list = order.map(function (k) { return map[k] })
    list.sort(function (a, b) {
      var aFirst = a.episodes[0] ? a.episodes[0].airdate : ''
      var bFirst = b.episodes[0] ? b.episodes[0].airdate : ''
      if (aFirst !== bFirst) return aFirst < bFirst ? -1 : 1
      return a.subject_name.localeCompare(b.subject_name)
    })
    return list
  }

  /**
   * 构造番剧清单 HTML。
   * @param {Array} groups - aggregateBySubject 返回值
   */
  function buildListHtml(groups) {
    var todayStr = getTodayStr()
    var html = '<div class="airing-list">'
    for (var i = 0; i < groups.length; i++) {
      html += buildSubjectRowHtml(groups[i], todayStr)
    }
    html += '</div>'
    return html
  }

  /**
   * 单个番剧行 HTML。
   * @param {Object} g - 单个聚合番剧
   * @param {string} todayStr - 今日 YYYY-MM-DD
   */
  function buildSubjectRowHtml(g, todayStr) {
    var name = g.subject_name_cn || g.subject_name || '未知条目'
    var typeBadge = g.subject_type === 6
      ? '<span class="airing-list__type airing-list__type--real" title="三次元">三次元</span>'
      : '<span class="airing-list__type airing-list__type--anime" title="动画">动画</span>'
    var totalEps = g.episodes.length

    var html =
      '<div class="airing-list__row" data-subject-id="' + g.subject_id + '">' +
      '<div class="airing-list__head">' +
      '<div class="airing-list__title-wrap">' +
      typeBadge +
      '<span class="airing-list__title" title="' + escapeHtml(name) + '">' + escapeHtml(truncate(name, 22)) + '</span>' +
      '</div>' +
      '<div class="airing-list__meta">' +
      '<span class="airing-list__count" title="未来 ' + state.days + ' 天共 ' + totalEps + ' 集">' + totalEps + ' 集</span>' +
      '</div>' +
      '</div>' +
      '<div class="airing-list__eps">'

    // 展示每集放送日期，最多展示 12 条，超出折叠
    var maxShow = 12
    var shown = g.episodes.slice(0, maxShow)
    for (var k = 0; k < shown.length; k++) {
      var ep = shown[k]
      var epLabel = formatEpLabel(ep)
      var dateLabel = formatDateLabel(ep.airdate, todayStr)
      var cls = 'airing-list__ep'
      if (isSameDay(ep.airdate, todayStr)) cls += ' airing-list__ep--today'
      else if (isBeforeToday(ep.airdate, todayStr)) cls += ' airing-list__ep--past'
      html +=
        '<div class="' + cls + '" title="' + escapeHtml(epLabel + ' · ' + ep.airdate) + '">' +
        '<span class="airing-list__ep-date ' + (isSameDay(ep.airdate, todayStr) ? 'airing-list__date--today' : '') + '">' + escapeHtml(dateLabel) + '</span>' +
        '<span class="airing-list__ep-label">' + escapeHtml(epLabel) + '</span>' +
        '</div>'
    }
    var hidden = totalEps - shown.length
    if (hidden > 0) {
      html += '<div class="airing-list__more" title="共 ' + totalEps + ' 集">+' + hidden + '</div>'
    }

    html += '</div></div>'
    return html
  }

  /**
   * 章节标签：#集号 或 章节名
   */
  function formatEpLabel(ep) {
    if (ep.ep_sort != null && ep.ep_sort !== '') return '#' + ep.ep_sort
    return ep.ep_name_cn || ep.ep_name || ''
  }

  /**
   * 日期标签：今天/明天/后天/MM-DD
   */
  function formatDateLabel(dateStr, todayStr) {
    if (isSameDay(dateStr, todayStr)) return '今天'
    var diff = dayDiff(dateStr, todayStr)
    if (diff === 1) return '明天'
    if (diff === 2) return '后天'
    // 超过 2 天显示 MM-DD
    var parts = String(dateStr).split('-')
    return parts.length === 3 ? parts[1] + '-' + parts[2] : dateStr
  }

  /**
   * 判断两个 YYYY-MM-DD 是否同一天
   */
  function isSameDay(a, b) {
    return a === b
  }

  /**
   * 判断 dateStr 是否早于 todayStr
   */
  function isBeforeToday(dateStr, todayStr) {
    return dateStr < todayStr
  }

  /**
   * 计算 dateStr 相对 todayStr 的天数差（dateStr - todayStr，正数表示未来）
   */
  function dayDiff(dateStr, todayStr) {
    var d1 = new Date(dateStr + 'T00:00:00')
    var d2 = new Date(todayStr + 'T00:00:00')
    return Math.round((d1 - d2) / 86400000)
  }

  /**
   * 获取今日 YYYY-MM-DD
   */
  function getTodayStr() {
    var d = new Date()
    var m = String(d.getMonth() + 1).padStart(2, '0')
    var day = String(d.getDate()).padStart(2, '0')
    return d.getFullYear() + '-' + m + '-' + day
  }

  /**
   * 截断字符串，超长加省略号。
   */
  function truncate(s, max) {
    if (!s) return ''
    return s.length > max ? s.slice(0, max) + '…' : s
  }
})();
