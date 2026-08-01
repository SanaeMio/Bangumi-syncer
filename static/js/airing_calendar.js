// 我在追的番剧放送清单：调用 /api/airing-calendar 渲染未来 30 天在追番剧的放送日程
// 仅在 Archive 启用时显示该卡片（由 API 返回的 archive_enabled 控制）
// 多用户模式（[sync] mode=multi）下右上角显示账号切换 dropdown
;(function () {
  'use strict'

  // 当前状态：仅展示在追番剧的放送日程（API 固定"我的追番"语义）
  var state = {
    days: 30, // 固定 30 天
    subjectType: 0, // 0=全部, 2=动画, 6=三次元
    loading: false,
    firstLoad: true, // 首次加载强制刷新在看列表缓存
    account: null, // 多用户模式下当前选中的 Bangumi 账号段名
    multiUser: false,
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

    // 先加载账号列表（判断多用户模式 + 填充 dropdown），再拉取放送数据
    loadBangumiAccounts().then(function () {
      loadAiringCalendar()
    })
  })

  /**
   * 加载 Bangumi 账号列表，多用户且 >1 账号时显示右上角切换 dropdown。
   * 单用户或仅 1 个账号时隐藏 dropdown，按默认活跃账号查询。
   */
  function loadBangumiAccounts() {
    return apiFetch('/api/airing-calendar/accounts', { method: 'GET' })
      .then(function (data) {
        var dropdown = document.getElementById('airing-account-dropdown')
        var menu = document.getElementById('airing-account-menu')
        var label = document.getElementById('airing-account-label')
        if (!dropdown || !menu) {
          state.multiUser = false
          state.account = null
          return
        }

        // 单用户模式或账号数 <=1：隐藏 dropdown，account 留空走默认
        if (data.mode !== 'multi' || !data.accounts || data.accounts.length <= 1) {
          dropdown.classList.add('d-none')
          state.multiUser = false
          state.account = null
          return
        }

        // 多用户且 >1 账号：显示 dropdown
        state.multiUser = true
        dropdown.classList.remove('d-none')

        // 默认选中后端返回的 active 账号
        state.account = data.active || data.accounts[0].section_name

        // 填充下拉菜单
        menu.innerHTML = ''
        data.accounts.forEach(function (acc) {
          var li = document.createElement('li')
          var a = document.createElement('a')
          a.className = 'dropdown-item' + (acc.section_name === state.account ? ' active' : '')
          a.href = '#'
          a.textContent = acc.username
          a.dataset.sectionName = acc.section_name
          a.addEventListener('click', function (e) {
            e.preventDefault()
            if (state.account === acc.section_name) return
            state.account = acc.section_name
            state.firstLoad = true // 切换账号强制刷新在看列表缓存
            // 更新菜单激活态与标签
            menu.querySelectorAll('.dropdown-item').forEach(function (item) {
              item.classList.toggle('active', item.dataset.sectionName === state.account)
            })
            label.textContent = acc.username
            loadAiringCalendar()
          })
          li.appendChild(a)
          menu.appendChild(li)
        })

        // 标签显示当前账号用户名
        var current = data.accounts.find(function (a) {
          return a.section_name === state.account
        })
        if (current) label.textContent = current.username
      })
      .catch(function (err) {
        console.error('加载 Bangumi 账号列表失败:', err)
        // 失败时按单用户模式继续（不阻塞放送日历加载）
        state.multiUser = false
        state.account = null
      })
  }

  /**
   * 拉取放送数据并渲染。
   */
  function loadAiringCalendar() {
    var row = document.getElementById('airing-calendar-row')
    var container = document.getElementById('airing-calendar-container')
    var summary = document.getElementById('airing-calendar-summary')
    if (!row || !container) return

    state.loading = true
    // 进入加载态前确保行可见，避免上次 archive_enabled=false 把行隐藏后，
    // 本次请求失败时错误提示也无法显示
    row.style.display = ''
    container.innerHTML =
      '<div class="text-center py-3 app-text-muted-block"><div class="spinner-border spinner-border-sm" role="status"></div></div>'
    if (summary) summary.classList.add('d-none')

    var params =
      'days=' + encodeURIComponent(state.days) +
      '&subject_type=' + encodeURIComponent(state.subjectType)
    // 多用户模式传当前选中账号段名
    if (state.account) {
      params += '&account=' + encodeURIComponent(state.account)
    }
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
        // catch 分支必须重置 display，否则上次 archive 未启用时 row 被隐藏，
        // 错误提示永远看不见
        row.style.display = ''
        if (summary) summary.classList.add('d-none')
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

    // 在看列表不可用：未配置 Bangumi 账号或获取在看列表失败
    // "我的追番"语义下不展示全部放送，提示用户检查账号配置
    if (data.status === 'watching_unavailable') {
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

    // 渲染列表：用 API 返回的调度器时区 today 作为"今天/明天"判定基准，
    // 避免浏览器本地时区与 [scheduler] timezone 不一致时标签错位
    var todayStr = (data && data.today) ? data.today : getTodayStr()
    container.innerHTML = buildListHtml(groups, todayStr)
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
   * @param {string} todayStr - 今日 YYYY-MM-DD（取 API 返回的调度器时区）
   */
  function buildListHtml(groups, todayStr) {
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
   * 浏览器本地时区今日 YYYY-MM-DD
   * 仅作为 API 未返回 today 字段时的兜底；正常应使用 API 返回的
   * 调度器时区 today，避免跨时区时"今天/明天"标签与放送日期错位。
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
