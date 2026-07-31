/**
 * 五段式 Cron 表达式描述工具
 *
 * 从 cron-preview.js 抽取的核心能力，供多个场景复用：
 *   - cron-preview.js：配置页 cron 输入框实时预览弹窗
 *   - dashboard.html：首页调度器状态卡只读展示
 *
 * 兼容 APScheduler CronTrigger 的标准五段语义：
 *   支持通配符、单值、区间、列表、步长（N/S）
 *   不支持 L / W / # 等高级特性
 *
 * 暴露接口：window.CronDesc = { parse, describe, formatNext, relative }
 */
;(function () {
  'use strict'

  var FIELD_META = [
    { key: 'minute', label: '分', min: 0, max: 59 },
    { key: 'hour', label: '时', min: 0, max: 23 },
    { key: 'day', label: '日', min: 1, max: 31 },
    { key: 'month', label: '月', min: 1, max: 12 },
    { key: 'weekday', label: '周', min: 0, max: 6 },
  ]

  var WEEKDAY_NAMES = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  var MONTH_NAMES = [
    '',
    '1月',
    '2月',
    '3月',
    '4月',
    '5月',
    '6月',
    '7月',
    '8月',
    '9月',
    '10月',
    '11月',
    '12月',
  ]

  /**
   * 解析单段 cron 表达式，返回该段允许的数值集合。
   * 失败时抛出 Error。
   */
  function parseField(expr, meta) {
    if (expr === '' || expr === '*') {
      return rangeSet(meta.min, meta.max)
    }
    var set = {}
    var parts = expr.split(',')
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i].trim()
      if (!part) continue
      parsePart(part, meta, set)
    }
    return set
  }

  function parsePart(part, meta, set) {
    var stepMatch = part.match(/^(.+?)\/(\d+)$/)
    var step = 1
    var rangePart = part
    if (stepMatch) {
      step = parseInt(stepMatch[2], 10)
      if (!step || step < 1) {
        throw new Error('步长必须为正整数')
      }
      rangePart = stepMatch[1]
    }

    var start, end
    if (rangePart === '*') {
      start = meta.min
      end = meta.max
    } else if (rangePart.indexOf('-') !== -1) {
      var seg = rangePart.split('-')
      if (seg.length !== 2) throw new Error('区间格式错误: ' + part)
      start = parseInt(seg[0], 10)
      end = parseInt(seg[1], 10)
      if (isNaN(start) || isNaN(end)) throw new Error('数值无效: ' + part)
    } else {
      start = parseInt(rangePart, 10)
      if (isNaN(start)) throw new Error('数值无效: ' + part)
      end = step > 1 ? meta.max : start
    }

    if (start < meta.min || end > meta.max || start > end) {
      throw new Error(
        meta.label +
          '段越界: ' +
          part +
          '（允许 ' +
          meta.min +
          '-' +
          meta.max +
          '）',
      )
    }
    for (var v = start; v <= end; v += step) {
      set[v] = true
    }
  }

  function rangeSet(min, max) {
    var s = {}
    for (var i = min; i <= max; i++) s[i] = true
    return s
  }

  /**
   * 解析完整五段 cron 表达式，返回 { minute, hour, day, month, weekday } 集合。
   */
  function parseCron(expr) {
    var parts = (expr || '').trim().split(/\s+/)
    if (parts.length !== 5) {
      throw new Error('五段式 Cron 需要 5 个字段（分 时 日 月 周）')
    }
    var result = {}
    for (var i = 0; i < 5; i++) {
      result[FIELD_META[i].key] = parseField(parts[i], FIELD_META[i])
    }
    return result
  }

  /**
   * 生成人类可读描述。
   */
  function describeCron(parsed) {
    var minute = parsed.minute
    var hour = parsed.hour
    var day = parsed.day
    var month = parsed.month
    var weekday = parsed.weekday

    var isAll = function (s, min, max) {
      for (var i = min; i <= max; i++) if (!s[i]) return false
      return true
    }

    var minAll = isAll(minute, 0, 59)
    var hourAll = isAll(hour, 0, 23)
    var dayAll = isAll(day, 1, 31)
    var monAll = isAll(month, 1, 12)
    var weekAll = isAll(weekday, 0, 6)

    // 完全通配
    if (minAll && hourAll && dayAll && monAll && weekAll) return '每分钟'

    // 检测等差步长
    var minVals = sortedKeys(minute)
    var hourVals = sortedKeys(hour)
    var minStep = detectStep(minVals)
    var hourStep = detectStep(hourVals)

    // */N 分钟
    if (
      minStep > 1 &&
      minAll === false &&
      hourAll &&
      dayAll &&
      monAll &&
      weekAll
    ) {
      return '每 ' + minStep + ' 分钟'
    }
    // */N 小时
    if (
      hourStep > 1 &&
      minVals.length === 1 &&
      minVals[0] === 0 &&
      dayAll &&
      monAll &&
      weekAll
    ) {
      return '每 ' + hourStep + ' 小时'
    }

    // 具体时刻组合
    var timeDesc = ''
    if (minVals.length === 1 && hourVals.length === 1 && !minAll) {
      timeDesc = pad2(hourVals[0]) + ':' + pad2(minVals[0])
    } else if (minAll && !hourAll) {
      timeDesc =
        '每小时整点（' +
        hourVals
          .map(function (h) {
            return pad2(h)
          })
          .join(' / ') +
        ' 时）'
    } else if (!minAll && hourAll) {
      timeDesc =
        '每小时 ' +
        minVals
          .map(function (m) {
            return pad2(m)
          })
          .join('/') +
        ' 分'
    } else if (!minAll && !hourAll) {
      var times = []
      hourVals.forEach(function (h) {
        minVals.forEach(function (m) {
          times.push(pad2(h) + ':' + pad2(m))
        })
      })
      timeDesc =
        times.length <= 4
          ? times.join(' / ')
          : times.slice(0, 3).join(' / ') + ' 等 ' + times.length + ' 个时刻'
    }

    // 日期描述
    var dateParts = []
    if (!monAll) dateParts.push(describeSet(month, 1, 12, MONTH_NAMES))
    if (!weekAll && !dayAll) {
      dateParts.push(
        describeSet(weekday, 0, 6, WEEKDAY_NAMES) +
          ' 与 ' +
          describeSet(day, 1, 31, null, '日'),
      )
    } else if (!weekAll) {
      dateParts.push(describeSet(weekday, 0, 6, WEEKDAY_NAMES))
    } else if (!dayAll) {
      dateParts.push('每月' + describeSet(day, 1, 31, null, '日'))
    }
    var dateDesc = dateParts.join('、')

    if (dateDesc && timeDesc) return dateDesc + ' ' + timeDesc
    // 日期全通配但限定了时刻：加"每天"前缀，如"每天 20:00"
    if (timeDesc && !dateDesc) return '每天 ' + timeDesc
    return dateDesc || timeDesc || '每分钟'
  }

  function sortedKeys(set) {
    return Object.keys(set)
      .map(Number)
      .sort(function (a, b) {
        return a - b
      })
  }

  function detectStep(vals) {
    if (vals.length < 2) return 1
    var step = vals[1] - vals[0]
    for (var i = 2; i < vals.length; i++) {
      if (vals[i] - vals[i - 1] !== step) return 1
    }
    return step
  }

  function describeSet(set, min, max, names, suffix) {
    var vals = Object.keys(set)
      .map(Number)
      .sort(function (a, b) {
        return a - b
      })
    if (vals.length === 0) return ''
    // 检测等差步长
    if (vals.length >= 2) {
      var step = vals[1] - vals[0]
      var isStep = true
      for (var i = 2; i < vals.length; i++) {
        if (vals[i] - vals[i - 1] !== step) {
          isStep = false
          break
        }
      }
      if (
        isStep &&
        vals.length * step === vals[vals.length - 1] - vals[0] + step &&
        step > 1
      ) {
        var label = names ? names[vals[0]] || vals[0] : vals[0]
        var labelEnd = names
          ? names[vals[vals.length - 1]] || vals[vals.length - 1]
          : vals[vals.length - 1]
        if (names) {
          return (
            '每 ' +
            step +
            ' 个' +
            (suffix || '') +
            '（' +
            label +
            '~' +
            labelEnd +
            '）'
          )
        }
        return '每 ' + step + ' ' + (suffix || '个')
      }
    }
    // 全选
    if (vals.length === max - min + 1) {
      return '每' + (suffix || '个')
    }
    // 列举
    if (names) {
      return vals
        .map(function (v) {
          return names[v] || v
        })
        .join('、')
    }
    return vals
      .map(function (v) {
        return v + (suffix || '')
      })
      .join('、')
  }

  function pad2(n) {
    return n < 10 ? '0' + n : '' + n
  }

  function formatDate(d) {
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  function relativeTime(d) {
    var diffMs = d.getTime() - Date.now()
    var min = Math.round(diffMs / 60000)
    if (min < 1) return '即将'
    if (min < 60) return min + ' 分钟后'
    var hr = Math.round(min / 60)
    if (hr < 24) return hr + ' 小时后'
    var day = Math.round(hr / 24)
    return day + ' 天后'
  }

  /**
   * 计算单字段的简短摘要（用于字段卡片展示）。
   * - 全选：'每分' / '每时' ...
   * - 单值：'5分' / '周三'
   * - 多值：'3 个值'
   */
  function fieldSummary(metaKey, vals) {
    var meta = null
    for (var i = 0; i < FIELD_META.length; i++) {
      if (FIELD_META[i].key === metaKey) {
        meta = FIELD_META[i]
        break
      }
    }
    if (!meta) return ''
    if (vals.length === meta.max - meta.min + 1) return '每' + meta.label
    if (vals.length === 1) {
      return (
        (meta.key === 'weekday' ? WEEKDAY_NAMES[vals[0]] : vals[0]) +
        (meta.key === 'weekday' ? '' : meta.label)
      )
    }
    return vals.length + ' 个值'
  }

  /**
   * HTML 转义（防止 cron 字段值注入）。
   */
  function escapeHtml(s) {
    if (s === null || s === undefined) return ''
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
  }

  // 将 APScheduler 的 str(CronTrigger) 输出归一化为标准五段式 cron。
  // APScheduler 输出形如：cron[month='*', day='*', day_of_week='*', hour='*', minute='*/3']
  // 归一化为：*/3 * * * *（minute hour day month weekday）
  // 非 cron[...] 格式则原样返回。

  function normalizeApsCron(expr) {
    if (!expr) return expr
    var s = String(expr).trim()
    if (s.indexOf('cron[') !== 0) return expr
    // 提取方括号内的内容
    var inner = s.slice(s.indexOf('[') + 1, s.lastIndexOf(']'))
    if (!inner) return expr
    var fields = { minute: '*', hour: '*', day: '*', month: '*', weekday: '*' }
    // 匹配 key='value' 片段
    var re = /(\w+)\s*=\s*'([^']*)'/g
    var m
    while ((m = re.exec(inner)) !== null) {
      var key = m[1]
      var val = m[2]
      if (key === 'day_of_week') key = 'weekday'
      if (fields.hasOwnProperty(key)) fields[key] = val || '*'
    }
    return (
      fields.minute +
      ' ' +
      fields.hour +
      ' ' +
      fields.day +
      ' ' +
      fields.month +
      ' ' +
      fields.weekday
    )
  }

  /**
   * 将 cron 字符串转为人类可读描述。
   * 自动归一化 APScheduler 的 cron[...] 格式。
   * 解析失败时返回 fallback（默认空串）。
   */
  function describe(expr, fallback) {
    try {
      return describeCron(parseCron(normalizeApsCron(expr)))
    } catch (e) {
      return fallback || ''
    }
  }

  /**
   * 将 cron 字符串归一化为标准五段式（对外暴露）。
   * 用于在 UI 中展示原始表达式时统一格式。
   */
  function normalize(expr) {
    return normalizeApsCron(expr)
  }

  // 暴露给外部
  window.CronDesc = {
    parse: parseCron,
    describe: describe,
    describeParsed: describeCron,
    normalize: normalize,
    formatNext: formatDate,
    relative: relativeTime,
    fieldSummary: fieldSummary,
    escapeHtml: escapeHtml,
    FIELD_META: FIELD_META,
    WEEKDAY_NAMES: WEEKDAY_NAMES,
    MONTH_NAMES: MONTH_NAMES,
  }
})()
