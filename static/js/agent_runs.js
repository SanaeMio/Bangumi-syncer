/**
 * /agent-runs 运行观测页：列表（过滤 / 分页 / 刷新）+ 详情弹窗。
 * 步骤渲染复用 agent-run-steps.js（loadAgentRunSteps / renderAgentRunSteps）。
 */

var _agentRunsPage = 1;
var _agentRunsPageSize = 20;

function _agentRunsFilterParams() {
    var params = new URLSearchParams({
        page: _agentRunsPage,
        page_size: _agentRunsPageSize,
        days: (document.getElementById('filter-days') || {}).value || '30',
    });
    var status = (document.getElementById('filter-status') || {}).value || '';
    var taskType = (document.getElementById('filter-task-type') || {}).value || '';
    if (status) params.set('status', status);
    if (taskType) params.set('task_type', taskType);
    return params;
}

function _agentRunStatusPill(status) {
    var map = {
        pending: ['bg-secondary', '待处理'],
        processing: ['bg-warning', '执行中'],
        succeeded: ['bg-success', '成功'],
        failed: ['bg-danger', '失败'],
        cancelled: ['bg-dark', '已取消'],
    };
    var entry = map[status];
    if (!entry) {
        return '<span class="badge bg-light text-dark">' + escapeHtml(String(status || '未知')) + '</span>';
    }
    return '<span class="badge ' + entry[0] + '">' + entry[1] + '</span>';
}

function _agentRunDurationLabel(startedAt, endedAt) {
    if (!startedAt || !endedAt) return '—';
    var start = Date.parse(startedAt);
    var end = Date.parse(endedAt);
    if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—';
    return ((end - start) / 1000).toFixed(1) + 's';
}

/** 结束原因 → 中文短标签（列表与详情共用；未知值原样展示）。 */
function _agentRunStopReasonLabel(reason) {
    var map = {
        submit_suggestion: '提交建议',
        end_turn: '正常结束',
        exhausted: '预算耗尽',
        give_up: '明确放弃',
        llm_error: 'LLM 错误',
        max_tokens: '输出超限',
        persist_error: '落库失败',
        user_resolved: '用户已处理',
        error: '错误',
    };
    return map[reason] || reason || '';
}

/** 长文本截断（展示用；完整值保留在 title/展开后的内容里）。 */
function _agentRunErrorPreview(text, limit) {
    var s = text ? String(text) : '';
    var max = limit || 200;
    return s.length > max ? s.slice(0, max) + '…' : s;
}

function _agentRunRowHtml(run) {
    var err = run.last_error ? String(run.last_error) : '';
    var reason = _agentRunStopReasonLabel(run.stop_reason);
    var tail = [reason, err].filter(Boolean).join(' · ');
    return (
        '<tr style="cursor:pointer" data-run-id="' + escapeHtml(String(run.run_id || '')) +
        '" onclick="openAgentRunDetail(this.dataset.runId)">' +
        '<td>' + _agentRunStatusPill(run.status) + '</td>' +
        '<td class="small">' + escapeHtml(String(run.task_type || '')) + '</td>' +
        '<td class="small text-nowrap">' + escapeHtml(String(run.created_at || '')) + '</td>' +
        '<td class="small text-nowrap">' + _agentRunDurationLabel(run.started_at, run.ended_at) + '</td>' +
        '<td class="small">' + (Number(run.attempts) || 0) + '</td>' +
        '<td class="small">' + (Number(run.total_tokens) || 0) + '</td>' +
        '<td class="small text-truncate" style="max-width: 280px" title="' + escapeHtml(err) + '">' +
        escapeHtml(tail.slice(0, 120)) + '</td>' +
        '</tr>'
    );
}

async function loadAgentRuns(page) {
    if (page) _agentRunsPage = Math.max(1, Number(page) || 1);
    var tbody = document.querySelector('#agent-runs-table tbody');
    if (!tbody) return;
    setAppTableLoading(true, 'agent-runs-table-wrap');
    try {
        var data = await apiFetch('/api/agent/runs?' + _agentRunsFilterParams().toString());
        var items = data && Array.isArray(data.items) ? data.items : [];
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">暂无记录</td></tr>';
        } else {
            tbody.innerHTML = items.map(_agentRunRowHtml).join('');
            replayAppTableAnimation('agent-runs-table-wrap');
        }
        renderAppPagination({
            total: Number(data && data.total) || 0,
            currentPage: Number(data && data.page) || 1,
            limit: Number(data && data.page_size) || _agentRunsPageSize,
            onPageChange: loadAgentRuns,
        });
    } catch (error) {
        console.error('加载运行列表失败:', error);
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-danger py-3">加载失败</td></tr>';
    } finally {
        setAppTableLoading(false, 'agent-runs-table-wrap');
    }
}

var _agentRunDetailCurrent = null;

/** 运行元数据卡片（状态/原因/尝试/Tokens/业务键/时间 + last_error 展开/复制）。 */
function renderAgentRunMeta(run) {
    run = run || {};
    var err = run.last_error ? String(run.last_error) : '';
    var reason = _agentRunStopReasonLabel(run.stop_reason) || '—';
    var cells = [
        ['状态', _agentRunStatusPill(run.status)],
        ['任务', escapeHtml(String(run.task_type || '—'))],
        ['结束原因', escapeHtml(reason)],
        ['尝试', (Number(run.attempts) || 0) + ' / ' + (Number(run.total_attempts) || 0)],
        ['Tokens', String(Number(run.total_tokens) || 0)],
        ['关联记录', run.sync_record_id == null ? '—' : escapeHtml(String(run.sync_record_id))],
        ['业务键', run.business_key ? '<code>' + escapeHtml(String(run.business_key)) + '</code>' : '—'],
        ['创建', escapeHtml(String(run.created_at || '—'))],
        ['开始', escapeHtml(String(run.started_at || '—'))],
        ['结束', escapeHtml(String(run.ended_at || '—'))],
    ];
    var html = '<div class="border rounded p-2 mb-2 small"><div class="row g-2">';
    cells.forEach(function (kv) {
        html += '<div class="col-md-4"><span class="text-muted">' + kv[0] + '</span> ' + kv[1] + '</div>';
    });
    html += '</div>';
    if (err) {
        html += '<div class="mt-2">'
            + '<div class="d-flex align-items-center gap-2">'
            + '<span class="text-muted">最近错误</span>'
            + '<button type="button" class="btn btn-link btn-sm p-0" id="agent-run-error-toggle" onclick="toggleAgentRunError()">展开</button>'
            + '<button type="button" class="btn btn-link btn-sm p-0" onclick="copyAgentRunError()">复制</button>'
            + '</div>'
            + '<div id="agent-run-meta-error" class="text-danger text-break" style="white-space:pre-wrap">'
            + escapeHtml(_agentRunErrorPreview(err)) + '</div>'
            + '</div>';
    }
    html += '</div>';
    return html;
}

/** last_error 展开 / 收起（全文已在 run 元数据中，无需额外请求）。 */
function toggleAgentRunError() {
    var run = _agentRunDetailCurrent || {};
    var el = document.getElementById('agent-run-meta-error');
    var btn = document.getElementById('agent-run-error-toggle');
    if (!el) return;
    var full = String(run.last_error || '');
    var expanded = el.dataset.expanded === '1';
    if (expanded) {
        el.textContent = _agentRunErrorPreview(full);
        el.dataset.expanded = '0';
        if (btn) btn.textContent = '展开';
    } else {
        el.textContent = full;
        el.dataset.expanded = '1';
        if (btn) btn.textContent = '收起';
    }
}

function copyAgentRunError() {
    var run = _agentRunDetailCurrent || {};
    var text = String(run.last_error || '');
    if (!text) {
        showAlert('没有可复制的错误信息', 'warning', 2500);
        return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
            showAlert('错误信息已复制', 'success', 2000);
        }).catch(function () {
            showAlert('复制失败，请手动选择文本', 'warning', 2500);
        });
    } else {
        showAlert('当前浏览器不支持剪贴板', 'warning', 2500);
    }
}

async function openAgentRunDetail(runId) {
    if (!runId) return;
    var label = document.getElementById('agent-run-detail-id');
    if (label) label.textContent = runId;
    var content = document.getElementById('agent-run-detail-content');
    if (content) {
        content.dataset.loaded = '';
        content.innerHTML = '<div id="agent-run-detail-meta"><span class="text-muted">加载中…</span></div>'
            + '<div id="agent-run-detail-steps"></div>';
    }
    showModal('agentRunDetailModal');

    try {
        var run = await apiFetch('/api/agent/runs/' + encodeURIComponent(runId));
        _agentRunDetailCurrent = run;
        var metaEl = document.getElementById('agent-run-detail-meta');
        if (metaEl) metaEl.innerHTML = renderAgentRunMeta(run);
    } catch (error) {
        var metaEl = document.getElementById('agent-run-detail-meta');
        if (metaEl) {
            metaEl.innerHTML = '<div class="text-danger small">加载运行信息失败：'
                + escapeHtml(error && error.message ? error.message : '') + '</div>';
        }
    }
    loadAgentRunSteps(runId, 'agent-run-detail-steps', { force: true });
}

function clearAgentRunFilters() {
    ['filter-status', 'filter-task-type'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.value = '';
    });
    var days = document.getElementById('filter-days');
    if (days) days.value = '30';
    loadAgentRuns(1);
}

window.loadAgentRuns = loadAgentRuns;
window.openAgentRunDetail = openAgentRunDetail;
window.clearAgentRunFilters = clearAgentRunFilters;
window.toggleAgentRunError = toggleAgentRunError;
window.copyAgentRunError = copyAgentRunError;
