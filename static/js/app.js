// 通用JavaScript功能

/**
 * 子路径反向代理下的站内 URL（与后端 join_public / 模板 | p 一致）
 */
function appUrl(path) {
    const base =
        typeof window.__APP_BASE_PATH__ === 'string' ? window.__APP_BASE_PATH__ : '';
    if (!path) {
        return base || '/';
    }
    const p = path.startsWith('/') ? path : '/' + path;
    return base + p;
}

// 显示提示消息
function showAlert(message, type = 'info', duration = 5000) {
    // 创建Toast容器（如果不存在）
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '1055';
        document.body.appendChild(toastContainer);
    }
    
    // 创建Toast
    const toastId = 'toast-' + Date.now();
    const toastDiv = document.createElement('div');
    toastDiv.id = toastId;
    toastDiv.className = `toast align-items-center text-white bg-${type} border-0`;
    toastDiv.setAttribute('role', 'alert');
    toastDiv.setAttribute('aria-live', 'assertive');
    toastDiv.setAttribute('aria-atomic', 'true');
    
    // 获取图标
    const icon = getToastIcon(type);
    
    toastDiv.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                <i class="${icon} me-2"></i>${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="关闭"></button>
        </div>
    `;
    
    // 添加到容器
    toastContainer.appendChild(toastDiv);
    
    // 初始化Toast
    const toast = new bootstrap.Toast(toastDiv, {
        autohide: duration > 0,
        delay: duration
    });
    
    // 显示Toast
    toast.show();
    
    // 监听隐藏事件，移除DOM元素
    toastDiv.addEventListener('hidden.bs.toast', () => {
        if (toastDiv.parentNode) {
            toastDiv.parentNode.removeChild(toastDiv);
        }
    });
}

// 获取Toast图标
function getToastIcon(type) {
    switch (type) {
        case 'success': return 'bi bi-check-circle-fill';
        case 'danger': return 'bi bi-exclamation-triangle-fill';
        case 'warning': return 'bi bi-exclamation-triangle-fill';
        case 'info': return 'bi bi-info-circle-fill';
        default: return 'bi bi-info-circle-fill';
    }
}

// 格式化日期
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function getSyncRecordStatusColor(status) {
    switch (status) {
        case 'success': return 'success';
        case 'error': return 'danger';
        case 'ignored': return 'warning';
        case 'retried': return 'success';
        default: return 'secondary';
    }
}

function getSyncRecordStatusText(status) {
    switch (status) {
        case 'success': return '成功';
        case 'error': return '失败';
        case 'ignored': return '已忽略';
        case 'retried': return '已重试';
        default: return status;
    }
}

function renderSyncStatusBadge(status) {
    return `<span class="badge rounded-pill bg-${getSyncRecordStatusColor(status)}">${getSyncRecordStatusText(status)}</span>`;
}

function renderMatchMethodBadge(method) {
    const badges = {
        custom_mapping: ['primary', '自定义映射'],
        bangumi_data: ['success', 'bangumi-data'],
        api_search: ['info', 'API 搜索'],
        failed: ['danger', '失败'],
    };
    const [color, text] = badges[method] || ['secondary', '未知'];
    return `<span class="badge rounded-pill bg-${color}">${text}</span>`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

function normalizeRecordText(value) {
    return String(value || '').trim();
}

function isMatchFailure(record, trace) {
    if (record.match_method === 'failed') {
        return true;
    }
    if (trace && trace.final_match_method === 'failed') {
        return true;
    }
    return false;
}

function renderRecordDetailFacts(items) {
    const rows = (items || []).filter((item) => {
        const v = item.value;
        return v !== null && v !== undefined && v !== '';
    });
    if (rows.length === 0) {
        return '';
    }
    let html = '<dl class="record-detail-facts">';
    rows.forEach((item) => {
        const wideClass = item.wide ? ' record-detail-fact--wide' : '';
        html += `<div class="record-detail-fact${wideClass}">`;
        html += `<dt>${escapeHtml(item.label)}</dt>`;
        html += `<dd>${item.value}</dd>`;
        html += '</div>';
    });
    html += '</dl>';
    return html;
}

function parseRecordMatchTrace(record) {
    const raw = record && record.match_trace;
    if (!raw) {
        return null;
    }
    if (typeof raw === 'object') {
        return raw;
    }
    try {
        return JSON.parse(raw);
    } catch (error) {
        return null;
    }
}

function getRecordReleaseDate(record, trace) {
    const fromTrace = trace?.request_release_date
        || parseRecordMatchTrace(record)?.request_release_date
        || '';
    return normalizeRecordText(fromTrace);
}

function hasDisplayText(value) {
    return !!normalizeRecordText(value);
}

function renderRecordDetailZone(variant, title, hint, bodyHtml, sectionId) {
    const icons = {
        receive: 'bi-box-arrow-in-down',
        match: 'bi-diagram-3',
        steps: 'bi-signpost-split',
        result: 'bi-clipboard2-check',
        'result-error': 'bi-exclamation-circle',
    };
    const icon = icons[variant] || 'bi-info-circle';
    const idAttr = sectionId ? ` id="${sectionId}"` : '';
    return `
        <section class="record-detail-zone record-detail-zone--${variant}"${idAttr}>
            <header class="record-detail-zone__head">
                <div class="record-detail-zone__icon" aria-hidden="true">
                    <i class="bi ${icon}"></i>
                </div>
                <div class="record-detail-zone__titles">
                    <h6 class="record-detail-zone__title">${escapeHtml(title)}</h6>
                    ${hint ? `<p class="record-detail-zone__hint">${escapeHtml(hint)}</p>` : ''}
                </div>
            </header>
            <div class="record-detail-zone__body">${bodyHtml}</div>
        </section>
    `;
}

function getMediaTypeLabel(mediaType) {
    const map = {
        episode: '剧集',
        movie: '电影/剧场版',
        ova: 'OVA/OAD',
        real_action: '三次元',
    };
    const mt = (mediaType || 'episode').toLowerCase();
    return map[mt] || mediaType || '—';
}

function renderEpisodeFactValue(record, trace) {
    const mediaType = trace?.request_media_type || record.media_type;
    const isMovie = (mediaType || 'episode').toLowerCase() === 'movie';
    if (isMovie) {
        return '<span class="record-detail-chip">剧场版</span>';
    }
    const season = trace?.request_season ?? record.season ?? 0;
    const episode = trace?.request_episode ?? record.episode ?? 0;
    return `S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`;
}

function renderMatchInputFacts(record, trace) {
    const title = trace?.request_title || record.title;
    const oriTitle = trace?.request_ori_title || record.ori_title;
    const mediaType = trace?.request_media_type || record.media_type;
    const user = trace?.request_user_name || record.user_name;
    const facts = [];

    if (hasDisplayText(title)) {
        facts.push({ label: '匹配标题', value: escapeHtml(title), wide: true });
    }
    if (hasDisplayText(oriTitle)) {
        facts.push({ label: '匹配原标题', value: escapeHtml(oriTitle), wide: true });
    }
    if (hasDisplayText(user)) {
        facts.push({ label: '用户', value: escapeHtml(user) });
    }
    if (mediaType) {
        facts.push({ label: '媒体类型', value: renderMediaTypeBadge(mediaType) });
    }
    facts.push({ label: '季 / 集', value: renderEpisodeFactValue(record, trace) });

    if (hasDisplayText(trace?.normalized_title)) {
        facts.push({ label: '归一化标题', value: escapeHtml(trace.normalized_title), wide: true });
    }

    const subsection = facts.length > 0
        ? renderRecordDetailFacts(facts)
        : '<p class="record-detail-empty-hint mb-0">无匹配输入信息</p>';
    return subsection;
}

function renderMatchFailureBanner(record) {
    const mapTitle = encodeURIComponent(record.title || '');
    const mapSeason = record.season || 1;
    return `
        <div class="record-detail-banner record-detail-banner--warn record-detail-banner--steps">
            <i class="bi bi-exclamation-triangle-fill"></i>
            <span class="flex-grow-1">未匹配到 Bangumi 条目，可添加自定义映射解决</span>
            <a href="${appUrl('/mappings')}?title=${mapTitle}&season=${mapSeason}" class="record-detail-banner__action">
                前往映射 <i class="bi bi-arrow-up-right"></i>
            </a>
        </div>
    `;
}

function getMatchStepStatusLabel(status) {
    const labels = {
        hit: '命中',
        miss: '未命中',
        skipped: '已跳过',
        error: '出错',
    };
    return labels[status] || status || '未知';
}

// 流水线阶段名映射（10 阶段）
const PIPELINE_STAGE_NAMES = {
    receive: '接收请求',
    normalize: '标题归一化',
    custom_mapping: '自定义映射',
    bangumi_data: 'bangumi-data 本地匹配',
    api_search: 'Bangumi API 搜索',
    post_search: '搜索后处理',
    cross_season: '跨季链查找',
    episode_resolve: '集数解析',
    sync_action: '同步动作',
    result: '同步结果',
};

function getPipelineStageName(stage) {
    return PIPELINE_STAGE_NAMES[stage] || stage;
}

// 流水线摘要卡：输入 → 输出
function renderPipelineSummary(record, trace) {
    const title = record.title || (trace && trace.request_title) || '';
    const season = (trace && trace.request_season) || record.season || 1;
    const episode = (trace && trace.request_episode) || record.episode || 0;
    const source = record.source || (trace && trace.request_platform_hint) || '';
    const mediaType = (trace && trace.request_media_type) || record.media_type || 'episode';

    const isSuccess = record.status === 'success' || record.status === 'retried';
    const statusClass = isSuccess ? 'success' : (record.status === 'error' ? 'error' : 'neutral');
    const statusIcon = isSuccess ? 'bi-check-circle-fill' : (record.status === 'error' ? 'bi-x-circle-fill' : 'bi-dash-circle-fill');
    const statusText = isSuccess ? '同步成功' : (record.status === 'error' ? '同步失败' : (record.status || '未知'));

    const subjectId = record.subject_id || (trace && trace.final_subject_id);
    const episodeId = record.episode_id || (trace && trace.final_episode_id);
    const score = (trace && trace.final_score !== null && trace.final_score !== undefined)
        ? trace.final_score
        : record.match_score;
    const bgmTitle = record.bgm_title || '';

    // 输入区
    let inputHtml = `<span class="record-pipeline-summary__title">${escapeHtml(title)}</span>`;
    inputHtml += `<span class="record-pipeline-summary__episode">S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}</span>`;
    if (source) {
        inputHtml += `<span class="badge bg-secondary">${escapeHtml(source)}</span>`;
    }
    inputHtml += `<span class="badge bg-info">${escapeHtml(mediaType)}</span>`;

    // 输出区
    let outputHtml = `<i class="bi ${statusIcon} text-${isSuccess ? 'success' : (record.status === 'error' ? 'danger' : 'secondary')}"></i>`;
    outputHtml += `<span class="record-pipeline-summary__status-text record-pipeline-summary__status-text--${statusClass}">${escapeHtml(statusText)}</span>`;
    if (subjectId) {
        outputHtml += `<a href="https://bgm.tv/subject/${escapeHtml(subjectId)}" target="_blank" class="record-pipeline-summary__link">`
            + `<i class="bi bi-collection"></i>subject/${escapeHtml(subjectId)}`;
        if (bgmTitle) {
            outputHtml += ` ${escapeHtml(bgmTitle)}`;
        }
        outputHtml += `</a>`;
    }
    if (episodeId) {
        outputHtml += `<a href="https://bgm.tv/ep/${escapeHtml(episodeId)}" target="_blank" class="record-pipeline-summary__link">`
            + `<i class="bi bi-play-circle"></i>ep/${escapeHtml(episodeId)}</a>`;
    }
    if (score !== null && score !== undefined && isSuccess) {
        outputHtml += `<span class="record-pipeline-summary__score">置信度 ${(score * 100).toFixed(0)}%</span>`;
    }

    return `
        <section class="record-pipeline-summary record-pipeline-summary--${statusClass}">
            <div class="record-pipeline-summary__input">${inputHtml}</div>
            <div class="record-pipeline-summary__arrow" aria-hidden="true">
                <i class="bi bi-arrow-down"></i>
            </div>
            <div class="record-pipeline-summary__output">${outputHtml}</div>
        </section>
    `;
}

// 通用 payload 表格渲染：横向表头 + 单行数据
// labels: 可选的 { key: 显示标签 } 映射；以 _url 结尾且非空的字段渲染为链接
function renderPayloadTable(payload, labels) {
    if (!payload || typeof payload !== 'object') {
        return '';
    }
    const keys = Object.keys(payload);
    if (keys.length === 0) {
        return '';
    }
    const labelMap = labels || {};
    const headCells = keys.map((k) => {
        const label = labelMap[k] || k;
        return `<th>${escapeHtml(label)}</th>`;
    }).join('');
    const bodyCells = keys.map((k) => {
        const v = payload[k];
        const vv = (v === null || v === undefined) ? '' : String(v);
        let valueCell;
        if (k.endsWith('_url') && vv) {
            valueCell = `<a href="${escapeHtml(vv)}" target="_blank" rel="noopener">${escapeHtml(vv)}</a>`;
        } else {
            valueCell = escapeHtml(vv);
        }
        return `<td>${valueCell}</td>`;
    }).join('');
    return '<div class="table-responsive">'
        + '<table class="table table-sm table-bordered mb-0 record-detail-payload-table">'
        + `<thead><tr>${headCells}</tr></thead>`
        + `<tbody><tr>${bodyCells}</tr></tbody></table></div>`;
}

// receive step 输入字段表格：sync 开始时的输入
function renderReceiveInputTable(step) {
    if (!step || step.stage !== 'receive' || !step.processed_payload) {
        return '';
    }
    let html = renderPayloadTable(step.processed_payload, {
        source: '来源',
        user_name: '用户',
        title: '标题',
        ori_title: '原始标题',
        season: '季',
        episode: '集',
        media_type: '媒体类型',
        release_date: '发布日期',
        sync_action: '同步动作',
    });
    if (step.raw_payload && typeof step.raw_payload === 'object' && Object.keys(step.raw_payload).length > 0) {
        html += '<div class="mt-2">'
            + '<div class="small text-muted mb-1">驱动原始数据</div>'
            + `<pre class="record-detail-raw-payload mb-0">${escapeHtml(JSON.stringify(step.raw_payload, null, 2))}</pre>`
            + '</div>';
    }
    return html;
}

// result step 结果表格：状态/集数/链接/消息
function renderResultTable(step) {
    if (!step || step.stage !== 'result' || !step.processed_payload) {
        return '';
    }
    return renderPayloadTable(step.processed_payload, {
        status: '状态',
        episode: '集数',
        subject_id: '条目 ID',
        episode_id: '剧集 ID',
        subject_url: '条目链接',
        episode_url: '剧集链接',
        bgm_title: '番剧标题',
        message: '消息',
    });
}

// episode_resolve step 表格：展示输入→输出的集数解析变更过程
function renderEpisodeResolveTable(step) {
    if (!step || step.stage !== 'episode_resolve' || !step.processed_payload) {
        return '';
    }
    return renderPayloadTable(step.processed_payload, {
        input_subject_id: '输入条目 ID',
        input_is_season_id: '是否季度 ID',
        request_season: '请求季',
        request_episode: '请求集',
        media_type: '媒体类型',
        release_date: '发布日期',
        output_subject_id: '输出条目 ID',
        output_episode_id: '输出剧集 ID',
        changed: '是否变更',
        subject_url: '条目链接',
        episode_url: '剧集链接',
        error: '错误',
    });
}

// cross_season step 表格：展示跨季链查找的变更过程
function renderCrossSeasonTable(step) {
    if (!step || step.stage !== 'cross_season' || !step.processed_payload) {
        return '';
    }
    return renderPayloadTable(step.processed_payload, {
        input_subject_id: '输入条目 ID',
        output_subject_id: '输出条目 ID',
        output_episode_id: '输出剧集 ID',
        target_episode: '目标集',
        changed: '是否变更',
        subject_url: '条目链接',
        episode_url: '剧集链接',
        error: '错误',
    });
}

// 流水线渲染：10 阶段统一展示
function renderPipelineHtml(record, trace) {
    const scoreText = (s) => (s !== null && s !== undefined) ? `${(s * 100).toFixed(1)}%` : '-';

    if (!trace) {
        return `
            <p class="record-detail-empty-hint mb-0">
                无匹配追踪数据（可能为旧版记录），可在
                <a href="${appUrl('/debug')}">调试工具</a> 中测试匹配。
            </p>
        `;
    }

    if (!trace.steps || trace.steps.length === 0) {
        return '<p class="record-detail-empty-hint mb-0">匹配追踪为空，无步骤数据。</p>';
    }

    let html = '<div class="record-detail-steps">';

    trace.steps.forEach((step, idx) => {
        const status = step.status || 'unknown';
        const stageName = getPipelineStageName(step.stage);

        html += `<article class="record-detail-step record-detail-step--${status}">`;
        html += '<div class="record-detail-step__rail" aria-hidden="true">';
        html += `<span class="record-detail-step__index">${idx + 1}</span>`;
        if (idx < trace.steps.length - 1) {
            html += '<span class="record-detail-step__line"></span>';
        }
        html += '</div>';
        html += '<div class="record-detail-step__card">';
        html += '<header class="record-detail-step__head">';
        html += `<strong class="record-detail-step__name">${escapeHtml(stageName)}</strong>`;
        html += `<span class="record-detail-step__badge record-detail-step__badge--${status}">${getMatchStepStatusLabel(status)}</span>`;
        if (step.score !== null && step.score !== undefined) {
            html += `<span class="record-detail-step__score">${(step.score * 100).toFixed(0)}%</span>`;
        }
        html += `<span class="record-detail-step__time">${step.elapsed_ms || 0}ms</span>`;
        html += '</header>';

        if (step.reason) {
            html += `<p class="record-detail-step__reason">${escapeHtml(step.reason)}</p>`;
        }

        // receive step：渲染 sync 开始时的输入字段表格
        if (step.stage === 'receive') {
            html += renderReceiveInputTable(step);
        }

        if (step.candidates && step.candidates.length > 0) {
            html += renderMatchCandidatesTable(step.candidates, scoreText);
        }

        // episode_resolve step：渲染输入→输出变更过程表格
        if (step.stage === 'episode_resolve') {
            const t = renderEpisodeResolveTable(step);
            if (t) {
                html += `<div class="mt-2">${t}</div>`;
            }
        }

        // cross_season step：渲染跨季链查找变更过程表格
        if (step.stage === 'cross_season') {
            const t = renderCrossSeasonTable(step);
            if (t) {
                html += `<div class="mt-2">${t}</div>`;
            }
        }

        // result step：渲染结果表格（集数 + 链接 + 消息）
        if (step.stage === 'result') {
            const resultTable = renderResultTable(step);
            if (resultTable) {
                html += `<div class="mt-2">${resultTable}</div>`;
            }
        }

        html += '</div></article>';
    });

    html += '</div>';
    return html;
}

function renderMatchStepsHtml(record, trace) {
    // 旧调用方（debug 工具页）兼容：直接复用流水线渲染
    return renderPipelineHtml(record, trace);
}

function renderMatchInputZone(record, trace) {
    const body = renderMatchInputFacts(record, trace);

    return renderRecordDetailZone(
        'match',
        '匹配信息',
        '用于 Bangumi 条目识别的输入',
        body,
        'record-section-match',
    );
}

function renderMatchStepsZone(record, trace) {
    let body = renderMatchStepsHtml(record, trace);
    if (isMatchFailure(record, trace)) {
        body += renderMatchFailureBanner(record);
    }

    return renderRecordDetailZone(
        'steps',
        '匹配步骤',
        '各阶段匹配过程与候选结果',
        body,
        'record-section-steps',
    );
}

function renderMatchDetailModalParts(record, trace) {
    return {
        match: renderMatchInputZone(record, trace),
        steps: renderMatchStepsZone(record, trace),
    };
}

function isRecordSyncSuccess(record) {
    return record.status === 'success' || record.status === 'retried';
}

function renderSyncResultContent(record, trace) {
    const isSuccess = isRecordSyncSuccess(record);
    const score = (trace && trace.final_score !== null && trace.final_score !== undefined)
        ? trace.final_score
        : record.match_score;
    const method = record.match_method || (trace && trace.final_match_method) || '';
    const subjectId = record.subject_id || (trace && trace.final_subject_id);
    const episodeId = record.episode_id || (trace && trace.final_episode_id);
    let body = '';

    if (isSuccess) {
        const facts = [];
        if (record.bgm_title) {
            facts.push({ label: 'Bangumi 条目', value: escapeHtml(record.bgm_title), wide: true });
        }
        if (method) {
            facts.push({ label: '匹配方式', value: renderMatchMethodBadge(method) });
        }
        if (score !== null && score !== undefined) {
            facts.push({ label: '置信度', value: `${(score * 100).toFixed(0)}%` });
        }
        const links = renderBangumiLinkPills(subjectId, episodeId);
        if (links) {
            facts.push({ label: '链接', value: links, wide: true });
        }
        body += renderRecordDetailFacts(facts);
    }

    const messageText = normalizeRecordText(record.message);
    if (messageText) {
        const isNoMatch = isMatchFailure(record, trace);
        const messageClass = record.status === 'error'
            ? 'record-detail-message record-detail-message--error'
            : 'record-detail-message';
        const msgLabel = (isSuccess || isNoMatch) ? '消息' : '同步结果';
        let inlineClass = 'record-detail-inline-msg';
        if (!body || isNoMatch) {
            inlineClass += ' record-detail-inline-msg--only';
        }
        body += `
            <div class="${inlineClass}">
                <span class="record-detail-inline-msg__label">${msgLabel}</span>
                <pre class="${messageClass} mb-0">${escapeHtml(messageText)}</pre>
            </div>
        `;
    } else if (!isSuccess) {
        body += `<div class="record-detail-result-status">${renderSyncStatusBadge(record.status)}</div>`;
    }

    if (!body) {
        body = '<p class="record-detail-empty-hint mb-0">无结果信息</p>';
    }

    const variant = isSuccess ? 'result' : 'result-error';
    const hint = isSuccess ? '已成功同步到 Bangumi' : '同步未成功或已忽略';
    return renderRecordDetailZone(variant, '同步结果', hint, body, 'record-section-result');
}

function renderBangumiLinkPills(subjectId, episodeId) {
    const pills = [];
    if (subjectId) {
        pills.push(`
            <a href="https://bgm.tv/subject/${subjectId}" target="_blank" rel="noopener"
               class="record-detail-link-pill record-detail-link-pill--subject">
                <i class="bi bi-collection"></i>条目 ${subjectId}
            </a>
        `);
    }
    if (episodeId) {
        pills.push(`
            <a href="https://bgm.tv/ep/${episodeId}" target="_blank" rel="noopener"
               class="record-detail-link-pill record-detail-link-pill--episode">
                <i class="bi bi-play-circle"></i>剧集 ${episodeId}
            </a>
        `);
    }
    if (pills.length === 0) {
        return '';
    }
    return `<div class="record-detail-link-pills">${pills.join('')}</div>`;
}

function renderRecordDetailTileGrid(items) {
    const rows = (items || []).filter((item) => {
        const v = item.value;
        return v !== null && v !== undefined && v !== '';
    });
    if (rows.length === 0) {
        return '<p class="text-muted small mb-0">暂无信息</p>';
    }
    let html = '<div class="record-detail-grid">';
    rows.forEach((item) => {
        const wideClass = item.wide ? ' record-detail-tile--wide' : '';
        html += `<div class="record-detail-tile${wideClass}">`;
        html += `<span class="record-detail-tile__label">${escapeHtml(item.label)}</span>`;
        html += `<span class="record-detail-tile__value">${item.value}</span>`;
        html += '</div>';
    });
    html += '</div>';
    return html;
}

function renderRecordDetailBlock(title, icon, bodyHtml, sectionId) {
    const idAttr = sectionId ? ` id="${sectionId}"` : '';
    return `
        <section class="record-detail-section"${idAttr}>
            <div class="record-detail-section__head">
                <i class="bi ${icon} card-header-icon"></i>
                <h6 class="record-detail-section__title">${escapeHtml(title)}</h6>
            </div>
            <div class="record-detail-section__body">${bodyHtml}</div>
        </section>
    `;
}

function renderRecordDetailKvGrid(items) {
    const rows = (items || []).filter((item) => {
        const v = item.value;
        return v !== null && v !== undefined && v !== '';
    });
    if (rows.length === 0) {
        return '<p class="text-muted small mb-0">暂无信息</p>';
    }
    let html = '<dl class="row record-detail-kv mb-0">';
    rows.forEach((item) => {
        html += `<dt class="col-sm-4 col-md-3">${escapeHtml(item.label)}</dt>`;
        html += `<dd class="col-sm-8 col-md-9">${item.value}</dd>`;
    });
    html += '</dl>';
    return html;
}

function renderRecordDetailSection(title, icon, bodyHtml, extraClass) {
    const sectionId = extraClass && extraClass.startsWith('id:') ? extraClass.slice(3) : '';
    return renderRecordDetailBlock(title, icon, bodyHtml, sectionId || null);
}

function getRecordEpisodeLabel(record) {
    const isMovie = (record.media_type || 'episode').toLowerCase() === 'movie';
    if (isMovie) {
        return '剧场版';
    }
    return `S${String(record.season || 0).padStart(2, '0')}E${String(record.episode || 0).padStart(2, '0')}`;
}

function updateRecordDetailModalChrome(record) {
    const headerEl = document.getElementById('record-detail-modal-header');
    const titleEl = document.getElementById('record-detail-modal-title');
    const subtitleEl = document.getElementById('record-detail-modal-subtitle');
    const statusEl = document.getElementById('record-detail-modal-status');
    const retryBtn = document.getElementById('record-detail-retry-btn');
    const helpLink = document.querySelector('.record-detail-modal__help');

    const displayTitle = record.bgm_title || record.title || '同步记录';
    const epLabel = getRecordEpisodeLabel(record);
    const statusChrome = {
        success: ['bi-check-circle-fill', 'record-detail-modal__header--success'],
        error: ['bi-x-circle-fill', 'record-detail-modal__header--error'],
        ignored: ['bi-dash-circle-fill', 'record-detail-modal__header--warning'],
        retried: ['bi-arrow-repeat', 'record-detail-modal__header--success'],
    };
    const [statusIcon, statusClass] = statusChrome[record.status] || ['bi-journal-text', ''];

    if (titleEl) {
        titleEl.innerHTML = `${escapeHtml(displayTitle)}<span class="text-muted fw-normal"> · ${epLabel}</span>`;
    }

    if (subtitleEl) {
        subtitleEl.innerHTML = [
            `<span>${escapeHtml(record.timestamp || '')}</span>`,
            renderSourceBadge(record.source),
        ].join('<span class="record-detail-meta-dot">·</span>');
    }

    if (headerEl) {
        headerEl.classList.remove(
            'record-detail-modal__header--error',
            'record-detail-modal__header--success',
            'record-detail-modal__header--warning',
        );
        if (statusClass) {
            headerEl.classList.add(statusClass);
        }
    }

    if (statusEl) {
        statusEl.innerHTML = `<i class="bi ${statusIcon}"></i>`;
        statusEl.title = getSyncRecordStatusText(record.status);
        statusEl.setAttribute('aria-label', getSyncRecordStatusText(record.status));
    }

    if (retryBtn) {
        const showRetry = record.status === 'error';
        retryBtn.classList.toggle('d-none', !showRetry);
        retryBtn.onclick = function() {
            retrySync(record.id);
        };
    }

    if (helpLink) {
        helpLink.classList.toggle('d-none', record.status !== 'error');
        helpLink.classList.toggle('d-inline-flex', record.status === 'error');
    }
}

function renderMatchCandidatesTable(candidates, scoreText) {
    if (!candidates || candidates.length === 0) {
        return '';
    }

    const renderRows = (items) => items.map((cand) => {
        const name = escapeHtml(cand.name_cn || cand.name || cand.subject_id || '-');
        return `<tr>
            <td><a href="https://bgm.tv/subject/${cand.subject_id}" target="_blank">${name}</a></td>
            <td><small>${escapeHtml(String(cand.subject_id || '-'))}</small></td>
            <td><small>${scoreText(cand.score)}</small></td>
            <td><small>${escapeHtml(cand.platform || '-')}</small></td>
            <td><small>${escapeHtml(cand.air_date || '-')}</small></td>
            <td><small>${escapeHtml(cand.source || '-')}</small></td>
        </tr>`;
    }).join('');

    const tableHead = '<thead><tr><th>条目</th><th>subject_id</th><th>置信度</th><th>平台</th><th>放送日期</th><th>来源</th></tr></thead>';
    const tableStart = '<div class="table-responsive"><table class="table table-sm table-bordered align-middle mb-0">';
    const tableEnd = '</table></div>';

    if (candidates.length <= 3) {
        return tableStart + tableHead + '<tbody>' + renderRows(candidates) + '</tbody>' + tableEnd;
    }

    const rest = candidates.slice(3);
    return tableStart + tableHead + '<tbody>' + renderRows(candidates.slice(0, 3)) + '</tbody>' + tableEnd
        + `<details class="record-detail-candidates mt-2">
            <summary>展开其余 ${rest.length} 条候选</summary>
            ${tableStart + tableHead + '<tbody>' + renderRows(rest) + '</tbody>' + tableEnd}
        </details>`;
}

function renderMatchDetailModalContent(record, trace) {
    const parts = renderMatchDetailModalParts(record, trace);
    return parts.match + parts.steps;
}

function renderMatchTraceDetail(record, trace, options) {
    const opts = options || {};
    if (opts.skipBasicInfo) {
        return renderMatchDetailModalContent(record, trace);
    }

    const val = (v, d = '-') => (v === null || v === undefined || v === '') ? d : v;
    const mediaTypeLabel = (t) => {
        const map = {
            episode: '剧集',
            movie: '电影/剧场版',
            ova: 'OVA/OAD',
            real_action: '三次元',
        };
        return map[t] || t || '-';
    };
    const subjectLink = (sid, name) => sid
        ? `<a href="https://bgm.tv/subject/${sid}" target="_blank">${escapeHtml(name || sid)}</a>`
        : '-';
    const episodeLink = (eid) => eid
        ? `<a href="https://bgm.tv/ep/${eid}" target="_blank">ep/${eid}</a>`
        : '-';
    const scoreText = (s) => (s !== null && s !== undefined) ? `${(s * 100).toFixed(1)}%` : '-';

    let html = '';

    html += renderRecordDetailSection('基本信息', 'bi-info-circle', renderRecordDetailKvGrid([
            { label: '标题', value: escapeHtml(val(record.title)) },
            { label: '原始标题', value: escapeHtml(val(record.ori_title)) },
            { label: '番剧标题', value: escapeHtml(val(record.bgm_title)) },
            { label: '来源', value: renderSourceBadge(record.source) },
            { label: '季度/集数', value: `S${String(record.season || 0).padStart(2, '0')}E${String(record.episode || 0).padStart(2, '0')}` },
            { label: '媒体类型', value: mediaTypeLabel(record.media_type) },
            { label: '时间', value: escapeHtml(val(record.timestamp)) },
            { label: '用户', value: escapeHtml(val(record.user_name)) },
            { label: '状态', value: renderSyncStatusBadge(record.status) },
            { label: '消息', value: escapeHtml(val(record.message)) },
            { label: '最终匹配方式', value: renderMatchMethodBadge(record.match_method || (trace && trace.final_match_method) || '') },
            { label: '最终置信度', value: scoreText((trace && trace.final_score !== null && trace.final_score !== undefined) ? trace.final_score : record.match_score) },
            { label: '命中条目', value: subjectLink(record.subject_id || (trace && trace.final_subject_id), record.bgm_title) },
            { label: '命中剧集', value: episodeLink((trace && trace.final_episode_id) || record.episode_id) },
        ]));

    if (trace) {
        const ctxFields = [
            ['请求标题', trace.request_title],
            ['请求原始标题', trace.request_ori_title],
            ['请求季度', trace.request_season],
            ['请求集数', trace.request_episode],
            ['请求媒体类型', trace.request_media_type ? mediaTypeLabel(trace.request_media_type) : null],
            ['请求发布日期', trace.request_release_date],
            ['请求用户', trace.request_user_name],
            ['请求平台提示', trace.request_platform_hint],
            ['归一化标题', trace.normalized_title],
        ].filter(([, v]) => v !== null && v !== undefined && v !== '');

        if (ctxFields.length > 0) {
            const ctxGrid = renderRecordDetailKvGrid(ctxFields.map(([label, v]) => ({
                label,
                value: escapeHtml(String(v)),
            })));
            html += renderRecordDetailBlock('匹配上下文', 'bi-braces', ctxGrid);
        }
    }

    if (isMatchFailure(record, trace)) {
        const mapTitle = encodeURIComponent(record.title || '');
        const mapSeason = record.season || 1;
        html += `
            <div class="record-detail-banner record-detail-banner--warn">
                <i class="bi bi-exclamation-triangle-fill"></i>
                <span class="flex-grow-1">未匹配到 Bangumi 条目，可添加自定义映射解决</span>
                <a href="${appUrl('/mappings')}?title=${mapTitle}&season=${mapSeason}" class="record-detail-banner__action">
                    前往映射 <i class="bi bi-arrow-up-right"></i>
                </a>
            </div>
        `;
    }

    if (trace && trace.steps && trace.steps.length > 0) {
        html += '<p class="record-detail-steps-label">匹配步骤</p>';
        html += '<ol class="record-detail-timeline mb-0">';

        trace.steps.forEach((step, idx) => {
            const statusIcon = {
                hit: '<i class="bi bi-check-circle-fill text-success"></i>',
                miss: '<i class="bi bi-x-circle-fill text-danger"></i>',
                skipped: '<i class="bi bi-skip-forward text-muted"></i>',
                error: '<i class="bi bi-exclamation-triangle-fill text-danger"></i>',
            }[step.status] || '<i class="bi bi-question-circle text-muted"></i>';

            const stageName = {
                custom_mapping: '自定义映射',
                bangumi_data: 'bangumi-data 本地匹配',
                api_search: 'Bangumi API 搜索',
            }[step.stage] || step.stage;

            const statusClass = step.status ? ` record-detail-timeline__item--${step.status}` : '';

            html += `<li class="record-detail-timeline__item${statusClass}">`;
            html += `<span class="record-detail-timeline__marker">${statusIcon}</span>`;
            html += '<div class="record-detail-timeline__content">';
            html += '<div class="record-detail-timeline__head">';
            html += `<strong class="record-detail-timeline__stage">${idx + 1}. ${stageName}</strong>`;
            if (step.score !== null && step.score !== undefined) {
                html += `<span class="record-detail-step-tag">${(step.score * 100).toFixed(0)}%</span>`;
            }
            html += `<small class="record-detail-step-time">${step.elapsed_ms || 0}ms</small>`;
            html += '</div>';

            if (step.reason) {
                html += `<div class="record-detail-step-reason">${escapeHtml(step.reason)}</div>`;
            }

            if (step.subject_id) {
                html += `<div class="record-detail-step-hit">命中 <a href="https://bgm.tv/subject/${step.subject_id}" target="_blank">${step.subject_id}</a></div>`;
            }

            if (step.candidates && step.candidates.length > 0) {
                html += renderMatchCandidatesTable(step.candidates, scoreText);
            }

            html += '</div></li>';
        });

        html += '</ol>';
    } else if (!trace) {
        html += `
            <p class="record-detail-empty-hint mb-0">
                无匹配追踪数据（可能为旧版记录），可在
                <a href="${appUrl('/debug')}">调试工具</a> 中测试匹配。
            </p>
        `;
    } else {
        html += '<p class="record-detail-empty-hint mb-0">匹配追踪为空，无步骤数据。</p>';
    }

    return html;
}

function renderSyncDetailContent(record, trace) {
    const isMovie = (record.media_type || 'episode').toLowerCase() === 'movie';
    const facts = [];

    if (hasDisplayText(record.title)) {
        facts.push({ label: '接收标题', value: escapeHtml(record.title), wide: true });
    }
    if (hasDisplayText(record.ori_title)) {
        facts.push({ label: '接收原标题', value: escapeHtml(record.ori_title), wide: true });
    }
    if (hasDisplayText(record.user_name)) {
        facts.push({ label: '用户名', value: escapeHtml(record.user_name) });
    }
    if (record.media_type) {
        facts.push({ label: '媒体类型', value: renderMediaTypeBadge(record.media_type) });
    }
    facts.push({
        label: '季 / 集',
        value: isMovie ? '<span class="record-detail-chip">剧场版</span>' : getRecordEpisodeLabel(record),
    });

    const releaseDate = getRecordReleaseDate(record, trace);
    if (releaseDate) {
        facts.push({ label: '播出日期', value: escapeHtml(releaseDate) });
    }

    const body = renderRecordDetailFacts(facts) || '<p class="record-detail-empty-hint mb-0">无接收信息</p>';

    return renderRecordDetailZone(
        'receive',
        '接收信息',
        '媒体库推送的观看记录',
        body,
        'record-section-receive',
    );
}

function renderMatchTraceLoading() {
    return renderRecordDetailZone(
        'match',
        '匹配信息',
        '用于 Bangumi 条目识别的输入',
        `<div class="record-detail-loading" aria-busy="true">
            <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
            <span>加载匹配信息…</span>
        </div>`,
        'record-section-match',
    );
}

function renderMatchStepsLoading() {
    return renderRecordDetailZone(
        'steps',
        '匹配步骤',
        '各阶段匹配过程与候选结果',
        `<div class="record-detail-loading" aria-busy="true">
            <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
            <span>加载匹配步骤…</span>
        </div>`,
        'record-section-steps',
    );
}

// ========== 同步记录详情弹窗（records / dashboard 共用） ==========

let _recordDetailModal = null;
const _matchTraceCache = {};

function getRecordDetailModal() {
    const modalEl = document.getElementById('recordDetailModal');
    if (!modalEl) {
        return null;
    }
    if (!_recordDetailModal) {
        _recordDetailModal = new bootstrap.Modal(modalEl);
    }
    return _recordDetailModal;
}

async function loadMatchTraceContent(recordId, record) {
    const summaryContent = document.getElementById('record-summary-content');
    const pipelineContent = document.getElementById('record-pipeline-content');
    if (!summaryContent || !pipelineContent) {
        return;
    }

    summaryContent.innerHTML = renderPipelineSummary(record, parseRecordMatchTrace(record));
    pipelineContent.innerHTML = renderMatchStepsLoading();

    try {
        let traceData = _matchTraceCache[recordId];
        if (!traceData) {
            const response = await fetch(appUrl(`/api/match-records/${recordId}/trace`), {
                credentials: 'include',
            });
            const data = await response.json();
            if (data.status !== 'success') {
                throw new Error('获取匹配详情失败');
            }
            traceData = data.data;
            _matchTraceCache[recordId] = traceData;
        }

        const traceRecord = traceData.record || record;
        const trace = traceData.trace;
        summaryContent.innerHTML = renderPipelineSummary(traceRecord, trace);
        pipelineContent.innerHTML = renderPipelineHtml(traceRecord, trace);
    } catch (error) {
        console.error('加载匹配过程失败:', error);
        summaryContent.innerHTML = '';
        pipelineContent.innerHTML = '<p class="record-detail-empty-hint record-detail-empty-hint--error mb-0">加载匹配流水线失败</p>';
    }
}

async function showRecordDetail(recordId, options) {
    const opts = typeof options === 'string'
        ? { scrollToMatch: options === 'match' }
        : (options || {});

    const modal = getRecordDetailModal();
    if (!modal) {
        showAlert('详情弹窗不可用', 'danger');
        return;
    }

    const summaryContent = document.getElementById('record-summary-content');
    const pipelineContent = document.getElementById('record-pipeline-content');
    if (!summaryContent || !pipelineContent) {
        showAlert('详情弹窗不可用', 'danger');
        return;
    }

    try {
        const response = await fetch(appUrl(`/api/records/${recordId}`), {
            method: 'GET',
            credentials: 'include',
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const result = await response.json();
        if (result.status !== 'success' || !result.data) {
            throw new Error('获取记录数据失败');
        }

        const record = result.data;
        delete _matchTraceCache[recordId];

        const embeddedTrace = parseRecordMatchTrace(record);
        updateRecordDetailModalChrome(record);
        summaryContent.innerHTML = renderPipelineSummary(record, embeddedTrace);
        pipelineContent.innerHTML = renderMatchStepsLoading();
        modal.show();
        await loadMatchTraceContent(recordId, record);

        if (opts.scrollToMatch) {
            const pipelineSection = document.getElementById('record-pipeline-content');
            if (pipelineSection) {
                pipelineSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    } catch (error) {
        console.error('显示记录详情失败:', error);
        showAlert('显示记录详情失败', 'danger');
    }
}

function renderCandidateStatusBadge(status) {
    const badges = {
        pending: ['warning', '待确认'],
        confirmed: ['success', '已确认'],
        rejected: ['secondary', '已忽略'],
    };
    const [color, text] = badges[status] || ['secondary', status];
    return `<span class="badge rounded-pill bg-${color}">${text}</span>`;
}

function renderMediaTypeBadge(mediaType) {
    const mt = (mediaType || 'episode').toLowerCase();
    const label = mt === 'movie' ? '电影' : '剧集';
    return `<span class="badge rounded-pill bg-dark bg-opacity-75">${label}</span>`;
}

function getSourceColor(source) {
    const sourceLower = (source || '').toLowerCase();
    if (sourceLower.startsWith('retry-')) return 'purple';
    switch (sourceLower) {
        case 'plex': return 'warning';
        case 'emby': return 'success';
        case 'jellyfin': return 'primary';
        case 'custom': return 'secondary';
        case 'feiniu': return 'info';
        case 'fongmi': return 'primary';
        case 'test': return 'secondary';
        case 'trakt': return 'danger';
        default: return 'secondary';
    }
}

function getSourceTlClass(source) {
    const s = (source || '').toLowerCase();
    if (s.startsWith('retry-')) return 'retry';
    if (['plex', 'emby', 'jellyfin', 'custom', 'feiniu', 'fongmi', 'test', 'trakt'].indexOf(s) !== -1) return s;
    return 'custom';
}

function renderSourceBadge(source) {
    const label = source || '-';
    return `<span class="badge rounded-pill tl-source--${getSourceTlClass(source)}">${label}</span>`;
}

function createAppEmptyStateHtml(title, subtitle) {
    let html = `<div class="app-empty-state"><i class="bi bi-inbox app-empty-state__icon"></i><div>${title}</div>`;
    if (subtitle) {
        html += `<div class="text-muted small mt-1">${subtitle}</div>`;
    }
    html += '</div>';
    return html;
}

function setAppTableLoading(show, wrapId, loadingId = 'loading') {
    const loading = document.getElementById(loadingId);
    const tableWrap = document.getElementById(wrapId);
    if (!loading || !tableWrap) return;

    if (show) {
        loading.classList.remove('is-hidden');
        tableWrap.classList.add('app-table-wrap--loading');
    } else {
        loading.classList.add('is-hidden');
        tableWrap.classList.remove('app-table-wrap--loading');
    }
}

window.getSyncRecordStatusColor = getSyncRecordStatusColor;
window.getSyncRecordStatusText = getSyncRecordStatusText;
window.getStatusColor = getSyncRecordStatusColor;
window.getStatusText = getSyncRecordStatusText;
window.renderSyncStatusBadge = renderSyncStatusBadge;
window.renderMatchMethodBadge = renderMatchMethodBadge;
window.escapeHtml = escapeHtml;
window.renderMatchTraceDetail = renderMatchTraceDetail;
window.renderSyncDetailContent = renderSyncDetailContent;
window.renderSyncResultContent = renderSyncResultContent;
window.showRecordDetail = showRecordDetail;
window.renderCandidateStatusBadge = renderCandidateStatusBadge;
window.renderMediaTypeBadge = renderMediaTypeBadge;
window.getSourceColor = getSourceColor;
window.getSourceTlClass = getSourceTlClass;
window.renderSourceBadge = renderSourceBadge;
window.createAppEmptyStateHtml = createAppEmptyStateHtml;
window.setAppTableLoading = setAppTableLoading;

function bindAppTableMobileRowClick(tableSelector, onRowClick) {
    const tbody = document.querySelector(`${tableSelector} tbody`);
    if (!tbody) return;

    tbody.addEventListener('click', function(e) {
        if (!window.matchMedia('(max-width: 991.98px)').matches) return;
        if (e.target.closest('a, button')) return;
        const row = e.target.closest('tr[data-record-id]');
        if (!row) return;
        const recordId = parseInt(row.dataset.recordId, 10);
        if (!isNaN(recordId) && recordId > 0) {
            onRowClick(recordId);
        }
    });
}

window.bindAppTableMobileRowClick = bindAppTableMobileRowClick;

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 复制到剪贴板
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        showAlert('已复制到剪贴板', 'success', 2000);
    } catch (err) {
        // 备用方法
        const textArea = document.createElement('textarea');
        textArea.value = text;
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
            document.execCommand('copy');
            showAlert('已复制到剪贴板', 'success', 2000);
        } catch (err) {
            showAlert('复制失败', 'danger');
        }
        document.body.removeChild(textArea);
    }
}

// 确认对话框（保持向后兼容）
function confirmAction(message, callback) {
    if (confirm(message)) {
        if (callback && typeof callback === 'function') {
            callback();
        }
    }
}

// 防抖函数
function debounce(func, wait, immediate) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            timeout = null;
            if (!immediate) func(...args);
        };
        const callNow = immediate && !timeout;
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
        if (callNow) func(...args);
    };
}

// 节流函数
function throttle(func, limit) {
    let inThrottle;
    return function() {
        const args = arguments;
        const context = this;
        if (!inThrottle) {
            func.apply(context, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

// 加载状态管理
class LoadingManager {
    constructor() {
        this.loadingCount = 0;
        this.loadingElement = null;
    }
    
    show(element = null) {
        this.loadingCount++;
        if (element) {
            this.loadingElement = element;
            element.style.display = 'block';
        }
        document.body.style.cursor = 'wait';
    }
    
    hide(element = null) {
        this.loadingCount = Math.max(0, this.loadingCount - 1);
        if (this.loadingCount === 0) {
            document.body.style.cursor = 'default';
            if (element) {
                element.style.display = 'none';
            } else if (this.loadingElement) {
                this.loadingElement.style.display = 'none';
                this.loadingElement = null;
            }
        }
    }
}

const loadingManager = new LoadingManager();

// HTTP请求封装
class ApiClient {
    constructor(baseURL = '') {
        this.baseURL = baseURL;
    }
    
    async request(url, options = {}) {
        const config = {
            credentials: 'include',  // 默认包含Cookie认证信息
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        };
        
        try {
            loadingManager.show();
            const response = await fetch(this.baseURL + url, config);
            
            // 处理认证失败
            if (response.status === 401) {
                // 跳转到登录页面
                window.location.href = appUrl('/login');
                return;
            }
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            return data;
        } catch (error) {
            console.error('API request failed:', error);
            throw error;
        } finally {
            loadingManager.hide();
        }
    }
    
    async get(url, params = {}) {
        const queryString = new URLSearchParams(params).toString();
        const fullUrl = queryString ? `${url}?${queryString}` : url;
        return this.request(fullUrl);
    }
    
    async post(url, data = {}) {
        return this.request(url, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }
    
    async put(url, data = {}) {
        return this.request(url, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }
    
    async delete(url) {
        return this.request(url, {
            method: 'DELETE'
        });
    }
}

const api = new ApiClient(
    typeof window.__APP_BASE_PATH__ === 'string' ? window.__APP_BASE_PATH__ : ''
);

// 表单验证
class FormValidator {
    constructor(form) {
        this.form = form;
        this.errors = {};
    }
    
    addRule(fieldName, rule, message) {
        if (!this.rules) this.rules = {};
        if (!this.rules[fieldName]) this.rules[fieldName] = [];
        this.rules[fieldName].push({ rule, message });
        return this;
    }
    
    validate() {
        this.errors = {};
        const formData = new FormData(this.form);
        
        for (const [fieldName, rules] of Object.entries(this.rules || {})) {
            const value = formData.get(fieldName);
            
            for (const { rule, message } of rules) {
                if (!rule(value)) {
                    if (!this.errors[fieldName]) this.errors[fieldName] = [];
                    this.errors[fieldName].push(message);
                }
            }
        }
        
        this.displayErrors();
        return Object.keys(this.errors).length === 0;
    }
    
    displayErrors() {
        // 清除之前的错误
        this.form.querySelectorAll('.is-invalid').forEach(el => {
            el.classList.remove('is-invalid');
        });
        this.form.querySelectorAll('.invalid-feedback').forEach(el => {
            el.remove();
        });
        
        // 显示新错误
        for (const [fieldName, messages] of Object.entries(this.errors)) {
            const field = this.form.querySelector(`[name="${fieldName}"]`);
            if (field) {
                field.classList.add('is-invalid');
                
                const feedback = document.createElement('div');
                feedback.className = 'invalid-feedback';
                feedback.textContent = messages[0];
                field.parentNode.appendChild(feedback);
            }
        }
    }
}

// 常用验证规则
const ValidationRules = {
    required: (value) => value && value.trim() !== '',
    email: (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value),
    url: (value) => {
        try {
            new URL(value);
            return true;
        } catch {
            return false;
        }
    },
    number: (value) => !isNaN(value) && isFinite(value),
    integer: (value) => Number.isInteger(Number(value)),
    positive: (value) => Number(value) > 0,
    minLength: (min) => (value) => value && value.length >= min,
    maxLength: (max) => (value) => !value || value.length <= max,
    pattern: (regex) => (value) => !value || regex.test(value)
};

// 本地存储管理
class StorageManager {
    static set(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (error) {
            console.error('Failed to save to localStorage:', error);
        }
    }
    
    static get(key, defaultValue = null) {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : defaultValue;
        } catch (error) {
            console.error('Failed to read from localStorage:', error);
            return defaultValue;
        }
    }
    
    static remove(key) {
        try {
            localStorage.removeItem(key);
        } catch (error) {
            console.error('Failed to remove from localStorage:', error);
        }
    }
    
    static clear() {
        try {
            localStorage.clear();
        } catch (error) {
            console.error('Failed to clear localStorage:', error);
        }
    }
}

// 页面初始化
document.addEventListener('DOMContentLoaded', function() {
    // 初始化所有提示工具
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // 初始化所有弹出框
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });
    
    // 自动关闭提示消息
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        if (alert.classList.contains('auto-dismiss')) {
            setTimeout(() => {
                alert.classList.remove('show');
                setTimeout(() => {
                    if (alert.parentNode) {
                        alert.parentNode.removeChild(alert);
                    }
                }, 150);
            }, 5000);
        }
    });

    // 加载 Webhook 配置列表
    if (typeof loadWebhookConfigs === 'function') {
        loadWebhookConfigs();
    }
});

// 认证相关功能
async function logout() {
    try {
        const result = await confirmAction('确定要登出吗？', async () => {
            const response = await fetch(appUrl('/api/logout'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            if (response.ok) {
                if (typeof window.clearBgmReleaseInfoCache === 'function') {
                    window.clearBgmReleaseInfoCache();
                }
                showAlert('登出成功', 'success', 2000);
                // 延迟跳转到登录页面
                setTimeout(() => {
                    window.location.href = appUrl('/login');
                }, 1000);
            } else {
                throw new Error('登出失败');
            }
        });
    } catch (error) {
        showAlert('登出失败: ' + error.message, 'danger');
    }
}

// 异步确认对话框
async function confirmAction(message, callback) {
    return new Promise((resolve) => {
        if (confirm(message)) {
            Promise.resolve(callback()).then(resolve).catch((error) => {
                console.error('Callback error:', error);
                resolve(false);
            });
        } else {
            resolve(false);
        }
    });
}

// 检查认证状态
async function checkAuthStatus() {
    try {
        const response = await fetch(appUrl('/api/auth/status'));
        const result = await response.json();
        
        if (result.status === 'success' && result.data) {
            return result.data;
        }
        return { authenticated: false };
    } catch (error) {
        console.error('检查认证状态失败:', error);
        return { authenticated: false };
    }
}

// 页面认证检查
async function initAuth() {
    const authStatus = await checkAuthStatus();
    
    // 如果未认证且不在登录页面，跳转到登录页面
    if (!authStatus.authenticated && !window.location.pathname.includes('/login')) {
        window.location.href = appUrl('/login');
        return false;
    }
    
    return true;
}

// 导出全局函数
window.showAlert = showAlert;
window.formatDate = formatDate;
window.formatFileSize = formatFileSize;
window.copyToClipboard = copyToClipboard;
window.confirmAction = confirmAction;
window.debounce = debounce;
window.throttle = throttle;
window.loadingManager = loadingManager;
window.api = api;
window.FormValidator = FormValidator;
window.ValidationRules = ValidationRules;
window.StorageManager = StorageManager;
window.logout = logout;
window.checkAuthStatus = checkAuthStatus;
window.initAuth = initAuth;
window.appUrl = appUrl;

// ========== 登录页面专用功能 ==========

// 登录页面初始化
function initLoginPage() {
    // 聚焦到用户名输入框
    const usernameInput = document.getElementById('username');
    if (usernameInput) {
        usernameInput.focus();
    }
    
    // 登录表单处理
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', handleLoginSubmit);
    }
    
    // 回车键快速登录
    document.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            const loginForm = document.getElementById('loginForm');
            if (loginForm && document.activeElement && loginForm.contains(document.activeElement)) {
                e.preventDefault();
                loginForm.dispatchEvent(new Event('submit'));
            }
        }
    });
}

// 处理登录表单提交
async function handleLoginSubmit(e) {
    e.preventDefault();
    
    const loginBtn = document.getElementById('loginBtn');
    const loginText = loginBtn.querySelector('.login-text');
    const loadingSpinner = loginBtn.querySelector('.login-btn-status');
    const alertContainer = document.getElementById('alert-container');
    
    // 显示加载状态
    loginBtn.disabled = true;
    loginBtn.classList.add('login-btn-loading');
    
    // 清除之前的错误信息
    alertContainer.innerHTML = '';
    
    const formData = new FormData(e.target);
    const data = {
        username: formData.get('username'),
        password: formData.get('password')
    };
    
    try {
        const response = await fetch(appUrl('/api/login'), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(data)
        });
        
        const result = await response.json();
        
        if (response.ok && result.status === 'success') {
            // 登录成功，显示成功信息并跳转
            alertContainer.innerHTML = `
                <div class="alert alert-success">
                    <i class="bi bi-check-circle"></i> 登录成功，正在跳转...
                </div>
            `;
            
            // 延迟跳转以显示成功信息
            const params = new URLSearchParams(window.location.search);
            const next = params.get('next');
            // 只允许站内路径跳转，防止开放重定向
            const target = next && next.startsWith('/') && !next.startsWith('//')
                ? next : '/dashboard';
            setTimeout(() => {
                window.location.href = appUrl(target);
            }, 1000);
        } else {
            // 登录失败
            throw new Error(result.message || '登录失败');
        }
    } catch (error) {
        // 显示错误信息
        alertContainer.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle"></i> ${error.message}
            </div>
        `;
        
        // 恢复按钮状态
        loginBtn.disabled = false;
        loginBtn.classList.remove('login-btn-loading');
        
        // 清空密码字段
        const passwordInput = document.getElementById('password');
        if (passwordInput) {
            passwordInput.value = '';
            passwordInput.focus();
        }
    }
}

// 在页面加载时检查是否为登录页面
document.addEventListener('DOMContentLoaded', function() {
    // 如果是登录页面，初始化登录功能
    if (window.location.pathname.includes('/login') || document.getElementById('loginForm')) {
        initLoginPage();
    }
});

// 导出登录相关函数
window.initLoginPage = initLoginPage;
window.handleLoginSubmit = handleLoginSubmit;

// ========== 同步重试功能（SSE 流式日志） ==========

let _retryEventSource = null;
let _retryLogModal = null;
let _retryDone = false;

/**
 * 重试同步记录（打开日志弹窗，SSE 实时推送 debug 日志）
 * @param {number} recordId - 记录ID
 */
async function retrySync(recordId) {
    if (!recordId) {
        showAlert('记录ID无效', 'danger');
        return;
    }

    // 确认重试
    if (!confirm('确定要重试此同步记录吗？')) {
        return;
    }

    // 初始化弹窗（懒加载）
    if (!_retryLogModal) {
        const modalEl = document.getElementById('retryLogModal');
        if (!modalEl) {
            showAlert('日志弹窗元素不存在', 'danger');
            return;
        }
        _retryLogModal = new bootstrap.Modal(modalEl);
        // 弹窗关闭时停止 SSE
        modalEl.addEventListener('hidden.bs.modal', function () {
            stopRetrySSE();
        });
    }

    // 重置弹窗状态
    _retryDone = false;
    const logContent = document.getElementById('retry-log-content');
    const statusBadge = document.getElementById('retry-log-status');
    if (logContent) logContent.innerHTML = '';
    if (statusBadge) {
        statusBadge.innerHTML = '<span class="badge bg-info">重试中...</span>';
    }

    // 显示弹窗
    _retryLogModal.show();

    // 启动 SSE
    startRetrySSE(recordId);
}

function startRetrySSE(recordId) {
    stopRetrySSE();

    const url = appUrl(`/api/records/${recordId}/retry/stream`);
    _retryEventSource = new EventSource(url);
    _retryEventSource.withCredentials = true;

    _retryEventSource.addEventListener('start', function (e) {
        const data = JSON.parse(e.data);
        appendRetryLog(`▶ 开始重试：${data.title} S${String(data.season).padStart(2, '0')}E${String(data.episode).padStart(2, '0')}（来源：${data.source}）`, 'info');
    });

    _retryEventSource.addEventListener('log', function (e) {
        const data = JSON.parse(e.data);
        appendRetryLog(data.line, data.level.toLowerCase());
    });

    _retryEventSource.addEventListener('done', function (e) {
        const data = JSON.parse(e.data);
        const statusBadge = document.getElementById('retry-log-status');
        _retryDone = true;

        if (data.status === 'success') {
            appendRetryLog(`✅ 重试成功：${data.message}`, 'success');
            if (statusBadge) statusBadge.innerHTML = '<span class="badge bg-success">重试成功</span>';
        } else if (data.status === 'ignored') {
            appendRetryLog(`⚠️ 重试被忽略：${data.message}`, 'warning');
            if (statusBadge) statusBadge.innerHTML = '<span class="badge bg-warning">被忽略</span>';
        } else {
            appendRetryLog(`❌ 重试失败：${data.message}`, 'error');
            if (statusBadge) statusBadge.innerHTML = '<span class="badge bg-danger">重试失败</span>';
        }

        stopRetrySSE();
        refreshAfterRetry();
    });

    _retryEventSource.addEventListener('error', function (e) {
        if (_retryDone) return;
        if (e.data) {
            try {
                const data = JSON.parse(e.data);
                appendRetryLog(`❌ ${data.message}`, 'error');
            } catch (_) {
                appendRetryLog('❌ 连接异常断开', 'error');
            }
        }
        stopRetrySSE();
        const statusBadge = document.getElementById('retry-log-status');
        if (statusBadge) statusBadge.innerHTML = '<span class="badge bg-danger">连接异常</span>';
    });

    _retryEventSource.onerror = function () {
        // EventSource 自动重连会触发 onerror，已在 done 时标记 _retryDone 跳过
        if (_retryDone) return;
        // 连接异常时停止自动重连
        stopRetrySSE();
        const statusBadge = document.getElementById('retry-log-status');
        if (statusBadge) statusBadge.innerHTML = '<span class="badge bg-danger">连接异常</span>';
        appendRetryLog('❌ SSE 连接异常断开', 'error');
    };
}

function stopRetrySSE() {
    if (_retryEventSource) {
        _retryEventSource.close();
        _retryEventSource = null;
    }
}

function appendRetryLog(line, level) {
    const logContent = document.getElementById('retry-log-content');
    if (!logContent) return;

    const lineDiv = document.createElement('div');
    // 按级别着色
    const colorClass = {
        debug: 'text-secondary',
        info: 'text-info',
        warning: 'text-warning',
        error: 'text-danger',
        success: 'text-success'
    }[level] || 'text-light';
    lineDiv.className = colorClass;
    lineDiv.textContent = line;
    logContent.appendChild(lineDiv);

    // 自动滚动到底部
    logContent.scrollTop = logContent.scrollHeight;
}

function refreshAfterRetry() {
    setTimeout(() => {
        if (typeof loadDashboardData === 'function') {
            loadDashboardData();
        } else if (typeof loadRecords === 'function') {
            loadRecords(currentPage, currentLimit);
        } else {
            location.reload();
        }
    }, 2000);
}

// 导出重试功能
window.retrySync = retrySync;

// ========== 数据表格（分页条数与入场动画，与同步记录一致） ==========

const APP_TABLE_PAGE_SIZE = 10;
window.APP_TABLE_PAGE_SIZE = APP_TABLE_PAGE_SIZE;

function replayAppTableAnimation(wrapId) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;
    wrap.classList.remove('records-table-wrap--enter');
    void wrap.offsetWidth;
    wrap.classList.add('records-table-wrap--enter');
}

function applyAppTableRowEnter(row, index) {
    row.classList.add('records-table-row--enter');
    row.style.animationDelay = `${Math.min(index * 0.04, 0.36)}s`;
}

function animateAppTableBody(tbody, wrapId) {
    if (!tbody) return;
    tbody.querySelectorAll('tr').forEach((row, index) => applyAppTableRowEnter(row, index));
    replayAppTableAnimation(wrapId);
}

window.replayAppTableAnimation = replayAppTableAnimation;
window.applyAppTableRowEnter = applyAppTableRowEnter;
window.animateAppTableBody = animateAppTableBody;

// ========== 通用分页（与同步记录 app-pagination 一致） ==========

function createAppPageItem(page, currentPage, onPageChange) {
    const li = document.createElement('li');
    li.className = `page-item page-item--num ${page === currentPage ? 'active' : ''}`;
    const link = document.createElement('a');
    link.className = 'page-link';
    link.href = '#';
    link.setAttribute('aria-label', `第 ${page} 页`);
    link.setAttribute('aria-current', page === currentPage ? 'page' : 'false');
    link.textContent = String(page);
    link.addEventListener('click', (e) => {
        e.preventDefault();
        if (page !== currentPage) onPageChange(page);
    });
    li.appendChild(link);
    return li;
}

function createAppEllipsisItem() {
    const li = document.createElement('li');
    li.className = 'page-item page-item--ellipsis disabled';
    li.innerHTML = '<span class="page-link" aria-hidden="true">…</span>';
    return li;
}

function replayAppPaginationAnimation(navId) {
    const nav = document.getElementById(navId);
    if (!nav || nav.classList.contains('is-hidden')) return;
    nav.classList.remove('records-pagination--enter');
    void nav.offsetWidth;
    nav.classList.add('records-pagination--enter');
}

function renderAppPagination(options) {
    const {
        total,
        currentPage,
        limit,
        navId = 'pagination-nav',
        listId = 'pagination',
        summaryId = 'pagination-summary',
        onPageChange,
        animate = true,
    } = options;

    const pagination = document.getElementById(listId);
    if (!pagination) return;

    const summary = document.getElementById(summaryId);
    const nav = document.getElementById(navId);
    const totalPages = Math.ceil(total / limit);

    pagination.innerHTML = '';

    if (total <= 0) {
        if (summary) summary.textContent = '';
        if (nav) nav.classList.add('is-hidden');
        return;
    }

    if (summary) {
        summary.textContent = totalPages <= 1
            ? `共 ${total} 条记录`
            : `第 ${currentPage} / ${totalPages} 页，共 ${total} 条`;
    }

    if (totalPages <= 1) {
        if (nav) nav.classList.remove('is-hidden');
        if (animate) replayAppPaginationAnimation(navId);
        return;
    }

    if (nav) nav.classList.remove('is-hidden');

    const prevLi = document.createElement('li');
    prevLi.className = `page-item page-item--nav ${currentPage === 1 ? 'disabled' : ''}`;
    const prevLink = document.createElement('a');
    prevLink.className = 'page-link';
    prevLink.href = '#';
    prevLink.setAttribute('aria-label', '上一页');
    prevLink.innerHTML = '<i class="bi bi-chevron-left" aria-hidden="true"></i><span class="d-none d-sm-inline">上一页</span>';
    prevLink.addEventListener('click', (e) => {
        e.preventDefault();
        if (currentPage > 1) onPageChange(currentPage - 1);
    });
    prevLi.appendChild(prevLink);
    pagination.appendChild(prevLi);

    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, currentPage + 2);

    if (startPage > 1) {
        pagination.appendChild(createAppPageItem(1, currentPage, onPageChange));
        if (startPage > 2) {
            pagination.appendChild(createAppEllipsisItem());
        }
    }

    for (let i = startPage; i <= endPage; i++) {
        pagination.appendChild(createAppPageItem(i, currentPage, onPageChange));
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            pagination.appendChild(createAppEllipsisItem());
        }
        pagination.appendChild(createAppPageItem(totalPages, currentPage, onPageChange));
    }

    const nextLi = document.createElement('li');
    nextLi.className = `page-item page-item--nav ${currentPage === totalPages ? 'disabled' : ''}`;
    const nextLink = document.createElement('a');
    nextLink.className = 'page-link';
    nextLink.href = '#';
    nextLink.setAttribute('aria-label', '下一页');
    nextLink.innerHTML = '<span class="d-none d-sm-inline">下一页</span><i class="bi bi-chevron-right" aria-hidden="true"></i>';
    nextLink.addEventListener('click', (e) => {
        e.preventDefault();
        if (currentPage < totalPages) onPageChange(currentPage + 1);
    });
    nextLi.appendChild(nextLink);
    pagination.appendChild(nextLi);

    if (animate) replayAppPaginationAnimation(navId);
}

window.renderAppPagination = renderAppPagination;

// ========== 主题管理器 ==========

class ThemeManager {
    constructor() {
        this.THEME_KEY = 'app-theme';
        this.COLOR_KEY = 'app-color';
        this.COLORS = {
            sakura:    '#f09199',
            amber:     '#f78c50',
            sakuragi:  '#e9485e',
            sanae:     '#8cb48c',
            hatsune:   '#39C5BB',
            tianyi:    '#00a2ff',
            violet:    '#a682e6'
        };
        this._deprecatedColors = { eggyolk: 'sakura', sapphire: 'sakura' };
        this._listeners = [];
        this._systemDark = window.matchMedia('(prefers-color-scheme: dark)');
        var self = this;
        this._migrateDeprecatedColor();
        this._systemDark.addEventListener('change', function() {
            if (!localStorage.getItem(self.THEME_KEY)) {
                self._apply(self._systemDark.matches ? 'dark' : 'light');
            }
        });
    }

    _migrateDeprecatedColor() {
        var stored = localStorage.getItem(this.COLOR_KEY);
        if (!stored) return;
        var migrated = this._deprecatedColors[stored];
        if (migrated) {
            localStorage.setItem(this.COLOR_KEY, migrated);
            document.documentElement.setAttribute('data-color', migrated);
            return;
        }
        if (!this.COLORS[stored]) {
            localStorage.setItem(this.COLOR_KEY, 'sakura');
            document.documentElement.setAttribute('data-color', 'sakura');
        }
    }

    /** 实际生效的主题（含系统跟随） */
    getTheme() {
        var stored = localStorage.getItem(this.THEME_KEY);
        if (stored === 'light' || stored === 'dark') return stored;
        return this._systemDark.matches ? 'dark' : 'light';
    }

    /** 是否跟随系统 */
    isSystem() {
        return !localStorage.getItem(this.THEME_KEY);
    }

    /** 恢复跟随系统 */
    followSystem() {
        localStorage.removeItem(this.THEME_KEY);
        this._apply(this._systemDark.matches ? 'dark' : 'light');
    }

    _apply(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        this._notify();
    }

    setTheme(theme) {
        localStorage.setItem(this.THEME_KEY, theme);
        this._apply(theme);
    }

    toggleTheme(triggerEl) {
        const newTheme = this.getTheme() === 'light' ? 'dark' : 'light';
        const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (prefersReduced || !triggerEl) {
            this.setTheme(newTheme);
            return;
        }
        this._animateThemeSwitch(triggerEl, newTheme);
    }

    _animateThemeSwitch(triggerEl, newTheme) {
        var self = this;
        var x, y;

        if (triggerEl) {
            var rect = triggerEl.getBoundingClientRect();
            x = rect.left + rect.width / 2;
            y = rect.top + rect.height / 2;
        } else {
            x = window.innerWidth / 2;
            y = window.innerHeight / 2;
        }

        var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        // 优先使用 View Transitions API（Chrome 111+）
        if (!prefersReduced && 'startViewTransition' in document) {
            var radius = Math.hypot(
                Math.max(x, window.innerWidth - x),
                Math.max(y, window.innerHeight - y)
            );
            var clipFrom = 'circle(0px at ' + x + 'px ' + y + 'px)';
            var clipTo = 'circle(' + radius + 'px at ' + x + 'px ' + y + 'px)';

            var transition = document.startViewTransition(function() {
                self.setTheme(newTheme);
            });

            transition.ready.then(function() {
                // 始终对 ::view-transition-new(root) 做扩散动画
                var target = document.documentElement.animate(
                    { clipPath: [clipFrom, clipTo] },
                    { duration: 700, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards', pseudoElement: '::view-transition-new(root)' }
                );
                target.finished.then(function() {
                    document.documentElement.getAnimations({ subtree: true }).forEach(function(a) { a.cancel(); });
                });
            });
            return;
        }

        // 回退：手动 overlay 动画
        var oldTheme = self.getTheme();
        document.documentElement.setAttribute('data-theme', newTheme);
        var newBg = getComputedStyle(document.body).backgroundColor;
        document.documentElement.setAttribute('data-theme', oldTheme);

        var overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;pointer-events:none;background:' + newBg;
        document.body.appendChild(overlay);

        var maxR = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));

        var anim = overlay.animate([
            { clipPath: 'circle(0px at ' + x + 'px ' + y + 'px)' },
            { clipPath: 'circle(' + maxR + 'px at ' + x + 'px ' + y + 'px)' }
        ], { duration: 700, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards' });

        anim.onfinish = function() {
            self.setTheme(newTheme);
            overlay.remove();
        };
    }

    getColor() {
        return localStorage.getItem(this.COLOR_KEY) || 'sakura';
    }

    setColor(color) {
        if (!this.COLORS[color]) return;
        localStorage.setItem(this.COLOR_KEY, color);
        document.documentElement.setAttribute('data-color', color);
        this._notify();
    }

    setColorAnimated(color, triggerEl) {
        if (!this.COLORS[color]) return;
        if (this.getColor() === color) return;
        var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (prefersReduced || !triggerEl) { this.setColor(color); return; }

        var self = this;
        var rect = triggerEl.getBoundingClientRect();
        var x = rect.left + rect.width / 2;
        var y = rect.top + rect.height / 2;

        if ('startViewTransition' in document) {
            var radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
            var transition = document.startViewTransition(function() { self.setColor(color); });
            transition.ready.then(function() {
                document.documentElement.animate(
                    { clipPath: ['circle(0px at ' + x + 'px ' + y + 'px)', 'circle(' + radius + 'px at ' + x + 'px ' + y + 'px)'] },
                    { duration: 700, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards', pseudoElement: '::view-transition-new(root)' }
                ).finished.then(function() {
                    document.documentElement.getAnimations({ subtree: true }).forEach(function(a) { a.cancel(); });
                });
            });
            return;
        }

        // 回退：overlay 扩散动画
        var oldColor = this.getColor();
        this.setColor(color);
        var overlay = document.createElement('div');
        var scrollY = window.scrollY || window.pageYOffset;
        overlay.style.cssText = 'position:absolute;left:0;top:' + scrollY + 'px;width:' + document.documentElement.scrollWidth + 'px;height:' + document.documentElement.scrollHeight + 'px;overflow:hidden;z-index:99999;pointer-events:none;';
        var clone = document.documentElement.cloneNode(true);
        clone.style.position = 'absolute';
        clone.style.left = '0';
        clone.style.top = '0';
        clone.style.width = document.documentElement.scrollWidth + 'px';
        clone.style.height = document.documentElement.scrollHeight + 'px';
        overlay.appendChild(clone);
        document.body.appendChild(overlay);
        self.setColor(oldColor);
        var maxR = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
        overlay.animate([
            { clipPath: 'circle(0px at ' + x + 'px ' + (y + scrollY) + 'px)' },
            { clipPath: 'circle(' + maxR + 'px at ' + x + 'px ' + (y + scrollY) + 'px)' }
        ], { duration: 700, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards' }).onfinish = function() {
            self.setColor(color);
            overlay.remove();
        };
    }

    getPrimaryColor() {
        var css = getComputedStyle(document.documentElement).getPropertyValue('--app-primary').trim();
        if (css) return css;
        return this.COLORS[this.getColor()] || this.COLORS.sakura;
    }

    getColors() {
        return this.COLORS;
    }

    onChange(fn) {
        this._listeners.push(fn);
    }

    _notify() {
        this._listeners.forEach(fn => fn());
    }
}

window.themeManager = new ThemeManager();