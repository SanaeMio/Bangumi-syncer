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

function _agentRunRowHtml(run) {
    var err = run.last_error ? String(run.last_error) : '';
    var tail = [run.stop_reason || '', err].filter(Boolean).join(' · ');
    return (
        '<tr style="cursor:pointer" data-run-id="' + escapeHtml(String(run.run_id || '')) +
        '" onclick="openAgentRunDetail(this.dataset.runId)">' +
        '<td>' + _agentRunStatusPill(run.status) + '</td>' +
        '<td class="small">' + escapeHtml(String(run.task_type || '')) + '</td>' +
        '<td class="small text-nowrap">' + escapeHtml(String(run.created_at || '')) + '</td>' +
        '<td class="small text-nowrap">' + _agentRunDurationLabel(run.started_at, run.ended_at) + '</td>' +
        '<td class="small">' + (Number(run.attempts) || 0) + '</td>' +
        '<td class="small">' + (Number(run.total_tokens) || 0) + '</td>' +
        '<td class="small text-truncate" style="max-width: 280px" title="' + escapeHtml(tail) + '">' +
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

function openAgentRunDetail(runId) {
    if (!runId) return;
    var label = document.getElementById('agent-run-detail-id');
    if (label) label.textContent = runId;
    var content = document.getElementById('agent-run-detail-content');
    if (content) {
        content.dataset.loaded = '';
        content.innerHTML = '<span class="text-muted">加载中…</span>';
    }
    showModal('agentRunDetailModal');
    loadAgentRunSteps(runId, 'agent-run-detail-content', { force: true });
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
