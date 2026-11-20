/**
 * 仪表盘卡片的拖动、拉伸与显示开关。
 *
 * 卡片清单与默认尺寸 / 摆放由后端注册表给出（app/core/dashboard_registry.py），
 * 本文件只做三件事：
 *   1. 把布局写进卡片的 --dg-x/--dg-y/--dg-w/--dg-h 自定义属性（定位交给 CSS）；
 *   2. 拖动换位、拖右下角改尺寸，并把偏好存进 localStorage（不落服务端）；
 *   3. 自定义弹窗里只控制「显示哪些卡片」，顺序与大小在首页拖出来。
 *
 * 布局单位：横向按 12 栏栅格（列），纵向按像素（高度吸附到 HEIGHT_STEP）。
 * 每次改动后按「重力」向上压缩，避免出现悬空的空隙。
 */
(function () {
    'use strict'

    var STORAGE_KEY = 'app-dashboard-cards'
    var STORAGE_VERSION = 2
    var FALLBACK_COLUMNS = 12
    var FALLBACK_GAP = 24
    var FALLBACK_HEIGHT = 106
    var FALLBACK_MIN_HEIGHT = 96
    var HEIGHT_STEP = 8
    var MAX_HEIGHT = 1600
    var MOBILE_MAX_WIDTH = 767
    var DRAG_THRESHOLD = 3

    var grid = null
    var placeholder = null
    var modalEl = null
    var modalInstance = null
    var state = null
    var defaults = null
    var dragState = null
    var resizeTimer = null
    var readyDispatched = false

    // ========== 基础工具 ==========

    function getGrid() {
        if (!grid || !grid.isConnected) grid = document.getElementById('dashboardGrid')
        return grid
    }

    function getItems() {
        var target = getGrid()
        if (!target) return []
        return Array.prototype.slice.call(
            target.querySelectorAll('.dashboard-grid__item')
        )
    }

    function cardId(el) {
        return el.getAttribute('data-dashboard-card') || ''
    }

    function numberAttr(el, name, fallback) {
        var value = parseInt(el.getAttribute(name), 10)
        return isNaN(value) ? fallback : value
    }

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value))
    }

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
    }

    function isMobile() {
        return window.innerWidth <= MOBILE_MAX_WIDTH
    }

    function metrics() {
        var target = getGrid()
        var style = window.getComputedStyle(target)
        var columns =
            parseInt(style.getPropertyValue('--dg-cols'), 10) || FALLBACK_COLUMNS
        var gap = parseFloat(style.getPropertyValue('--dg-gap'))
        if (isNaN(gap) || gap < 0) gap = FALLBACK_GAP
        var width = target.clientWidth
        var colWidth = (width - (columns - 1) * gap) / columns
        return {
            columns: columns,
            gap: gap,
            width: width,
            colWidth: colWidth,
            stride: colWidth + gap
        }
    }

    function snapHeight(value) {
        return clamp(
            Math.round(value / HEIGHT_STEP) * HEIGHT_STEP,
            FALLBACK_MIN_HEIGHT,
            MAX_HEIGHT
        )
    }

    function minWidthOf(el) {
        return numberAttr(el, 'data-min-width', 2)
    }

    function minHeightOf(el) {
        return numberAttr(el, 'data-min-height', FALLBACK_MIN_HEIGHT)
    }

    // ========== 状态读写 ==========

    function geometryOf(el) {
        var style = el.style
        var x = parseInt(style.getPropertyValue('--dg-x'), 10)
        var y = parseFloat(style.getPropertyValue('--dg-y'))
        var w = parseInt(style.getPropertyValue('--dg-w'), 10)
        var h = parseFloat(style.getPropertyValue('--dg-h'))
        return {
            x: isNaN(x) ? 0 : x,
            y: isNaN(y) ? 0 : y,
            w: isNaN(w) ? numberAttr(el, 'data-card-width', 3) : w,
            h: isNaN(h) ? numberAttr(el, 'data-card-height', FALLBACK_HEIGHT) : h
        }
    }

    function captureDefaults() {
        var layout = {}
        var hidden = []
        getItems().forEach(function (el) {
            var id = cardId(el)
            if (!id) return
            layout[id] = geometryOf(el)
            if (el.classList.contains('d-none')) hidden.push(id)
        })
        defaults = { version: STORAGE_VERSION, layout: layout, hidden: hidden }
    }

    function sanitize(raw) {
        if (!raw || raw.version !== STORAGE_VERSION) return null
        if (!raw.layout || typeof raw.layout !== 'object') return null

        var layout = {}
        Object.keys(raw.layout).forEach(function (id) {
            var item = raw.layout[id]
            if (!item) return
            var x = parseInt(item.x, 10)
            var y = parseFloat(item.y)
            var w = parseInt(item.w, 10)
            var h = parseFloat(item.h)
            layout[id] = {
                x: isNaN(x) ? 0 : clamp(x, 0, FALLBACK_COLUMNS - 1),
                y: isNaN(y) ? 0 : Math.max(0, y),
                w: isNaN(w) ? 3 : clamp(w, 1, FALLBACK_COLUMNS),
                h: isNaN(h)
                    ? FALLBACK_HEIGHT
                    : clamp(h, FALLBACK_MIN_HEIGHT, MAX_HEIGHT)
            }
        })

        var hidden = Array.isArray(raw.hidden)
            ? raw.hidden.filter(function (id) {
                  return typeof id === 'string'
              })
            : []
        return { version: STORAGE_VERSION, layout: layout, hidden: hidden }
    }

    function readState() {
        try {
            var raw = window.localStorage.getItem(STORAGE_KEY)
            if (!raw) return null
            return sanitize(JSON.parse(raw))
        } catch (e) {
            return null
        }
    }

    function writeState(next) {
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
        } catch (e) {
            // 隐私模式等场景写不进去：本次会话内的排序仍然生效
        }
    }

    function cloneDefaults() {
        var layout = {}
        Object.keys(defaults.layout).forEach(function (id) {
            var item = defaults.layout[id]
            layout[id] = { x: item.x, y: item.y, w: item.w, h: item.h }
        })
        return {
            version: STORAGE_VERSION,
            layout: layout,
            hidden: defaults.hidden.slice()
        }
    }

    /** 注册表新增 / 删除了卡片时，让本地偏好跟上 DOM */
    function normalize(next) {
        var result = { version: STORAGE_VERSION, layout: {}, hidden: [] }
        var known = {}

        getItems().forEach(function (el) {
            var id = cardId(el)
            if (!id) return
            known[id] = true
            var item = next.layout[id]
            if (!item) {
                item = defaults.layout[id] || geometryOf(el)
            }
            result.layout[id] = {
                x: clamp(item.x, 0, FALLBACK_COLUMNS - 1),
                y: Math.max(0, item.y),
                w: clamp(item.w, 1, FALLBACK_COLUMNS),
                h: clamp(item.h, FALLBACK_MIN_HEIGHT, MAX_HEIGHT)
            }
        })

        result.hidden = next.hidden.filter(function (id) {
            return known[id]
        })
        return result
    }

    // ========== 布局运算 ==========

    function overlaps(a, b, gap) {
        return (
            a.x < b.x + b.w &&
            b.x < a.x + a.w &&
            a.y < b.y + b.h + gap &&
            b.y < a.y + a.h + gap
        )
    }

    /** 把被移动卡片压到的其它卡片依次往下推（连锁） */
    function pushDown(movedId, gap) {
        var queue = [movedId]
        var guard = 0
        while (queue.length && guard < 500) {
            guard += 1
            var currentId = queue.shift()
            var current = state.layout[currentId]
            if (!current) continue
            Object.keys(state.layout).forEach(function (id) {
                if (id === currentId || state.hidden.indexOf(id) !== -1) return
                var other = state.layout[id]
                if (overlaps(current, other, gap)) {
                    other.y = current.y + current.h + gap
                    queue.push(id)
                }
            })
        }
    }

    /** 重力压缩：按 (y, x) 顺序让每张卡尽量往上贴，消除悬空空隙 */
    /**
     * 为刚打开的卡片找第一个空位（列优先扫描，语义与后端 dashboard_registry._first_fit 一致）。
     * 直接追加到最底部会让两张同宽度的卡上下堆叠，看起来像丢了一行。
     */
    function firstFit(w, h, gap, skipId) {
        var occupied = getItems()
            .filter(function (el) {
                var id = cardId(el)
                return id !== skipId && state.hidden.indexOf(id) === -1
            })
            .map(function (el) {
                return state.layout[cardId(el)]
            })
            .filter(Boolean)
        if (!occupied.length) return { x: 0, y: 0 }

        var columns = metrics().columns
        var candidates = [0]
        occupied.forEach(function (item) {
            candidates.push(item.y + item.h + gap)
        })
        candidates.sort(function (a, b) {
            return a - b
        })

        for (var c = 0; c < candidates.length; c += 1) {
            var y = candidates[c]
            for (var x = 0; x + w <= columns; x += 1) {
                var box = { x: x, y: y, w: w, h: h }
                var clash = occupied.some(function (item) {
                    return overlaps(box, item, gap)
                })
                if (!clash) return { x: x, y: y }
            }
        }
        return { x: 0, y: maxBottom() }
    }

    function compact(gap) {
        var ids = Object.keys(state.layout).filter(function (id) {
            return state.hidden.indexOf(id) === -1
        })
        ids.sort(function (a, b) {
            var left = state.layout[a]
            var right = state.layout[b]
            return left.y - right.y || left.x - right.x
        })

        var placed = []
        ids.forEach(function (id) {
            var item = state.layout[id]
            var y = 0
            placed.forEach(function (other) {
                if (item.x < other.x + other.w && other.x < item.x + item.w) {
                    y = Math.max(y, other.y + other.h + gap)
                }
            })
            item.y = y
            placed.push(item)
        })
    }

    function maxBottom() {
        var bottom = 0
        getItems().forEach(function (el) {
            var id = cardId(el)
            if (state.hidden.indexOf(id) !== -1) return
            var item = state.layout[id]
            if (item) bottom = Math.max(bottom, item.y + item.h)
        })
        return bottom
    }

    // ========== 渲染 ==========

    function writeGeometry(el, item) {
        el.style.setProperty('--dg-x', String(item.x))
        el.style.setProperty('--dg-y', item.y + 'px')
        el.style.setProperty('--dg-w', String(item.w))
        el.style.setProperty('--dg-h', item.h + 'px')
    }

    function apply() {
        var target = getGrid()
        if (!target) return

        var mobile = isMobile()
        var bottom = 0

        getItems().forEach(function (el) {
            var id = cardId(el)
            var item = state.layout[id]
            if (!item) return
            var hidden = state.hidden.indexOf(id) !== -1
            el.classList.toggle('d-none', hidden)
            writeGeometry(el, item)
            if (!hidden && !mobile) bottom = Math.max(bottom, item.y + item.h)
        })

        target.style.setProperty('--dg-height', mobile ? 'auto' : bottom + 'px')
        target.classList.toggle('is-mobile', mobile)

        if (readyDispatched) {
            document.dispatchEvent(
                new CustomEvent('dashboard:layout-changed', {
                    detail: { layout: snapshot() }
                })
            )
        }
    }

    function snapshot() {
        var out = {}
        Object.keys(state.layout).forEach(function (id) {
            var item = state.layout[id]
            out[id] = { x: item.x, y: item.y, w: item.w, h: item.h }
        })
        return out
    }

    function persist() {
        writeState(state)
        apply()
    }

    // ========== 拖动 / 拉伸 ==========

    function ensurePlaceholder() {
        if (placeholder && placeholder.isConnected) return placeholder
        placeholder = document.createElement('div')
        placeholder.className = 'dashboard-grid__item dashboard-grid__placeholder'
        placeholder.setAttribute('aria-hidden', 'true')
        getGrid().appendChild(placeholder)
        return placeholder
    }

    function removePlaceholder() {
        if (placeholder && placeholder.parentNode) {
            placeholder.parentNode.removeChild(placeholder)
        }
        placeholder = null
    }

    function movePlaceholder(target) {
        var el = ensurePlaceholder()
        var m = metrics()
        el.style.left = target.x * m.stride + 'px'
        el.style.top = target.y + 'px'
        el.style.width = Math.max(0, target.w * m.stride - m.gap) + 'px'
        el.style.height = target.h + 'px'
    }

    function startInteraction(event, el, mode) {
        var id = cardId(el)
        var item = state.layout[id]
        if (!item || isMobile()) return

        dragState = {
            id: id,
            el: el,
            mode: mode,
            startX: event.clientX,
            startY: event.clientY,
            origin: { x: item.x, y: item.y, w: item.w, h: item.h },
            target: { x: item.x, y: item.y, w: item.w, h: item.h },
            minWidth: minWidthOf(el),
            minHeight: minHeightOf(el),
            moved: false
        }

        el.classList.add(mode === 'move' ? 'is-dragging' : 'is-resizing')
        getGrid().classList.add('is-interacting')
        movePlaceholder(dragState.target)

        document.addEventListener('pointermove', onPointerMove)
        document.addEventListener('pointerup', onPointerUp)
        document.addEventListener('pointercancel', onPointerUp)
        event.preventDefault()
    }

    function onPointerMove(event) {
        if (!dragState) return

        var dx = event.clientX - dragState.startX
        var dy = event.clientY - dragState.startY
        if (!dragState.moved) {
            if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return
            dragState.moved = true
        }

        var m = metrics()
        var origin = dragState.origin
        var target = dragState.target

        if (dragState.mode === 'move') {
            target.x = clamp(
                Math.round((origin.x * m.stride + dx) / m.stride),
                0,
                m.columns - origin.w
            )
            target.y = Math.max(
                0,
                Math.round((origin.y + dy) / HEIGHT_STEP) * HEIGHT_STEP
            )
            dragState.el.style.transform =
                'translate3d(' + dx + 'px,' + dy + 'px,0)'
        } else {
            target.w = clamp(
                Math.round((origin.w * m.stride + dx) / m.stride),
                dragState.minWidth,
                m.columns - origin.x
            )
            target.h = clamp(
                Math.round((origin.h + dy) / HEIGHT_STEP) * HEIGHT_STEP,
                dragState.minHeight,
                MAX_HEIGHT
            )
            writeGeometry(dragState.el, {
                x: origin.x,
                y: origin.y,
                w: target.w,
                h: target.h
            })
        }

        movePlaceholder(target)
    }

    function onPointerUp() {
        document.removeEventListener('pointermove', onPointerMove)
        document.removeEventListener('pointerup', onPointerUp)
        document.removeEventListener('pointercancel', onPointerUp)

        var current = dragState
        dragState = null
        if (!current) return

        current.el.classList.remove('is-dragging', 'is-resizing')
        current.el.style.transform = ''
        getGrid().classList.remove('is-interacting')
        removePlaceholder()

        if (!current.moved) {
            apply()
            return
        }

        var gap = metrics().gap
        var item = state.layout[current.id]
        item.x = current.target.x
        item.y = current.target.y
        item.w = current.target.w
        item.h = current.target.h

        pushDown(current.id, gap)
        compact(gap)
        persist()
    }

    function onPointerDown(event) {
        if (event.button !== 0 || isMobile()) return
        var el = event.target.closest('.dashboard-grid__item')
        if (!el || el.classList.contains('dashboard-grid__placeholder')) return

        if (event.target.closest('[data-card-resize]')) {
            startInteraction(event, el, 'resize')
            return
        }

        var grip = event.target.closest('[data-card-drag]')
        var header = event.target.closest('.card-header')
        if (!grip && !header) return
        if (event.target.closest('a, button, input, select, textarea') && !grip) return
        startInteraction(event, el, 'move')
    }

    // ========== 自定义弹窗 ==========

    function ensureModal() {
        if (modalEl && modalEl.isConnected) return modalEl

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
            '<div class="dashboard-customize__hint">这里控制显示哪些卡片；勾选后回到首页直接拖动卡片调整位置，拖右下角改变大小。偏好只保存在当前浏览器。</div>' +
            '</div>' +
            '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="关闭"></button>' +
            '</div>' +
            '<div class="modal-body dashboard-customize__body">' +
            '<ul class="dashboard-customize__list" id="dashboardCustomizeList"></ul>' +
            '</div>' +
            '<div class="modal-footer dashboard-customize__footer">' +
            '<button type="button" class="btn btn-outline-secondary btn-sm dashboard-customize__reset" id="dashboardCustomizeReset">恢复默认布局</button>' +
            '<button type="button" class="btn btn-primary btn-sm dashboard-customize__done" data-bs-dismiss="modal">完成</button>' +
            '</div>' +
            '</div></div>'
        document.body.appendChild(modalEl)

        modalEl.addEventListener('change', onModalChange)
        modalEl.addEventListener('click', onModalClick)
        return modalEl
    }

    var GROUP_LABELS = {
        stats: '统计',
        llm: 'LLM 用量',
        activity: '活动'
    }

    // LLM 未配置时，LLM 卡片仍然列出，但开关先禁掉并给出提示
    function isLocked(el) {
        var group = el.getAttribute('data-card-group') || 'stats'
        var target = getGrid()
        if (group !== 'llm') return false
        return !target || target.getAttribute('data-llm-available') === '0'
    }

    function buildRow(el) {
        var id = cardId(el)
        var title = el.getAttribute('data-card-title') || id
        var description = el.getAttribute('data-card-description') || ''
        var group = el.getAttribute('data-card-group') || 'stats'
        var visible = state.hidden.indexOf(id) === -1
        var locked = isLocked(el)

        return {
            group: GROUP_LABELS[group] || group,
            html:
                '<li class="dashboard-customize__row' +
                (visible ? '' : ' dashboard-customize__row--off') +
                (locked ? ' dashboard-customize__row--locked' : '') +
                '" data-card-id="' +
                escapeHtml(id) +
                '">' +
                '<div class="dashboard-customize__info">' +
                '<div class="dashboard-customize__name">' +
                escapeHtml(title) +
                '</div>' +
                (locked
                    ? '<div class="dashboard-customize__desc">未配置 LLM，先在配置页填写 API Key 后可用。</div>'
                    : description
                      ? '<div class="dashboard-customize__desc">' +
                        escapeHtml(description) +
                        '</div>'
                      : '') +
                '</div>' +
                '<div class="dashboard-customize__actions">' +
                '<div class="form-check form-switch dashboard-customize__switch mb-0">' +
                '<input class="form-check-input" type="checkbox" role="switch" data-role="visible"' +
                (visible ? ' checked' : '') +
                (locked ? ' disabled' : '') +
                ' aria-label="显示' +
                escapeHtml(title) +
                '">' +
                '</div>' +
                '</div></li>'
        }
    }

    function renderList() {
        if (!modalEl) return
        var list = modalEl.querySelector('#dashboardCustomizeList')
        if (!list) return

        var html = ''
        var lastGroup = ''
        getItems().forEach(function (el) {
            var row = buildRow(el)
            if (row.group !== lastGroup) {
                lastGroup = row.group
                html +=
                    '<li class="dashboard-customize__group">' +
                    escapeHtml(row.group) +
                    '</li>'
            }
            html += row.html
        })
        list.innerHTML = html
    }

    function setVisible(id, visible) {
        var hidden = state.hidden.filter(function (item) {
            return item !== id
        })
        if (!visible) {
            hidden.push(id)
        } else if (!state.layout[id]) {
            state.layout[id] = { x: 0, y: maxBottom(), w: 3, h: FALLBACK_HEIGHT }
        }
        state.hidden = hidden

        if (visible) {
            var gap = metrics().gap
            var el = getItems().filter(function (item) {
                return cardId(item) === id
            })[0]
            var item = state.layout[id]
            if (item) {
                if (el) {
                    if (!item.w) item.w = numberAttr(el, 'data-card-width', 3)
                    if (!item.h) {
                        item.h = numberAttr(el, 'data-card-height', FALLBACK_HEIGHT)
                    }
                }
                var slot = firstFit(item.w, item.h, gap, id)
                item.x = slot.x
                item.y = slot.y
            }
            pushDown(id, gap)
            compact(gap)
        }

        persist()
        renderList()
    }

    function onModalChange(event) {
        var input = event.target.closest('input[data-role="visible"]')
        if (!input) return
        var row = input.closest('[data-card-id]')
        if (!row) return
        setVisible(row.getAttribute('data-card-id'), input.checked)
    }

    function onModalClick(event) {
        if (event.target.closest('#dashboardCustomizeReset')) resetAll()
    }

    function resetAll() {
        try {
            window.localStorage.removeItem(STORAGE_KEY)
        } catch (e) {
            // 忽略：删不掉时内存状态也会回到默认
        }
        state = cloneDefaults()
        apply()
        renderList()
    }

    function openModal() {
        ensureModal()
        renderList()

        if (window.bootstrap && window.bootstrap.Modal) {
            if (!modalInstance) modalInstance = new window.bootstrap.Modal(modalEl)
            modalInstance.show()
        }
    }

    // ========== 初始化 ==========

    function onResize() {
        if (resizeTimer) window.clearTimeout(resizeTimer)
        resizeTimer = window.setTimeout(function () {
            resizeTimer = null
            apply()
        }, 160)
    }

    function init() {
        if (!getGrid()) return
        captureDefaults()

        var stored = readState()
        state = normalize(stored || cloneDefaults())
        compact(metrics().gap)
        apply()

        var btn = document.getElementById('dashboardCustomizeBtn')
        if (btn) btn.addEventListener('click', openModal)

        getGrid().addEventListener('pointerdown', onPointerDown)
        window.addEventListener('resize', onResize)

        readyDispatched = true
        // 同一份 API 既挂到 window（供先执行的脚本/插件直接调用），
        // 也通过事件分发给后执行的脚本
        var api = {
            layout: snapshot(),
            reset: resetAll,
            setVisible: setVisible,
            relayout: apply
        }
        window.dashboardCards = api
        document.dispatchEvent(
            new CustomEvent('dashboard:cards-ready', { detail: api })
        )
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init)
    } else {
        init()
    }
})()
