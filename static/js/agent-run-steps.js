/**
 * Agent run 步骤时间线渲染（共用组件）
 *
 * 消费者：
 * - /agent-runs 观测页（详情弹窗，force 重载）
 * - /pending-candidates 候选弹窗「AI 评估过程」（accordion 懒加载一次）
 *
 * 数据来源：GET /api/agent/runs/{run_id}/steps
 * step 字段：span_id / name / status / model / tokens / latency_ms / tool_name /
 *            input_summary / error / iteration / sequence / started_at / ended_at /
 *            display_json（读取时现场生成：llm_chat→stop_reason/content；
 *            tool_execute→tool_use_id/content/is_error；budget→budget_message）
 */

/** 单 span 状态徽章（running / ok / error / aborted） */
function agentRunSpanStatusBadge(status) {
    var map = {
        running: ['bg-warning', '进行中'],
        ok: ['bg-success', '正常'],
        error: ['bg-danger', '失败'],
        aborted: ['bg-secondary', '中断'],
    };
    var entry = map[status];
    if (!entry) {
        return '<span class="badge bg-light text-dark">' + escapeHtml(String(status || '')) + '</span>';
    }
    return '<span class="badge ' + entry[0] + '">' + entry[1] + '</span>';
}

/** 单行渲染（llm_chat / tool_execute / budget / 未知行） */
function renderAgentRunStepRow(step) {
    var ms = Number(step.latency_ms) || 0;
    var preview = step.display_json && typeof step.display_json === 'object' ? step.display_json : {};

    if (step.name === 'llm_chat') {
        var content = preview.content ? String(preview.content) : '';
        var stop = preview.stop_reason ? escapeHtml(String(preview.stop_reason)) : '';
        return (
            '<div class="d-flex align-items-start gap-2 py-1">' +
            '<span class="badge bg-info">LLM</span>' +
            '<div class="flex-grow-1">' +
            '<div class="small">' +
            escapeHtml(String(step.model || '')) +
            ' · ' + ms + ' ms · ' + (Number(step.tokens) || 0) + ' tokens' +
            (stop ? ' · stop=' + stop : '') +
            ' ' + agentRunSpanStatusBadge(step.status) +
            '</div>' +
            (content ? '<div class="small text-muted text-break">' + escapeHtml(content.slice(0, 300)) + '</div>' : '') +
            (step.error ? '<div class="small text-danger text-break">' + escapeHtml(String(step.error)) + '</div>' : '') +
            '</div></div>'
        );
    }

    if (step.name === 'tool_execute') {
        var isError = step.status === 'error' || preview.is_error === true;
        var toolContent = preview.content ? String(preview.content) : '';
        return (
            '<div class="d-flex align-items-start gap-2 py-1">' +
            '<span class="badge ' + (isError ? 'bg-danger' : 'bg-secondary') + '">工具</span>' +
            '<div class="flex-grow-1">' +
            '<div class="small">' +
            escapeHtml(String(step.tool_name || '')) +
            ' · ' + ms + ' ms' +
            (step.input_summary ? ' · ' + escapeHtml(String(step.input_summary)) : '') +
            ' ' + agentRunSpanStatusBadge(step.status) +
            '</div>' +
            (step.error ? '<div class="small text-danger text-break">' + escapeHtml(String(step.error)) + '</div>' : '') +
            (toolContent ? '<div class="small text-muted text-break">' + escapeHtml(toolContent.slice(0, 300)) + '</div>' : '') +
            '</div></div>'
        );
    }

    if (step.name === 'budget') {
        var budget = preview.budget_message ? String(preview.budget_message) : '';
        return (
            '<div class="small text-muted py-1">' +
            '<i class="bi bi-hourglass-split me-1"></i>' +
            escapeHtml(budget.slice(0, 200)) +
            '</div>'
        );
    }

    return (
        '<div class="small text-muted py-1">' +
        escapeHtml(String(step.name || '')) +
        ' ' + agentRunSpanStatusBadge(step.status) +
        '</div>'
    );
}

/**
 * 完整时间线：按 iteration 分组（组内按 sequence），头部保留汇总信息
 * （LLM 调用 N 轮 / 工具 M 次 / 预算 K 条 / 合计 tokens，与旧简版口径一致）。
 * @param {Array} steps agent_steps 列表（已按 (iteration, sequence) 排序）
 */
function renderAgentRunSteps(steps) {
    if (!Array.isArray(steps) || steps.length === 0) {
        return '<span class="text-muted">暂无评估过程记录</span>';
    }
    var llmCalls = steps.filter(function (s) { return s.name === 'llm_chat'; });
    var toolCalls = steps.filter(function (s) { return s.name === 'tool_execute'; });
    var budgets = steps.filter(function (s) { return s.name === 'budget'; });
    var totalTokens = llmCalls.reduce(function (acc, s) {
        return acc + (Number(s.tokens) || 0);
    }, 0);

    var html = '<div class="small text-muted mb-2">LLM 调用 ' + llmCalls.length +
        ' 轮 · 工具调用 ' + toolCalls.length + ' 次 · 预算消息 ' + budgets.length +
        ' 条 · 合计 ' + totalTokens + ' tokens</div>';

    var byIter = new Map();
    steps.forEach(function (s) {
        var it = Number.isInteger(s.iteration) ? s.iteration : 0;
        if (!byIter.has(it)) byIter.set(it, []);
        byIter.get(it).push(s);
    });

    html += '<div class="agent-run-timeline">';
    Array.from(byIter.keys()).sort(function (a, b) { return a - b; }).forEach(function (it) {
        var items = byIter.get(it).slice().sort(function (a, b) {
            return (Number(a.sequence) || 0) - (Number(b.sequence) || 0);
        });
        html += '<div class="border rounded p-2 mb-2">' +
            '<div class="small fw-semibold text-muted mb-1">第 ' + (it + 1) + ' 轮</div>';
        items.forEach(function (s) { html += renderAgentRunStepRow(s); });
        html += '</div>';
    });
    html += '</div>';
    return html;
}

/**
 * 懒加载并渲染 run 的步骤时间线。
 * 默认只加载一次（dataset.loaded 守卫，候选页折叠区语义）；
 * options.force=true 时忽略守卫（观测页重复打开不同 run 时使用）。
 */
async function loadAgentRunSteps(runId, targetId, options) {
    var opts = options || {};
    var el = document.getElementById(targetId);
    if (!el) return;
    if (el.dataset.loaded === '1' && !opts.force) return;
    el.dataset.loaded = '1';
    el.innerHTML = '<span class="text-muted">加载中…</span>';
    try {
        var data = await apiFetch('/api/agent/runs/' + encodeURIComponent(runId) + '/steps');
        if (Array.isArray(data)) {
            el.innerHTML = renderAgentRunSteps(data);
        } else {
            el.innerHTML = '<span class="text-muted">无评估过程数据</span>';
        }
    } catch (e) {
        el.dataset.loaded = '';
        el.innerHTML = '<span class="text-danger">加载评估过程失败</span>';
    }
}

/**
 * 候选列表行的 AI 徽标：
 * - agent_run_status ∈ {pending, processing} → "AI 评估中"
 * - agent_run_status == succeeded 且有 llm_subject_id → "AI 推荐"
 * - 其余（无 run / failed 等）→ 不显示
 */
function renderAgentRunBadge(status, llmSubjectId) {
    if (status === 'pending' || status === 'processing') {
        return '<span class="badge rounded-pill bg-warning bg-opacity-75 ms-1" title="AI 正在评估匹配建议">AI 评估中</span>';
    }
    if (status === 'succeeded' && llmSubjectId) {
        return '<span class="badge rounded-pill bg-primary ms-1" title="AI 已给出推荐">AI 推荐</span>';
    }
    return '';
}

window.agentRunSpanStatusBadge = agentRunSpanStatusBadge;
window.renderAgentRunSteps = renderAgentRunSteps;
window.loadAgentRunSteps = loadAgentRunSteps;
window.renderAgentRunBadge = renderAgentRunBadge;
// 历史别名：pending_candidates 内联 onclick 仍使用 _loadAgentRunSteps
window._loadAgentRunSteps = loadAgentRunSteps;
