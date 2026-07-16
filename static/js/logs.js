/**
 * 日志管理页：分组视图、筛选、自动刷新、一键复制
 */
(function () {
    'use strict';

    const DEFAULT_EXPAND_RECENT = 3;
    const COPY_FEEDBACK_MS = 2000;

    let autoRefreshInterval = null;
    let clearLogsModal = null;
    let lastModified = null;
    let lastGrouped = null;
    /** @type {Record<string, boolean>} run_id -> true 表示折叠 */
    let groupCollapseState = {};

    document.addEventListener('DOMContentLoaded', function () {
        const modalEl = document.getElementById('clearLogsModal');
        if (modalEl) {
            clearLogsModal = new bootstrap.Modal(modalEl);
        }
        loadLogs();
        startAutoRefresh();

        document.getElementById('auto-refresh').addEventListener('change', function () {
            if (this.checked) {
                startAutoRefresh();
            } else {
                stopAutoRefresh();
            }
        });

        document.getElementById('grouped-view').addEventListener('change', function () {
            lastModified = null;
            lastGrouped = null;
            groupCollapseState = {};
            loadLogs();
        });
    });

    function startAutoRefresh() {
        stopAutoRefresh();
        autoRefreshInterval = setInterval(loadLogs, 5000);
    }

    function stopAutoRefresh() {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
    }

    function getFilterParams() {
        const level = document.getElementById('log-level').value;
        const search = document.getElementById('log-search').value;
        const limit = document.getElementById('log-lines-limit').value;
        const grouped = document.getElementById('grouped-view').checked;
        const params = new URLSearchParams();
        if (level) params.set('level', level);
        if (search) params.set('search', search);
        if (limit) params.set('limit', limit);
        if (grouped) params.set('grouped', 'true');
        return params;
    }

    window.loadLogs = async function loadLogs() {
        try {
            const params = getFilterParams();
            const response = await fetch(appUrl(`/api/logs?${params}`), {
                credentials: 'include',
            });
            const data = await response.json();

            if (data.status !== 'success') {
                showAlert('加载日志失败: ' + (data.message || ''), 'danger');
                return;
            }

            const stats = data.data.stats;
            const grouped = document.getElementById('grouped-view').checked;
            if (
                lastModified !== null
                && stats.modified === lastModified
                && lastGrouped === grouped
            ) {
                return;
            }
            lastModified = stats.modified;
            lastGrouped = grouped;

            displayLogStats(stats);
            updateDebugHint(data.data.debug_mode);

            if (grouped) {
                displayGroupedLogs(data.data.groups || [], data.data.orphans || [], data.data.debug_mode);
            } else {
                displayLogContent(data.data.content || '');
            }
        } catch (error) {
            console.error('加载日志失败:', error);
            showAlert('加载日志失败', 'danger');
        }
    };

    window.applyLogFilter = function applyLogFilter() {
        lastModified = null;
        lastGrouped = null;
        groupCollapseState = {};
        loadLogs();
    };

    function displayLogStats(stats) {
        document.getElementById('log-size').textContent = formatFileSize(stats.size);
        document.getElementById('log-lines').textContent = stats.lines.toLocaleString();
        if (stats.modified) {
            const modDate = new Date(stats.modified);
            document.getElementById('log-modified').textContent =
                (modDate.getMonth() + 1) + '/' + modDate.getDate() + ' ' +
                modDate.toTimeString().slice(0, 5);
        } else {
            document.getElementById('log-modified').textContent = '-';
        }
        document.getElementById('log-errors').textContent = stats.errors.toLocaleString();
    }

    function updateDebugHint(debugMode) {
        const hint = document.getElementById('debug-mode-hint');
        if (!hint) return;
        hint.classList.toggle('d-none', !debugMode);
    }

    function displayLogContent(content) {
        const logContent = document.getElementById('log-content');
        logContent.innerHTML = '';
        logContent.classList.remove('log-viewer--grouped');

        if (!content || content.length === 0) {
            logContent.innerHTML = '<div class="text-center p-4 text-muted">暂无日志内容</div>';
            return;
        }

        const pre = document.createElement('pre');
        pre.className = 'log-text';
        pre.textContent = content;
        logContent.appendChild(pre);
        logContent.scrollTop = logContent.scrollHeight;
    }

    function groupKey(group) {
        return group.run_id || ('group_' + formatGroupTitle(group));
    }

    function linesSignature(lines) {
        if (!lines || lines.length === 0) {
            return '0';
        }
        return String(lines.length) + ':' + lines[lines.length - 1];
    }

    function shouldStartCollapsed(group, index, total) {
        const key = groupKey(group);
        if (Object.prototype.hasOwnProperty.call(groupCollapseState, key)) {
            return groupCollapseState[key];
        }
        if (group.status === 'error') {
            return false;
        }
        return index < total - DEFAULT_EXPAND_RECENT;
    }

    function getScrollContainer() {
        const container = document.getElementById('log-content');
        return container.querySelector('.log-groups-scroll') || container;
    }

    function isNearBottom(el, threshold) {
        threshold = threshold || 48;
        return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    }

    function displayGroupedLogs(groups, orphans, debugMode) {
        const container = document.getElementById('log-content');
        container.classList.add('log-viewer--grouped');

        if (groups.length === 0 && orphans.length === 0) {
            container.innerHTML = '<div class="text-center p-4 text-muted">暂无日志内容</div>';
            return;
        }

        const scrollEl = getScrollContainer();
        const stickToBottom = scrollEl ? isNearBottom(scrollEl) : false;
        const scrollTop = scrollEl ? scrollEl.scrollTop : 0;

        let wrapper = container.querySelector('.log-groups');
        if (!wrapper) {
            wrapper = createLogGroupsShell();
            container.innerHTML = '';
            container.appendChild(wrapper);
        }

        syncGroupedLogs(wrapper, groups, orphans, debugMode);

        const nextScrollEl = getScrollContainer();
        if (nextScrollEl) {
            if (stickToBottom) {
                nextScrollEl.scrollTop = nextScrollEl.scrollHeight;
            } else {
                nextScrollEl.scrollTop = scrollTop;
            }
        }
    }

    function createLogGroupsShell() {
        const wrapper = document.createElement('div');
        wrapper.className = 'log-groups';
        wrapper.innerHTML =
            '<div class="log-groups-scroll"></div>' +
            '<div class="log-orphans-footer" hidden></div>';
        return wrapper;
    }

    function syncGroupedLogs(wrapper, groups, orphans, debugMode) {
        const listEl = wrapper.querySelector('.log-groups-scroll');
        const visibleGroups = groups.slice(-50);
        const existingCards = new Map();

        listEl.querySelectorAll('.log-group-card').forEach(function (card) {
            existingCards.set(card.dataset.runId, card);
        });

        const orderedCards = [];
        visibleGroups.forEach(function (group, index) {
            const key = groupKey(group);
            let card = existingCards.get(key);
            if (card) {
                updateGroupCard(card, group, debugMode);
                existingCards.delete(key);
            } else {
                card = buildGroupCard(group, debugMode, index, visibleGroups.length);
            }
            orderedCards.push(card);
        });

        existingCards.forEach(function (card) {
            const key = card.dataset.runId;
            if (key) {
                delete groupCollapseState[key];
            }
            card.remove();
        });

        let moreHint = listEl.querySelector('.log-groups-more-hint');
        if (groups.length > 50) {
            if (!moreHint) {
                moreHint = document.createElement('div');
                moreHint.className = 'log-groups-more-hint';
                listEl.appendChild(moreHint);
            }
            moreHint.textContent = '仅显示最新 50 组，共 ' + groups.length + ' 组';
        } else if (moreHint) {
            moreHint.remove();
        }

        orderedCards.forEach(function (card) {
            listEl.appendChild(card);
        });
        if (moreHint) {
            listEl.appendChild(moreHint);
        }

        syncOrphansFooter(wrapper, orphans);
    }

    function syncOrphansFooter(wrapper, orphans) {
        const footer = wrapper.querySelector('.log-orphans-footer');
        if (!footer) {
            return;
        }
        if (!orphans.length) {
            footer.hidden = true;
            footer.innerHTML = '';
            return;
        }

        footer.hidden = false;
        const detailsEl = footer.querySelector('details');
        const wasOpen = detailsEl ? detailsEl.open : false;
        footer.innerHTML = '';
        footer.appendChild(buildOrphansSection(orphans));
        const details = footer.querySelector('details');
        if (details && wasOpen) {
            details.open = true;
        }
    }

    function statusLabel(status) {
        switch (status) {
            case 'success': return '成功';
            case 'error': return '失败';
            case 'ignored': return '忽略';
            default: return '未知';
        }
    }

    function statusBadgeClass(status) {
        switch (status) {
            case 'success': return 'bg-success';
            case 'error': return 'bg-danger';
            case 'ignored': return 'bg-secondary';
            default: return 'bg-warning';
        }
    }

    function formatGroupTitle(group) {
        const name = (group.title || '').trim();
        const se = (group.season && group.episode)
            ? 'S' + String(group.season).padStart(2, '0') + 'E' + String(group.episode).padStart(2, '0')
            : '';
        if (name && se) {
            return name + ' ' + se;
        }
        if (name) {
            return name;
        }
        if (se) {
            return se;
        }
        return '未识别同步';
    }

    function formatDuration(durationMs) {
        if (durationMs == null || durationMs === undefined) {
            return '';
        }
        if (durationMs < 1000) {
            return durationMs + 'ms';
        }
        const sec = durationMs / 1000;
        if (sec < 60) {
            return (Number.isInteger(sec) ? String(sec) : sec.toFixed(1)) + 's';
        }
        return Math.round(sec) + 's';
    }

    function detectLineLevel(line) {
        if (line.includes('[ERROR]')) return 'ERROR';
        if (line.includes('[WARNING]') || line.includes('[WARN ]') || line.includes('[WARN]')) {
            return 'WARNING';
        }
        if (line.includes('[DEBUG]')) return 'DEBUG';
        if (line.includes('[INFO]')) return 'INFO';
        return 'INFO';
    }

    function stripRunTagFromLine(line) {
        return line.replace(/ \[run:[^\]]+\]/, '');
    }

    function renderGroupBadges(group) {
        let html = '';
        if (group.source) {
            html += '<span class="badge rounded-pill bg-info">' + escapeHtml(group.source) + '</span>';
        }
        html += '<span class="badge rounded-pill ' + statusBadgeClass(group.status) + '">' +
            statusLabel(group.status) + '</span>';
        if (group.ambiguous) {
            html += '<span class="badge rounded-pill bg-warning" title="并发交叉或历史日志，分组可能不完整">可能不完整</span>';
        }
        return html;
    }

    function renderGroupActions(group) {
        const durationText = formatDuration(group.duration_ms);
        return (durationText
            ? '<span class="log-group-meta"><i class="bi bi-hourglass-split"></i>' + escapeHtml(durationText) + '</span>'
            : '') +
            '<span class="log-group-meta"><i class="bi bi-list-ul"></i>' + group.line_count + ' 行</span>' +
            '<button type="button" class="log-group-btn log-group-copy" title="复制本组日志">' +
            '<i class="bi bi-clipboard"></i><span class="log-group-copy-label">复制</span></button>' +
            '<button type="button" class="log-group-btn log-group-toggle" title="折叠/展开" aria-expanded="true">' +
            '<i class="bi bi-chevron-down"></i></button>';
    }

    function fillGroupBody(body, lines) {
        body.innerHTML = '';
        (lines || []).forEach(function (line, index) {
            const row = document.createElement('div');
            const level = detectLineLevel(line);
            row.className = 'log-line log-line--' + level.toLowerCase();
            if (index % 2 === 1) {
                row.classList.add('log-line--alt');
            }
            row.textContent = stripRunTagFromLine(line);
            body.appendChild(row);
        });
    }

    function syncDebugHint(card, group, debugMode) {
        let hint = card.querySelector('.log-group-debug-hint');
        const show = debugMode && group.level_counts && group.level_counts.DEBUG > 0;
        if (!show) {
            if (hint) {
                hint.remove();
            }
            return;
        }
        if (!hint) {
            hint = document.createElement('div');
            hint.className = 'log-group-debug-hint';
            const body = card.querySelector('.log-group-body');
            card.insertBefore(hint, body);
        }
        hint.innerHTML = '<i class="bi bi-bug"></i> 含 ' + group.level_counts.DEBUG + ' 条 DEBUG';
    }

    function bindGroupCardEvents(card) {
        if (card.dataset.eventsBound === '1') {
            return;
        }
        card.dataset.eventsBound = '1';

        card.addEventListener('click', function (e) {
            const copyBtn = e.target.closest('.log-group-copy');
            if (copyBtn) {
                e.stopPropagation();
                if (card._logGroup) {
                    copyGroupLines(card._logGroup, copyBtn);
                }
                return;
            }

            const toggleBtn = e.target.closest('.log-group-toggle');
            const header = e.target.closest('.log-group-header');
            if (!toggleBtn && !header) {
                return;
            }

            e.stopPropagation();
            toggleGroupBody(
                card,
                card.querySelector('.log-group-body'),
                card.querySelector('.log-group-toggle'),
            );
        });
    }

    function applyCardCollapsed(card, body, toggleBtn, collapsed) {
        body.classList.toggle('collapsed', collapsed);
        card.classList.toggle('is-collapsed', collapsed);
        if (toggleBtn) {
            toggleBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        }
    }

    function buildGroupCard(group, debugMode, index, total) {
        const card = document.createElement('div');
        card.className = 'log-group-card';
        card.dataset.runId = groupKey(group);
        card.dataset.status = group.status || 'unknown';
        card.dataset.linesSig = linesSignature(group.lines);

        const title = formatGroupTitle(group);

        const header = document.createElement('div');
        header.className = 'log-group-header';

        const main = document.createElement('div');
        main.className = 'log-group-header-main';
        main.innerHTML =
            '<span class="log-group-status-dot" aria-hidden="true"></span>' +
            '<div class="log-group-title-row">' +
            '<div class="log-group-title">' + escapeHtml(title) + '</div>' +
            '<div class="log-group-badges">' + renderGroupBadges(group) + '</div>' +
            '</div>';

        const actions = document.createElement('div');
        actions.className = 'log-group-header-actions';
        actions.innerHTML = renderGroupActions(group);

        header.appendChild(main);
        header.appendChild(actions);

        const body = document.createElement('div');
        body.className = 'log-group-body';
        fillGroupBody(body, group.lines);

        card.appendChild(header);
        syncDebugHint(card, group, debugMode);
        card.appendChild(body);

        bindGroupCardEvents(card);
        card._logGroup = group;

        const collapsed = shouldStartCollapsed(group, index, total);
        applyCardCollapsed(card, body, header.querySelector('.log-group-toggle'), collapsed);
        if (collapsed) {
            groupCollapseState[groupKey(group)] = true;
        }

        return card;
    }

    function updateGroupCard(card, group, debugMode) {
        const key = groupKey(group);
        card.dataset.runId = key;
        card.dataset.status = group.status || 'unknown';

        const title = formatGroupTitle(group);
        card.querySelector('.log-group-title').textContent = title;
        card.querySelector('.log-group-badges').innerHTML = renderGroupBadges(group);
        card.querySelector('.log-group-header-actions').innerHTML = renderGroupActions(group);

        const sig = linesSignature(group.lines);
        if (card.dataset.linesSig !== sig) {
            card.dataset.linesSig = sig;
            fillGroupBody(card.querySelector('.log-group-body'), group.lines);
        }

        syncDebugHint(card, group, debugMode);
        bindGroupCardEvents(card);
        card._logGroup = group;
    }

    function toggleGroupBody(card, body, toggleBtn) {
        const collapsed = !body.classList.contains('collapsed');
        applyCardCollapsed(card, body, toggleBtn, collapsed);
        const key = card.dataset.runId;
        if (key) {
            groupCollapseState[key] = collapsed;
        }
    }

    function buildOrphansSection(orphans) {
        const section = document.createElement('details');
        section.className = 'log-orphans-section';
        const summary = document.createElement('summary');
        summary.innerHTML =
            '<i class="bi bi-gear-wide-connected me-1"></i>其他系统日志 (' + orphans.length + ')' +
            '<span class="log-orphans-hint">登录、启动等，与同步无关</span>';
        section.appendChild(summary);

        const body = document.createElement('div');
        body.className = 'log-orphans-body';
        orphans.forEach(function (line) {
            const row = document.createElement('div');
            row.className = 'log-line log-line--info';
            row.textContent = line;
            body.appendChild(row);
        });
        section.appendChild(body);
        return section;
    }

    function copyGroupLines(group, btn) {
        const text = (group.lines || []).map(stripRunTagFromLine).join('\n');
        navigator.clipboard.writeText(text).then(function () {
            flashCopyButton(btn);
        }).catch(function () {
            showAlert('复制失败', 'danger');
        });
    }

    function flashCopyButton(btn) {
        if (!btn || btn.classList.contains('log-group-copy--done')) {
            return;
        }
        const icon = btn.querySelector('.bi');
        const label = btn.querySelector('.log-group-copy-label');
        const origIconClass = icon ? icon.className : '';
        if (icon) {
            icon.className = 'bi bi-check2';
        }
        if (label) {
            label.textContent = '已复制';
        }
        btn.classList.add('log-group-copy--done');
        window.setTimeout(function () {
            if (icon) {
                icon.className = origIconClass;
            }
            if (label) {
                label.textContent = '复制';
            }
            btn.classList.remove('log-group-copy--done');
        }, COPY_FEEDBACK_MS);
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    window.clearLogs = function clearLogs() {
        clearLogsModal.show();
    };

    window.confirmClearLogs = async function confirmClearLogs() {
        const createBackup = document.getElementById('backup-before-clear').checked;

        try {
            const response = await fetch(appUrl('/api/logs/clear'), {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ backup: createBackup }),
            });
            const data = await response.json();

            if (data.status === 'success') {
                clearLogsModal.hide();
                showAlert('日志已清空', 'success');
                lastModified = null;
                lastGrouped = null;
                groupCollapseState = {};
                loadLogs();
            } else {
                showAlert('清空日志失败: ' + data.message, 'danger');
            }
        } catch (error) {
            console.error('清空日志失败:', error);
            showAlert('清空日志失败', 'danger');
        }
    };

    function formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function showAlert(message, type) {
        const toastContainer = document.querySelector('.toast-container');
        if (!toastContainer) return;
        const toast = document.createElement('div');
        toast.className = 'toast align-items-center text-white bg-' + type + ' border-0';
        toast.setAttribute('role', 'alert');
        toast.innerHTML =
            '<div class="d-flex">' +
            '<div class="toast-body">' + escapeHtml(message) + '</div>' +
            '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>' +
            '</div>';
        toastContainer.appendChild(toast);
        const bsToast = new bootstrap.Toast(toast);
        bsToast.show();
        toast.addEventListener('hidden.bs.toast', function () {
            toast.remove();
        });
    }
})();
