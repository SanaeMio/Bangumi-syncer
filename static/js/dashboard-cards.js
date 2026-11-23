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
 * 拖动换位：主屏幕插入（指针落在哪张卡上就插入到哪，行满换行）。
 * 拉伸：只改当前这张，松手后再把被挡住的卡往下推。
 */
(function () {
    'use strict'

    var STORAGE_KEY = 'app-dashboard-cards'
    var STORAGE_VERSION = 2
    var FALLBACK_COLUMNS = 12
    var FALLBACK_GAP = 24
    var FALLBACK_HEIGHT = 128
    var LEGACY_DEFAULT_HEIGHT = 106
    var FALLBACK_MIN_HEIGHT = 96
    var HEIGHT_STEP = 8
    var MAX_HEIGHT = 1600
    var LEGACY_HEIGHT = {
        'sync-timeline': [780, 736],
        'sync-heatmap': [280],
        'sync-daily-trend': [556, 432],
        'airing-calendar': [432],
        'scheduler-status': [432],
        'llm-trend': [432]
    }
    // 与 CSS max-width: 991.98px 对齐：整数视口宽 ≤991 流式栅格，禁止拖 / 拉
    var MOBILE_MAX_WIDTH = 991
    var DRAG_THRESHOLD = 3

    var grid = null
    var placeholder = null
    var modalEl = null
    var modalInstance = null
    var state = null
    var defaults = null
    var dragState = null
    var resizeTimer = null
    var fitTimer = null
    var readyDispatched = false
    var editing = false

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

    function pointerOnGrid(event) {
        var rect = getGrid().getBoundingClientRect()
        var stride = metrics().stride
        return {
            col: (event.clientX - rect.left) / stride,
            y: event.clientY - rect.top
        }
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
            var registeredH = numberAttr(el, 'data-card-height', FALLBACK_HEIGHT)
            var h = clamp(item.h, FALLBACK_MIN_HEIGHT, MAX_HEIGHT)
            var legacyH = LEGACY_HEIGHT[id]
            // 仍是旧默认、且登记高度已改时跟着改；用户手动拉过的不改
            if (h === LEGACY_DEFAULT_HEIGHT && registeredH === FALLBACK_HEIGHT) {
                h = FALLBACK_HEIGHT
            }
            if (legacyH && legacyH.indexOf(h) !== -1) h = registeredH
            result.layout[id] = {
                x: clamp(item.x, 0, FALLBACK_COLUMNS - 1),
                y: Math.max(0, item.y),
                w: clamp(item.w, 1, FALLBACK_COLUMNS),
                h: h
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

    function cloneLayout(layout) {
        var out = {}
        Object.keys(layout).forEach(function (id) {
            var item = layout[id]
            out[id] = { x: item.x, y: item.y, w: item.w, h: item.h }
        })
        return out
    }

    function assignLayout(next) {
        Object.keys(state.layout).forEach(function (id) {
            var item = next[id]
            if (!item) return
            state.layout[id].x = item.x
            state.layout[id].y = item.y
            state.layout[id].w = item.w
            state.layout[id].h = item.h
        })
    }

    function visibleLayoutIds() {
        return Object.keys(state.layout).filter(function (id) {
            return state.hidden.indexOf(id) === -1
        })
    }

    /** 同一列、上下紧贴的卡片成一组（热力图+趋势），左右换位时一起走 */
    function columnCluster(id, layout, gap) {
        var item = layout[id]
        if (!item) return [id]
        var col = visibleLayoutIds().filter(function (otherId) {
            var other = layout[otherId]
            return other && other.x === item.x && other.w === item.w
        })
        col.sort(function (a, b) {
            return layout[a].y - layout[b].y
        })
        var index = col.indexOf(id)
        if (index < 0) return [id]
        var start = index
        var end = index
        while (start > 0) {
            var above = layout[col[start - 1]]
            var current = layout[col[start]]
            if (above.y + above.h + gap === current.y) start -= 1
            else break
        }
        while (end < col.length - 1) {
            var currentEnd = layout[col[end]]
            var below = layout[col[end + 1]]
            if (currentEnd.y + currentEnd.h + gap === below.y) end += 1
            else break
        }
        return col.slice(start, end + 1)
    }

    function clusterHeight(members, layout, gap) {
        var height = 0
        members.forEach(function (id, index) {
            height += layout[id].h
            if (index < members.length - 1) height += gap
        })
        return height
    }

    function restackCluster(members, y, gap) {
        var nextY = y
        members.forEach(function (id) {
            state.layout[id].y = nextY
            nextY += state.layout[id].h + gap
        })
    }

    function allClusters(layout, gap) {
        var seen = {}
        var clusters = []
        visibleLayoutIds().forEach(function (id) {
            if (seen[id]) return
            var members = columnCluster(id, layout, gap)
            members.forEach(function (member) {
                seen[member] = true
            })
            clusters.push({
                ids: members,
                x: layout[members[0]].x,
                w: layout[members[0]].w,
                y: layout[members[0]].y,
                h: clusterHeight(members, layout, gap)
            })
        })
        return clusters
    }

    function readingOrder(clusters) {
        return clusters.slice().sort(function (a, b) {
            return a.y - b.y || a.x - b.x
        })
    }

    /** 从左到右、行满换行，类似主屏幕图标流式排布 */
    function packClusters(order, gap, columns) {
        var x = 0
        var y = 0
        var rowH = 0
        var slots = []
        order.forEach(function (cluster) {
            var width = cluster.w
            var height = cluster.h
            if (x > 0 && x + width > columns) {
                y += rowH + gap
                x = 0
                rowH = 0
            }
            restackCluster(cluster.ids, y, gap)
            cluster.ids.forEach(function (id) {
                state.layout[id].x = x
            })
            slots.push({ cluster: cluster, x: x, y: y, w: width, h: height })
            x += width
            rowH = Math.max(rowH, height)
        })
        return slots
    }

    function measurePack(order, gap, columns) {
        var x = 0
        var y = 0
        var rowH = 0
        var slots = []
        order.forEach(function (cluster) {
            var width = cluster.w
            var height = cluster.h
            if (x > 0 && x + width > columns) {
                y += rowH + gap
                x = 0
                rowH = 0
            }
            slots.push({ cluster: cluster, x: x, y: y, w: width, h: height })
            x += width
            rowH = Math.max(rowH, height)
        })
        return slots
    }

    function slotDistance(slot, pointerCol, pointerY, stride) {
        var cx = (slot.x + slot.w / 2) * stride
        var cy = slot.y + slot.h / 2
        var dx = pointerCol * stride - cx
        var dy = pointerY - cy
        return dx * dx + dy * dy
    }

    function slotContains(slot, pointerCol, pointerY) {
        return (
            pointerCol >= slot.x &&
            pointerCol < slot.x + slot.w &&
            pointerY >= slot.y &&
            pointerY < slot.y + slot.h
        )
    }

    function orderIndexOf(order, cluster) {
        var index = -1
        order.forEach(function (item, i) {
            if (item === cluster) index = i
        })
        return index
    }

    /** 主屏幕式插入：指针还在当前空位里就钉住；否则优先落点所在空位 */
    function insertIndexFromPointer(others, dragged, pointerCol, pointerY, gap, columns) {
        var stride = metrics().stride
        var current = dragState.insertIndex
        if (current != null && current >= 0 && current <= others.length) {
            var currentTrial = others.slice()
            currentTrial.splice(current, 0, dragged)
            var currentSlot = measurePack(currentTrial, gap, columns)[current]
            if (currentSlot && slotContains(currentSlot, pointerCol, pointerY)) {
                return current
            }
        }

        var hit = null
        var hitDist = Infinity
        var best = current == null ? 0 : current
        var bestDist = Infinity
        var limit = others.length
        for (var i = 0; i <= limit; i += 1) {
            var trial = others.slice()
            trial.splice(i, 0, dragged)
            var slot = measurePack(trial, gap, columns)[i]
            var dist = slotDistance(slot, pointerCol, pointerY, stride)
            if (slotContains(slot, pointerCol, pointerY) && dist < hitDist) {
                hit = i
                hitDist = dist
            }
            if (dist < bestDist) {
                bestDist = dist
                best = i
            }
        }
        return hit != null ? hit : best
    }

    function layoutDragMove(target, gap) {
        var columns = metrics().columns
        var clusters = allClusters(state.layout, gap)
        var dragged = null
        clusters.forEach(function (cluster) {
            if (cluster.ids.indexOf(dragState.id) !== -1) dragged = cluster
        })
        if (!dragged) return

        var ordered = readingOrder(clusters)
        var others = ordered.filter(function (cluster) {
            return cluster !== dragged
        })
        if (dragState.insertIndex == null) {
            dragState.insertIndex = orderIndexOf(ordered, dragged)
        }

        var pointer = dragState.pointer
        var pointerCol = pointer ? pointer.col : target.x + dragged.w / 2
        var pointerY = pointer ? pointer.y : target.y + dragged.h / 2
        var insertAt = insertIndexFromPointer(
            others,
            dragged,
            pointerCol,
            pointerY,
            gap,
            columns
        )
        dragState.insertIndex = insertAt
        others.splice(insertAt, 0, dragged)
        packClusters(others, gap, columns)
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

    /** 默认两列时把较短的一列拉到与对侧齐底（只拉高不压缩） */
    function alignActivityColumns(gap) {
        var timeline = state.layout['sync-timeline']
        var heatmap = state.layout['sync-heatmap']
        var trend = state.layout['sync-daily-trend']
        if (!timeline || !heatmap || !trend) return false
        if (
            state.hidden.indexOf('sync-timeline') !== -1 ||
            state.hidden.indexOf('sync-heatmap') !== -1 ||
            state.hidden.indexOf('sync-daily-trend') !== -1
        ) {
            return false
        }
        if (Math.abs(timeline.y - heatmap.y) > 0.5) return false
        if (timeline.x < heatmap.x + heatmap.w && heatmap.x < timeline.x + timeline.w) {
            return false
        }
        if (trend.x !== heatmap.x || trend.w !== heatmap.w) return false
        if (Math.abs(trend.y - (heatmap.y + heatmap.h + gap)) > 0.5) return false
        var leftBottom = timeline.y + timeline.h
        var rightBottom = trend.y + trend.h
        if (Math.abs(leftBottom - rightBottom) < 0.5) return false
        if (leftBottom > rightBottom) {
            var trendH = snapHeight(leftBottom - trend.y)
            if (trendH < 280 || trendH === trend.h) return false
            trend.h = Math.min(trendH, MAX_HEIGHT)
            return true
        }
        var timelineH = snapHeight(rightBottom - timeline.y)
        if (timelineH < 280 || timelineH === timeline.h) return false
        timeline.h = Math.min(timelineH, MAX_HEIGHT)
        return true
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

    // 内容比默认高度高的卡片（最近同步、热力图）按内容长高，避免默认高度截断内容。
    // 只长不缩：数据变少时保持用户当前尺寸，重置布局即可回到默认。
    function fitCardHeight(el, item) {
        var prev = el.style.height
        el.style.height = 'auto'
        var natural = Math.ceil(el.getBoundingClientRect().height)
        el.style.height = prev
        if (!natural || natural >= 100000) return false
        if (natural <= item.h) return false
        var target = Math.ceil(natural / HEIGHT_STEP) * HEIGHT_STEP
        if (target <= item.h) return false
        item.h = Math.min(target, MAX_HEIGHT)
        return true
    }

    function fitHeights() {
        if (!state || isMobile()) return
        var gap = metrics().gap
        var changed = false

        getItems().forEach(function (el) {
            if (el.getAttribute('data-fit-height') !== '1') return
            var id = cardId(el)
            var item = state.layout[id]
            if (!item || state.hidden.indexOf(id) !== -1) return
            if (fitCardHeight(el, item)) {
                pushDown(id, gap)
                changed = true
            }
        })

        if (changed) compact(gap)
        if (alignActivityColumns(gap)) changed = true
        if (changed) apply()
    }

    function scheduleFit() {
        if (fitTimer) window.clearTimeout(fitTimer)
        fitTimer = window.setTimeout(function () {
            fitTimer = null
            fitHeights()
        }, 80)
    }

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
            baseLayout: cloneLayout(state.layout),
            minWidth: minWidthOf(el),
            minHeight: minHeightOf(el),
            insertIndex: null,
            pointer: null,
            moved: false
        }

        el.classList.add(mode === 'move' ? 'is-dragging' : 'is-resizing')
        getGrid().classList.add('is-interacting')
        if (mode === 'move') movePlaceholder(dragState.target)

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
        dragState.pointer = pointerOnGrid(event)

        if (dragState.mode === 'move') {
            target.x = origin.x + dx / m.stride
            target.y = Math.max(0, origin.y + dy)
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
        }

        previewDrag()
    }

    function previewDrag() {
        if (!dragState) return
        var gap = metrics().gap
        assignLayout(dragState.baseLayout)
        var item = state.layout[dragState.id]
        if (dragState.mode === 'move') {
            layoutDragMove(dragState.target, gap)
            item = state.layout[dragState.id]
        } else {
            item.w = dragState.target.w
            item.h = dragState.target.h
        }

        getItems().forEach(function (el) {
            var id = cardId(el)
            var next = state.layout[id]
            if (!next) return
            if (id === dragState.id && dragState.mode === 'move') return
            writeGeometry(el, next)
        })
        if (dragState.mode === 'move') {
            var members = columnCluster(dragState.id, state.layout, gap)
            var head = state.layout[members[0]]
            movePlaceholder({
                x: head.x,
                y: head.y,
                w: head.w,
                h: clusterHeight(members, state.layout, gap)
            })
        }
        var target = getGrid()
        if (target && !isMobile()) {
            target.style.setProperty('--dg-height', maxBottom() + 'px')
        }
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
            assignLayout(current.baseLayout)
            apply()
            return
        }

        if (current.mode === 'resize') {
            pushDown(current.id, metrics().gap)
        }

        persist()
    }

    function onPointerDown(event) {
        if (!editing || event.button !== 0 || isMobile()) return
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
            '<div class="dashboard-customize__hint">勾选要显示的卡片。位置和大小在首页点「自定义」后调整：拖动标题栏换位置，拖右下角改大小。偏好只保存在当前浏览器。</div>' +
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

    // ========== 编辑态：只有开启后才能拖动 / 拉伸 ==========

    function customizeButton() {
        return document.getElementById('dashboardCustomizeBtn')
    }

    function manageButton() {
        return document.getElementById('dashboardManageCardsBtn')
    }

    function setEditing(next) {
        editing = !!next
        document.body.classList.toggle('is-dashboard-editing', editing)

        var gridEl = getGrid()
        if (gridEl) gridEl.classList.toggle('is-editing', editing)

        var btn = customizeButton()
        if (btn) {
            btn.classList.toggle('is-active', editing)
            btn.setAttribute('aria-pressed', editing ? 'true' : 'false')
            btn.title = editing ? '完成布局调整' : '自定义卡片显示与布局'
            var label = btn.querySelector('.inbox-header-chip__label')
            if (label) label.textContent = editing ? '完成' : '自定义'
            var icon = btn.querySelector('.bi')
            if (icon) icon.className = editing ? 'bi bi-check-lg' : 'bi bi-sliders'
        }

        var manage = manageButton()
        if (manage) manage.classList.toggle('d-none', !editing)

        if (!editing) {
            if (dragState) {
                dragState = null
                removePlaceholder()
                getItems().forEach(function (el) {
                    el.classList.remove('is-dragging')
                })
            }
            if (gridEl) gridEl.classList.remove('is-interacting')
            persist()
        }
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
            fitHeights()
        }, 160)
    }

    function init() {
        if (!getGrid()) return
        captureDefaults()

        var stored = readState()
        state = normalize(stored || cloneDefaults())
        var gap = metrics().gap
        compact(gap)
        alignActivityColumns(gap)
        apply()

        var btn = customizeButton()
        if (btn) {
            btn.addEventListener('click', function () {
                setEditing(!editing)
            })
        }

        var manage = manageButton()
        if (manage) manage.addEventListener('click', openModal)

        document.addEventListener('keydown', function (event) {
            if (!editing) return
            if (event.key !== 'Escape') return
            if (modalEl && modalEl.classList.contains('show')) return
            setEditing(false)
        })

        getGrid().addEventListener('pointerdown', onPointerDown)
        window.addEventListener('resize', onResize)
        document.addEventListener('dashboard:content-changed', scheduleFit)
        window.requestAnimationFrame(fitHeights)

        readyDispatched = true
        // 同一份 API 既挂到 window（供先执行的脚本/插件直接调用），
        // 也通过事件分发给后执行的脚本
        var api = {
            layout: snapshot(),
            reset: resetAll,
            setVisible: setVisible,
            relayout: apply,
            setEditing: setEditing,
            isEditing: function () {
                return editing
            },
            fit: fitHeights
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
