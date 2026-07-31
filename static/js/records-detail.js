// 同步记录详情弹窗 —— 接收/匹配/流水线/结果渲染与详情弹窗控制（records / dashboard 共用）

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

// 流水线阶段名映射（11 阶段：archive 短路命中时独立展示）
const PIPELINE_STAGE_NAMES = {
    receive: '接收请求',
    normalize: '标题归一化',
    custom_mapping: '自定义映射',
    bangumi_data: 'bangumi-data 本地匹配',
    archive: '本地归档匹配',
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
        html += '<details class="mt-2 record-detail-raw-payload-details">'
            + '<summary class="small text-muted">驱动原始数据</summary>'
            + `<pre class="record-detail-raw-payload mb-0 mt-1">${escapeHtml(JSON.stringify(step.raw_payload, null, 2))}</pre>`
            + '</details>';
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

// P0: 渲染 step.error_detail 异常详情（可折叠）
function renderStepErrorDetail(detail) {
    if (!detail) return '';
    const type = escapeHtml(detail.type || '');
    const message = escapeHtml(detail.message || '');
    const traceback = detail.traceback || '';
    let html = `<details class="record-detail-error-detail record-detail-subpanel record-detail-subpanel--error mt-1">
        <summary class="record-detail-subpanel__summary record-detail-subpanel__summary--error"><i class="bi bi-bug-fill"></i>异常详情：${type}</summary>
        <div class="mt-1 small">
            <div class="text-danger"><strong>${type}</strong>: ${message}</div>`;
    if (traceback) {
        html += `<pre class="mt-1 mb-0 p-2 bg-dark text-light rounded small" style="max-height:240px;overflow:auto">${escapeHtml(traceback)}</pre>`;
    }
    html += '</div></details>';
    return html;
}

// P1: 渲染 step.request_params 实际发送的搜索参数
function renderStepRequestParams(params) {
    if (!params) return '';
    const mediaTypeLabel = (t) => {
        const map = { episode: '剧集', movie: '剧场版', ova: 'OVA', oad: 'OAD', real_action: '三次元' };
        return map[t] || t || '-';
    };
    const subjectTypes = Array.isArray(params.subject_types)
        ? params.subject_types.join(', ')
        : (params.subject_types || '');
    const items = [
        { icon: 'bi-tag', label: '搜索标题', value: params.title, wide: true },
        { icon: 'bi-translate', label: '原始标题', value: params.ori_title, wide: true },
        { icon: 'bi-calendar3', label: '发布日期', value: params.premiere_date },
        { icon: 'bi-film', label: '媒体类型', value: params.media_type ? mediaTypeLabel(params.media_type) : '' },
        { icon: 'bi-collection-play', label: '季度', value: params.season },
        { icon: 'bi-filter-circle', label: 'subject_types', value: subjectTypes, mono: true },
    ].filter((it) => it.value !== null && it.value !== undefined && it.value !== '');
    if (items.length === 0) return '';
    const cells = items.map((it) => {
        const wideCls = it.wide ? ' record-detail-step__kv--wide' : '';
        const monoCls = it.mono ? ' record-detail-step__kv--mono' : '';
        return `
            <div class="record-detail-step__kv${wideCls}${monoCls}">
                <span class="record-detail-step__kv-label"><i class="bi ${it.icon}"></i>${escapeHtml(it.label)}</span>
                <span class="record-detail-step__kv-value">${escapeHtml(String(it.value))}</span>
            </div>`;
    }).join('');
    return `<details class="record-detail-request-params record-detail-subpanel mt-1">
        <summary class="record-detail-subpanel__summary"><i class="bi bi-arrow-up-right-square"></i>搜索参数</summary>
        <div class="record-detail-step__kv-grid">${cells}</div>
    </details>`;
}

// P1: 渲染 step.api_response_summary API 返回质量摘要
function renderStepApiResponseSummary(summary) {
    if (!summary) return '';
    const total = summary.total_candidates ?? 0;
    const archiveHit = summary.is_archive_hit;
    const firstId = summary.first_subject_id;
    const firstName = summary.first_name_cn || summary.first_name || '';
    const sourceBadge = archiveHit
        ? '<span class="record-detail-step__pill record-detail-step__pill--archive"><i class="bi bi-database-fill"></i>archive 短路</span>'
        : '<span class="record-detail-step__pill record-detail-step__pill--api"><i class="bi bi-cloud-fill"></i>API</span>';
    const totalBadge = `<span class="record-detail-step__pill record-detail-step__pill--count">候选 <strong>${total}</strong></span>`;
    const firstLink = firstId
        ? `<a href="https://bgm.tv/subject/${escapeHtml(String(firstId))}" target="_blank" class="record-detail-step__pill record-detail-step__pill--link"><i class="bi bi-link-45deg"></i>${escapeHtml(firstName || String(firstId))}</a>`
        : '<span class="record-detail-step__pill record-detail-step__pill--muted">无候选</span>';
    return `<details class="record-detail-api-summary record-detail-subpanel mt-1">
        <summary class="record-detail-subpanel__summary"><i class="bi bi-graph-up"></i>API 返回摘要</summary>
        <div class="record-detail-step__pills">${totalBadge}${sourceBadge}${firstLink}</div>
    </details>`;
}

// 流水线渲染：10 阶段统一展示
function renderPipelineHtml(record, trace) {
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

        html += `<article class="record-detail-step record-detail-step--${status}" id="record-step-${idx + 1}" tabindex="-1">`;
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
            html += `<span class="record-detail-step__score">${formatMatchScore(step.score)}</span>`;
        }
        html += `<span class="record-detail-step__time">${step.elapsed_ms || 0}ms</span>`;
        html += '</header>';

        if (step.reason) {
            html += `<p class="record-detail-step__reason">${escapeHtml(step.reason)}</p>`;
        }

        // P0: error_detail 异常详情（status=error 时展示）
        if (step.error_detail) {
            html += renderStepErrorDetail(step.error_detail);
        }

        // P1: request_params 实际发送的搜索参数
        if (step.request_params) {
            html += renderStepRequestParams(step.request_params);
        }

        // P1: api_response_summary API 返回质量摘要
        if (step.api_response_summary) {
            html += renderStepApiResponseSummary(step.api_response_summary);
        }

        // receive step：渲染 sync 开始时的输入字段表格
        if (step.stage === 'receive') {
            html += renderReceiveInputTable(step);
        }

        if (step.candidates && step.candidates.length > 0) {
            html += renderMatchCandidatesTable(step.candidates);
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

    // P2: 步骤耗时汇总（每步耗时 + 总耗时 + 占比条）
    html += renderStepTimings(trace);

    return html;
}

// P2: 渲染步骤耗时汇总面板
function renderStepTimings(trace) {
    if (!trace || !Array.isArray(trace.steps) || trace.steps.length === 0) {
        return '';
    }
    const rows = trace.steps
        .map((s, idx) => ({
            idx: idx + 1,
            stage: s.stage,
            stageName: getPipelineStageName(s.stage),
            status: s.status,
            elapsed: Math.max(0, Number(s.elapsed_ms) || 0),
        }))
        .filter((r) => r.elapsed > 0);

    // 没有任何耗时数据则不展示
    if (rows.length === 0) {
        return '';
    }

    // 优先用 trace.total_elapsed_ms；否则用 steps 求和
    const sumElapsed = rows.reduce((acc, r) => acc + r.elapsed, 0);
    const totalElapsed = (typeof trace.total_elapsed_ms === 'number' && trace.total_elapsed_ms > 0)
        ? trace.total_elapsed_ms
        : sumElapsed;
    const maxElapsed = rows.reduce((acc, r) => Math.max(acc, r.elapsed), 0);
    const baseTotal = Math.max(totalElapsed, maxElapsed, 1);

    const statusLabel = (st) => {
        const m = { hit: '命中', miss: '未命中', skipped: '已跳过', error: '出错' };
        return m[st] || st || '';
    };

    const formatMs = (ms) => {
        if (ms < 1) return '0ms';
        if (ms < 1000) return `${ms}ms`;
        return `${(ms / 1000).toFixed(2)}s`;
    };

    const rowHtml = rows.map((r) => {
        const pct = (r.elapsed / baseTotal) * 100;
        const pctOfTotal = totalElapsed > 0 ? (r.elapsed / totalElapsed) * 100 : 0;
        const isMax = r.elapsed === maxElapsed;
        return `
            <button type="button" class="record-detail-timings__row${isMax ? ' record-detail-timings__row--hot' : ''}" onclick="jumpToRecordStep(${r.idx})" title="跳转到「${escapeHtml(r.stageName)}」">
                <span class="record-detail-timings__idx">${r.idx}</span>
                <span class="record-detail-timings__name">${escapeHtml(r.stageName)}</span>
                <span class="record-detail-timings__status record-detail-timings__status--${r.status}">${escapeHtml(statusLabel(r.status))}</span>
                <span class="record-detail-timings__bar" aria-hidden="true">
                    <span class="record-detail-timings__bar-fill" style="width:${pct.toFixed(1)}%"></span>
                </span>
                <span class="record-detail-timings__elapsed">${formatMs(r.elapsed)}</span>
                <span class="record-detail-timings__pct">${pctOfTotal.toFixed(1)}%</span>
            </button>`;
    }).join('');

    return `
        <section class="record-detail-timings">
            <header class="record-detail-timings__head">
                <h6 class="record-detail-timings__title"><i class="bi bi-stopwatch"></i>步骤耗时</h6>
                <span class="record-detail-timings__total">总执行时间 <strong>${formatMs(totalElapsed)}</strong></span>
            </header>
            <div class="record-detail-timings__rows">${rowHtml}</div>
        </section>
    `;
}

// 耗时表点击跳转到对应步骤并高亮
function jumpToRecordStep(idx) {
    const target = document.getElementById(`record-step-${idx}`);
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.remove('record-detail-step--flash');
    void target.offsetWidth;
    target.classList.add('record-detail-step--flash');
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
        body += `<div class="record-detail-result-status">${renderSyncStatusBadge(record.status)}${renderSyncSubStatusBadge(record)}</div>`;
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
        const showRetry = record.status !== 'success';
        retryBtn.classList.toggle('d-none', !showRetry);
        retryBtn.onclick = function() {
            retrySync(record.id);
        };
    }

    if (helpLink) {
        const showHelp = record.status !== 'success';
        helpLink.classList.toggle('d-none', !showHelp);
        helpLink.classList.toggle('d-inline-flex', showHelp);
    }
}

function renderMatchDetailModalContent(record, trace) {
    const parts = renderMatchDetailModalParts(record, trace);
    return parts.match + parts.steps;
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

// 弹窗头部核心信息 chips（仅展示 modal-title 里没有的字段）
function renderPipelineSummaryChips(record, trace) {
    const t = trace || {};
    const season = t.request_season ?? record.season ?? 1;
    const episode = t.request_episode ?? record.episode ?? 0;
    const mediaType = t.request_media_type || record.media_type || 'episode';
    const isSuccess = record.status === 'success' || record.status === 'retried';
    const statusClass = isSuccess ? 'success' : (record.status === 'error' ? 'error' : 'neutral');
    const statusIcon = isSuccess ? 'bi-check-circle-fill' : (record.status === 'error' ? 'bi-x-circle-fill' : 'bi-dash-circle-fill');
    const statusText = isSuccess ? '同步成功' : (record.status === 'error' ? '同步失败' : (record.status || '未知'));

    const subjectId = record.subject_id || t.final_subject_id;
    const episodeId = record.episode_id || t.final_episode_id;
    const score = (t.final_score !== null && t.final_score !== undefined)
        ? t.final_score
        : record.match_score;
    const bgmTitle = record.bgm_title || '';

    const chips = [];

    // 季 / 集
    const isMovie = (mediaType || 'episode').toLowerCase() === 'movie';
    if (!isMovie) {
        chips.push(`<span class="record-detail-modal__chip record-detail-modal__chip--mono">S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}</span>`);
    }

    // 媒体类型
    chips.push(`<span class="record-detail-modal__chip record-detail-modal__chip--type">${escapeHtml(getMediaTypeLabel(mediaType))}</span>`);

    // 状态
    chips.push(`<span class="record-detail-modal__chip record-detail-modal__chip--status record-detail-modal__chip--status-${statusClass}"><i class="bi ${statusIcon}"></i>${escapeHtml(statusText)}</span>`);

    // subject 链接
    if (subjectId) {
        let label = `subject/${subjectId}`;
        if (bgmTitle) {
            label += ` · ${bgmTitle}`;
        }
        chips.push(`<a href="https://bgm.tv/subject/${escapeHtml(subjectId)}" target="_blank" class="record-detail-modal__chip record-detail-modal__chip--link"><i class="bi bi-collection"></i>${escapeHtml(label)}</a>`);
    }

    // episode 链接
    if (episodeId) {
        chips.push(`<a href="https://bgm.tv/ep/${escapeHtml(episodeId)}" target="_blank" class="record-detail-modal__chip record-detail-modal__chip--link"><i class="bi bi-play-circle"></i>ep/${escapeHtml(episodeId)}</a>`);
    }

    // 置信度
    if (score !== null && score !== undefined && isSuccess) {
        chips.push(`<span class="record-detail-modal__chip record-detail-modal__chip--score">置信度 ${(score * 100).toFixed(0)}%</span>`);
    }

    return `<div class="record-detail-modal__chips">${chips.join('')}</div>`;
}

// 弹窗头部核心信息 chips
function setRecordSummaryHtml(record, trace) {
    const head = document.getElementById('record-detail-modal-summary');
    if (head) {
        head.innerHTML = renderPipelineSummaryChips(record, trace);
    }
}

function clearRecordSummaryHtml() {
    const head = document.getElementById('record-detail-modal-summary');
    if (head) {
        head.innerHTML = '';
    }
}

async function loadMatchTraceContent(recordId, record) {
    const pipelineContent = document.getElementById('record-pipeline-content');
    if (!pipelineContent) {
        return;
    }

    setRecordSummaryHtml(record, parseRecordMatchTrace(record));
    pipelineContent.innerHTML = renderMatchStepsLoading();

    try {
        let traceData = _matchTraceCache[recordId];
        if (!traceData) {
            const data = await apiFetch(`/api/match-records/${recordId}/trace`);
            if (data.status !== 'success') {
                throw new Error('获取匹配详情失败');
            }
            traceData = data.data;
            _matchTraceCache[recordId] = traceData;
        }

        const traceRecord = traceData.record || record;
        const trace = traceData.trace;
        setRecordSummaryHtml(traceRecord, trace);
        pipelineContent.innerHTML = renderPipelineHtml(traceRecord, trace);
    } catch (error) {
        console.error('加载匹配过程失败:', error);
        clearRecordSummaryHtml();
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

    const pipelineContent = document.getElementById('record-pipeline-content');
    if (!pipelineContent) {
        showAlert('详情弹窗不可用', 'danger');
        return;
    }

    try {
        const result = await apiFetch(`/api/records/${recordId}`, { method: 'GET' });

        if (result.status !== 'success' || !result.data) {
            throw new Error('获取记录数据失败');
        }

        const record = result.data;
        delete _matchTraceCache[recordId];

        const embeddedTrace = parseRecordMatchTrace(record);
        updateRecordDetailModalChrome(record);
        setRecordSummaryHtml(record, embeddedTrace);
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

window.renderSyncDetailContent = renderSyncDetailContent;
window.renderSyncResultContent = renderSyncResultContent;
window.showRecordDetail = showRecordDetail;
