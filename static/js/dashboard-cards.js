/**
 * 仪表盘卡片自定义（显示 / 排序）
 *
 * 卡片清单由服务端按 app/core/dashboard_registry.py 渲染到 #dashboardGrid，
 * 这里只负责按浏览器本地偏好重排与隐藏。偏好存在 localStorage
 * （key: app-dashboard-cards，值 {version, order, hidden}），不落服务端。
 *
 * 新增卡片无需改本文件：注册表里的卡片会自动出现在自定义弹窗末尾。
 */
;(function () {
  'use strict'

  var STORAGE_KEY = 'app-dashboard-cards'
  var STORAGE_VERSION = 1
  var ITEM_SELECTOR = '.dashboard-grid__item'

  // 注册表默认状态（首次访问 / 恢复默认时使用），init 时从 DOM 抓一次
  var defaults = null
  // 当前编辑中的偏好
  var state = null
  var modalEl = null
  var modalInstance = null

  function getGrid() {
    return document.getElementById('dashboardGrid')
  }

  function getItems() {
    var grid = getGrid()
    if (!grid) return []
    return Array.prototype.slice.call(grid.querySelectorAll(ITEM_SELECTOR))
  }

  function cardId(el) {
    return el.getAttribute('data-dashboard-card') || ''
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
  }

  function isStringArray(value) {
    return (
      Array.isArray(value) &&
      value.every(function (item) {
        return typeof item === 'string'
      })
    )
  }

  // ========== 偏好读写 ==========

  function readState() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY)
      if (!raw) return null
      var parsed = JSON.parse(raw)
      if (!parsed || parsed.version !== STORAGE_VERSION) return null
      return {
        version: STORAGE_VERSION,
        order: isStringArray(parsed.order) ? parsed.order.slice() : [],
        hidden: isStringArray(parsed.hidden) ? parsed.hidden.slice() : [],
      }
    } catch (e) {
      // 隐私模式下 localStorage 不可读或 JSON 损坏，按「无偏好」处理
      return null
    }
  }

  function writeState(next) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch (e) {
      // 隐私模式下 localStorage 可能不可写，忽略即可（本次会话内仍然生效）
    }
  }

  function captureDefaults() {
    var items = getItems()
    defaults = {
      order: items.map(cardId),
      hidden: items
        .filter(function (el) {
          return el.classList.contains('d-none')
        })
        .map(cardId),
    }
  }

  // ========== 应用到网格 ==========

  function applyState(next) {
    var grid = getGrid()
    if (!grid || !next) return

    var items = getItems()
    var position = {}
    next.order.forEach(function (id, index) {
      position[id] = index
    })
    // 模型未记录过的卡片排在已记录卡片之后，并保持 DOM 原序
    var domIndex = {}
    items.forEach(function (el, index) {
      domIndex[cardId(el)] = index
    })
    items.sort(function (a, b) {
      var left = position[cardId(a)]
      var right = position[cardId(b)]
      if (left === undefined) left = next.order.length + domIndex[cardId(a)]
      if (right === undefined) right = next.order.length + domIndex[cardId(b)]
      return left - right
    })

    items.forEach(function (el) {
      var id = cardId(el)
      grid.appendChild(el)
      el.classList.toggle('d-none', next.hidden.indexOf(id) !== -1)
    })
  }

  function persist() {
    writeState(state)
    applyState(state)
  }

  // ========== 弹窗 ==========

  function ensureModal() {
    if (modalEl) return modalEl

    modalEl = document.createElement('div')
    modalEl.className = 'modal fade dashboard-customize-modal'
    modalEl.id = 'dashboardCustomizeModal'
    modalEl.tabIndex = -1
    modalEl.setAttribute('aria-labelledby', 'dashboardCustomizeModalLabel')
    modalEl.setAttribute('aria-hidden', 'true')
    modalEl.innerHTML =
      '<div class="modal-dialog modal-dialog-centered modal-dialog-scrollable">' +
      '<div class="modal-content dashboard-customize__content">' +
      '<div class="modal-header dashboard-customize__header">' +
      '<div class="dashboard-customize__head">' +
      '<h5 class="modal-title dashboard-customize__title" id="dashboardCustomizeModalLabel">自定义仪表盘</h5>' +
      '<div class="dashboard-customize__hint">选择要显示的卡片，并调整它们的顺序。偏好只保存在当前浏览器。</div>' +
      '</div>' +
      '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="关闭"></button>' +
      '</div>' +
      '<div class="modal-body dashboard-customize__body">' +
      '<ul class="dashboard-customize__list" id="dashboardCustomizeList"></ul>' +
      '</div>' +
      '<div class="modal-footer dashboard-customize__footer">' +
      '<button type="button" class="btn btn-outline-secondary btn-sm dashboard-customize__reset" id="dashboardCustomizeReset">恢复默认</button>' +
      '<button type="button" class="btn btn-primary btn-sm dashboard-customize__done" data-bs-dismiss="modal">完成</button>' +
      '</div>' +
      '</div></div>'
    document.body.appendChild(modalEl)

    modalEl.addEventListener('click', onModalClick)
    modalEl.addEventListener('change', onModalChange)
    return modalEl
  }

  /** 按当前偏好排出弹窗里的卡片顺序 */
  function orderedItems() {
    var items = getItems()
    var byId = {}
    items.forEach(function (el) {
      byId[cardId(el)] = el
    })

    var ordered = []
    state.order.forEach(function (id) {
      if (byId[id]) {
        ordered.push(byId[id])
        delete byId[id]
      }
    })
    items.forEach(function (el) {
      var id = cardId(el)
      if (byId[id]) {
        ordered.push(byId[id])
        delete byId[id]
      }
    })
    return ordered
  }

  function buildRow(el, index, total) {
    var id = cardId(el)
    var title = el.getAttribute('data-card-title') || id
    var description = el.getAttribute('data-card-description') || ''
    var visible = state.hidden.indexOf(id) === -1

    return (
      '<li class="dashboard-customize__row" data-card-id="' +
      escapeHtml(id) +
      '">' +
      '<div class="dashboard-customize__info">' +
      '<div class="dashboard-customize__name">' +
      escapeHtml(title) +
      '</div>' +
      (description
        ? '<div class="dashboard-customize__desc">' +
          escapeHtml(description) +
          '</div>'
        : '') +
      '</div>' +
      '<div class="dashboard-customize__actions">' +
      '<button type="button" class="dashboard-customize__move" data-dir="up" aria-label="上移"' +
      (index === 0 ? ' disabled' : '') +
      '><i class="bi bi-arrow-up"></i></button>' +
      '<button type="button" class="dashboard-customize__move" data-dir="down" aria-label="下移"' +
      (index === total - 1 ? ' disabled' : '') +
      '><i class="bi bi-arrow-down"></i></button>' +
      '<div class="form-check form-switch dashboard-customize__switch mb-0">' +
      '<input class="form-check-input" type="checkbox" role="switch" data-role="visible"' +
      (visible ? ' checked' : '') +
      ' aria-label="显示' +
      escapeHtml(title) +
      '">' +
      '</div>' +
      '</div></li>'
    )
  }

  function renderList() {
    if (!modalEl) return
    var list = modalEl.querySelector('#dashboardCustomizeList')
    if (!list) return

    var items = orderedItems()
    var html = ''
    items.forEach(function (el, index) {
      html += buildRow(el, index, items.length)
    })
    list.innerHTML = html
  }

  function move(id, dir) {
    var order = state.order.slice()
    var index = order.indexOf(id)
    if (index === -1) return
    var target = dir === 'up' ? index - 1 : index + 1
    if (target < 0 || target >= order.length) return
    order[index] = order[target]
    order[target] = id
    state.order = order
    persist()
    renderList()
  }

  function onModalClick(event) {
    var moveBtn = event.target.closest('.dashboard-customize__move')
    if (moveBtn) {
      var row = moveBtn.closest('[data-card-id]')
      if (row) move(row.getAttribute('data-card-id'), moveBtn.dataset.dir)
      return
    }
    if (event.target.closest('#dashboardCustomizeReset')) {
      resetAll()
    }
  }

  function onModalChange(event) {
    var input = event.target.closest('input[data-role="visible"]')
    if (!input) return
    var row = input.closest('[data-card-id]')
    if (!row) return

    var id = row.getAttribute('data-card-id')
    var hidden = state.hidden.filter(function (item) {
      return item !== id
    })
    if (!input.checked) hidden.push(id)
    state.hidden = hidden
    persist()
  }

  function resetAll() {
    try {
      window.localStorage.removeItem(STORAGE_KEY)
    } catch (e) {
      // 忽略：即使删不掉，下面的内存状态也会恢复到默认
    }
    state = {
      version: STORAGE_VERSION,
      order: defaults.order.slice(),
      hidden: defaults.hidden.slice(),
    }
    applyState(state)
    renderList()
  }

  /** 把 DOM 中新增的卡片追加到偏好末尾，避免注册表更新后新卡被丢掉 */
  function normalizeState(next) {
    var domIds = getItems().map(cardId)
    domIds.forEach(function (id) {
      if (next.order.indexOf(id) === -1) next.order.push(id)
    })
    return next
  }

  function openModal() {
    ensureModal()
    state = normalizeState(readState() || {
      version: STORAGE_VERSION,
      order: defaults.order.slice(),
      hidden: defaults.hidden.slice(),
    })
    renderList()

    if (window.bootstrap && window.bootstrap.Modal) {
      if (!modalInstance) modalInstance = new window.bootstrap.Modal(modalEl)
      modalInstance.show()
    }
  }

  function init() {
    if (!getGrid()) return
    captureDefaults()

    var stored = readState()
    if (stored) applyState(normalizeState(stored))

    var btn = document.getElementById('dashboardCustomizeBtn')
    if (btn) btn.addEventListener('click', openModal)
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init)
  } else {
    init()
  }
})()
