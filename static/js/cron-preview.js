/**
 * 五段式 Cron 表达式实时预览
 *
 * 为带 data-cron-preview 属性的 <input> 挂载实时解析预览：
 *   - 五段含义（分 / 时 / 日 / 月 / 周）
 *   - 人类可读描述
 *   - 下次 3 次执行时间（以浏览器本地时区计算）
 *
 * 核心解析/描述/转义能力依赖 cron-desc.js（window.CronDesc），
 * 本文件仅负责 input 交互、popover 定位与渲染编排。
 *
 * 兼容 APScheduler CronTrigger 的标准五段语义：
 *   支持通配符、单值、区间、列表、步长（N/S）
 *   不支持 L / W / # 等高级特性
 */
;(function () {
  'use strict'

  // 依赖 cron-desc.js 提供的核心能力
  var CronDesc = window.CronDesc
  if (!CronDesc) {
    console.error('cron-preview.js 依赖 cron-desc.js，请先加载该脚本')
    return
  }
  var FIELD_META = CronDesc.FIELD_META
  var escapeHtml = CronDesc.escapeHtml

  // 每个字段的常用预设（值 → 显示文案）
  var FIELD_PRESETS = {
    minute: [
      { v: '*', label: '每分' },
      { v: '*/5', label: '每 5 分' },
      { v: '*/10', label: '每 10 分' },
      { v: '*/15', label: '每 15 分' },
      { v: '*/30', label: '每 30 分' },
      { v: '0', label: '整点' },
    ],
    hour: [
      { v: '*', label: '每时' },
      { v: '*/2', label: '每 2 时' },
      { v: '*/4', label: '每 4 时' },
      { v: '*/6', label: '每 6 时' },
      { v: '*/12', label: '每 12 时' },
      { v: '0', label: '凌晨' },
    ],
    day: [
      { v: '*', label: '每日' },
      { v: '1', label: '1 日' },
      { v: '15', label: '15 日' },
    ],
    month: [
      { v: '*', label: '每月' },
      { v: '1', label: '1 月' },
      { v: '4', label: '4 月' },
      { v: '7', label: '7 月' },
      { v: '10', label: '10 月' },
    ],
    weekday: [
      { v: '*', label: '每天' },
      { v: '1-5', label: '工作日' },
      { v: '0,6', label: '周末' },
      { v: '1', label: '周一' },
      { v: '3', label: '周三' },
    ],
  }

  /**
   * 计算下次执行时间（本地时区），返回 Date 数组。
   * 采用从当前时间逐分钟推进的算法，最多扫描 366 天。
   */
  function nextRunTimes(parsed, count) {
    count = count || 3
    var now = new Date()
    // 从下一分钟开始
    var cursor = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate(),
      now.getHours(),
      now.getMinutes() + 1,
      0,
      0,
    )
    var results = []
    var limit = new Date(now.getTime() + 366 * 24 * 3600 * 1000)
    while (results.length < count && cursor <= limit) {
      if (matches(parsed, cursor)) {
        results.push(new Date(cursor))
      }
      cursor = new Date(cursor.getTime() + 60 * 1000)
    }
    return results
  }

  function matches(parsed, d) {
    if (!parsed.minute[d.getMinutes()]) return false
    if (!parsed.hour[d.getHours()]) return false
    if (!parsed.day[d.getDate()]) return false
    if (!parsed.month[d.getMonth() + 1]) return false
    // APScheduler CronTrigger: 0=周一, 6=周日；标准 unix: 0=周日, 6=周六
    // 这里按标准 unix（与项目 update_cron = "0 8 * * 3" 周三一致）
    if (!parsed.weekday[d.getDay()]) return false
    return true
  }

  /**
   * 渲染预览到容器。
   */
  function renderPreview(container, expr) {
    try {
      var parsed = CronDesc.parse(expr)
      var desc = CronDesc.describeParsed(parsed)
      var nextTimes = nextRunTimes(parsed, 3)
      var expandedField = container.dataset.expandedField || ''

      var fieldsHtml = FIELD_META.map(function (meta) {
        var raw = expr.trim().split(/\s+/)[FIELD_META.indexOf(meta)] || ''
        var vals = Object.keys(parsed[meta.key])
          .map(Number)
          .sort(function (a, b) {
            return a - b
          })
        var summary = CronDesc.fieldSummary(meta.key, vals)
        var cls =
          vals.length === meta.max - meta.min + 1
            ? 'cron-field--all'
            : 'cron-field--some'
        var isExpanded = expandedField === meta.key
        var expandedCls = isExpanded ? ' cron-field--expanded' : ''
        return (
          '<div class="cron-field ' +
          cls +
          expandedCls +
          '" data-field-key="' +
          meta.key +
          '" role="button" tabindex="0">' +
          '<span class="cron-field__label">' +
          meta.label +
          '<i class="bi bi-chevron-' + (isExpanded ? 'up' : 'down') + ' cron-field__caret"></i></span>' +
          '<span class="cron-field__raw">' +
          escapeHtml(raw) +
          '</span>' +
          '<span class="cron-field__summary">' +
          escapeHtml(summary) +
          '</span>' +
          '</div>'
        )
      }).join('')

      // 展开的预设面板
      var presetHtml = ''
      if (expandedField && FIELD_PRESETS[expandedField]) {
        var currentRaw = expr.trim().split(/\s+/)[
          FIELD_META.findIndex(function (m) { return m.key === expandedField })
        ] || ''
        var presets = FIELD_PRESETS[expandedField]
        presetHtml =
          '<div class="cron-presets">' +
          '<div class="cron-presets__title">选择「' +
          (FIELD_META.find(function (m) { return m.key === expandedField }).label) +
          '」预设</div>' +
          '<div class="cron-presets__grid">' +
          presets
            .map(function (p) {
              var active = p.v === currentRaw ? ' cron-presets__btn--active' : ''
              return (
                '<button type="button" class="cron-presets__btn' +
                active +
                '" data-field-key="' +
                expandedField +
                '" data-field-value="' +
                escapeHtml(p.v) +
                '">' +
                escapeHtml(p.label) +
                '<code>' +
                escapeHtml(p.v) +
                '</code></button>'
              )
            })
            .join('') +
          '</div></div>'
      }

      var nextHtml =
        nextTimes.length > 0
          ? nextTimes
              .map(function (t) {
                return (
                  '<li class="cron-next__item">' +
                  '<span class="cron-next__abs">' +
                  escapeHtml(CronDesc.formatNext(t)) +
                  '</span>' +
                  '<span class="cron-next__rel">' +
                  escapeHtml(CronDesc.relative(t)) +
                  '</span>' +
                  '</li>'
                )
              })
              .join('')
          : '<li class="cron-next__item cron-next__item--empty">未来一年内无匹配</li>'

      container.innerHTML =
        '<div class="cron-preview">' +
        '<div class="cron-preview__desc"><i class="bi bi-translate me-1"></i>' +
        escapeHtml(desc) +
        '</div>' +
        '<div class="cron-fields">' +
        fieldsHtml +
        '</div>' +
        presetHtml +
        '<div class="cron-next">' +
        '<div class="cron-next__title"><i class="bi bi-alarm me-1"></i>下次执行（浏览器时区）</div>' +
        '<ul class="cron-next__list">' +
        nextHtml +
        '</ul>' +
        '</div>' +
        '</div>'
      container.classList.remove('cron-preview-wrap--error')
    } catch (e) {
      container.innerHTML =
        '<div class="cron-preview cron-preview--error">' +
        '<i class="bi bi-exclamation-triangle me-1"></i>' +
        escapeHtml(e.message || '解析失败') +
        '</div>'
      container.classList.add('cron-preview-wrap--error')
    }
  }

  /**
   * 设置 cron 表达式指定段的值，写回 input 并触发更新。
   */
  function setFieldValue(input, fieldKey, value) {
    var parts = input.value.trim().split(/\s+/)
    if (parts.length !== 5) {
      // 当前表达式非法，用默认值补齐
      parts = ['*', '*', '*', '*', '*']
    }
    var idx = FIELD_META.findIndex(function (m) { return m.key === fieldKey })
    if (idx < 0) return
    parts[idx] = value
    input.value = parts.join(' ')
    // 触发 input 事件让预览刷新
    input.dispatchEvent(new Event('input', { bubbles: true }))
    // 同步触发 change 让 config 表单感知
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }

  /**
   * 初始化所有带 data-cron-preview 属性的 input。
   * 交互：点击 input 切换弹窗，输入实时更新，点击页面其他位置关闭。
   */
  function initCronPreviews() {
    var inputs = document.querySelectorAll('input[data-cron-preview]')
    inputs.forEach(function (input) {
      if (input.dataset.cronPreviewInit === '1') return
      input.dataset.cronPreviewInit = '1'

      var popover = ensurePopover()

      var show = function () {
        positionPopover(popover, input)
        renderPreview(popover, input.value)
        popover.classList.add('cron-popover--show')
      }
      var hide = function () {
        if (popover.dataset.ownerId === input.id) {
          popover.classList.remove('cron-popover--show')
        }
      }
      var toggle = function (e) {
        e.stopPropagation()
        if (
          popover.classList.contains('cron-popover--show') &&
          popover.dataset.ownerId === input.id
        ) {
          hide()
        } else {
          show()
        }
      }

      input.addEventListener('click', toggle)
      input.addEventListener('input', function () {
        if (
          popover.classList.contains('cron-popover--show') &&
          popover.dataset.ownerId === input.id
        ) {
          renderPreview(popover, input.value)
        }
      })
    })

    // 全局点击关闭弹窗（点击弹窗内部不关闭）
    var popover = ensurePopover()
    document.addEventListener('click', function (e) {
      if (!popover.classList.contains('cron-popover--show')) return
      if (popover.contains(e.target)) return
      // 点击触发弹窗的 input 本身由 input 的 click 处理，这里跳过
      var ownerId = popover.dataset.ownerId
      if (ownerId && e.target.id === ownerId) return
      popover.classList.remove('cron-popover--show')
    })

    // 弹窗内事件委托：字段卡片切换展开、预设按钮更新值
    popover.addEventListener('click', function (e) {
      // 点击预设按钮
      var presetBtn = e.target.closest('.cron-presets__btn')
      if (presetBtn) {
        e.stopPropagation()
        var ownerId = popover.dataset.ownerId
        var input = ownerId && document.getElementById(ownerId)
        if (input) {
          setFieldValue(input, presetBtn.dataset.fieldKey, presetBtn.dataset.fieldValue)
        }
        return
      }
      // 点击字段卡片切换展开
      var fieldCard = e.target.closest('.cron-field')
      if (fieldCard) {
        e.stopPropagation()
        var key = fieldCard.dataset.fieldKey
        popover.dataset.expandedField =
          popover.dataset.expandedField === key ? '' : key
        ownerId = popover.dataset.ownerId
        input = ownerId && document.getElementById(ownerId)
        if (input) renderPreview(popover, input.value)
      }
    })

    // 滚动/resize 时重新定位
    var reposition = function () {
      if (!popover.classList.contains('cron-popover--show')) return
      var ownerId = popover.dataset.ownerId
      if (!ownerId) return
      var input = document.getElementById(ownerId)
      if (input) positionPopover(popover, input)
    }
    window.addEventListener('scroll', reposition, true)
    window.addEventListener('resize', reposition)
  }

  /**
   * 获取/创建共享单例弹窗 DOM。
   */
  function ensurePopover() {
    var existing = document.getElementById('cron-preview-popover')
    if (existing) return existing
    var el = document.createElement('div')
    el.id = 'cron-preview-popover'
    el.className = 'cron-popover'
    document.body.appendChild(el)
    return el
  }

  /**
   * 将弹窗定位到 input 下方，自动避免右侧/底部溢出。
   */
  function positionPopover(popover, input) {
    var rect = input.getBoundingClientRect()
    var scrollY = window.pageYOffset || document.documentElement.scrollTop
    var scrollX = window.pageXOffset || document.documentElement.scrollLeft
    var popWidth = 360
    var popMaxWidth = window.innerWidth - 32
    if (popWidth > popMaxWidth) popWidth = popMaxWidth

    var left = rect.left + scrollX
    // 右侧溢出时左移
    if (left + popWidth > scrollX + window.innerWidth - 16) {
      left = scrollX + window.innerWidth - popWidth - 16
    }
    // 左侧不超出
    if (left < scrollX + 16) left = scrollX + 16

    var top = rect.bottom + scrollY + 6
    // 预估高度，下方溢出时显示在上方
    var estHeight = 260
    if (top + estHeight > scrollY + window.innerHeight - 16 &&
        rect.top - estHeight - 6 > scrollY) {
      top = rect.top + scrollY - estHeight - 6
    }

    popover.style.top = top + 'px'
    popover.style.left = left + 'px'
    popover.style.width = popWidth + 'px'
    popover.dataset.ownerId = input.id || ''
  }

  /**
   * 强制刷新所有 cron 预览（用于外部动态修改 input.value 后调用）。
   */
  function refreshAllCronPreviews() {
    var popover = document.getElementById('cron-preview-popover')
    if (!popover || !popover.classList.contains('cron-popover--show')) return
    var ownerId = popover.dataset.ownerId
    if (!ownerId) return
    var input = document.getElementById(ownerId)
    if (input) renderPreview(popover, input.value)
  }

  // 暴露给外部以便动态加载场景（如 summary modal、loadConfig 后）调用
  window.refreshCronPreviews = initCronPreviews
  window.refreshAllCronPreviews = refreshAllCronPreviews

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCronPreviews)
  } else {
    initCronPreviews()
  }
})()
