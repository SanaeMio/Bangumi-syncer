// 同步记录详情弹窗 —— 总览 / 耗时瀑布 / 步骤时间线渲染与弹窗控制（records / dashboard 共用）

// ========== 耗时判定阈值 ==========
// 单步耗时超过 SLOW_STEP_MS 且占总耗时 SLOW_STEP_SHARE 以上，或超过 HOT_STEP_MS，
// 视为耗时偏高；超过 CRITICAL_STEP_MS 或占比 CRITICAL_STEP_SHARE 视为严重。
const RD_TIMING = {
    WARM_STEP_MS: 500,
    SLOW_STEP_MS: 1000,
    SLOW_STEP_SHARE: 0.3,
    HOT_STEP_MS: 2000,
    CRITICAL_STEP_MS: 3000,
    CRITICAL_STEP_SHARE: 0.6,
    SLOW_TOTAL_MS: 5000,
};

// 流水线阶段名映射（archive 短路命中时独立展示）
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
    // bgm_search 子管线步骤（展示时归入「API 搜索内部流程」分组）
    api_search_reset: '搜索预处理',
    api_search_date_exact: '日期精确搜索',
    api_search_variant_fallback: '变体兜底搜索',
    api_search_finalize: '搜索结果确认',
};

// bgm_search 子管线阶段：在连续出现时折叠为一个分组，避免与主步骤平铺混淆
const SUB_PIPELINE_STAGES = new Set([
    'api_search_reset',
    'api_search_date_exact',
    'api_search_variant_fallback',
    'api_search_finalize',
]);

function isSubPipelineStep(step) {
    return SUB_PIPELINE_STAGES.has(step && step.stage);
}

function getPipelineStageName(stage) {
    return PIPELINE_STAGE_NAMES[stage] || stage;
}

// step 显示名：ArchiveShortcutStep（stage=archive，request_params.source=archive_shortcut）
// 与 APISearchStep 的 archive 覆盖命中区分开，前者是快速预检、后者是归档匹配定选
function getStepDisplayName(step) {
    if (step && step.stage === 'archive'
        && step.request_params && step.request_params.source === 'archive_shortcut') {
        return '归档预检';
    }
    return getPipelineStageName(step ? step.stage : '');
}

// step 状态元信息：文案 + 图标
const STEP_STATUS_META = {
    hit: { label: '命中', icon: 'bi-check-lg' },
    miss: { label: '未命中', icon: 'bi-dash-lg' },
    skipped: { label: '已跳过', icon: 'bi-skip-forward' },
    error: { label: '出错', icon: 'bi-x-lg' },
    low_confidence: { label: '低置信度', icon: 'bi-exclamation-lg' },
};

function getStepStatusMeta(status) {
    return STEP_STATUS_META[status] || { label: status || '未知', icon: 'bi-question-lg' };
}

// ========== 通用工具 ==========

function normalizeRecordText(value) {
    return String(value || '').trim();
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

function isMatchFailure(record, trace) {
    // 优先根据 status 判断：成功类状态不算匹配失败
    // 修复重试成功后原 match_method 仍是 failed 导致详情页误判的问题
    if (record.status === 'success' || record.status === 'retried') {
        return false;
    }
    if (record.match_method === 'failed') {
        return true;
    }
    if (trace && trace.final_match_method === 'failed') {
        return true;
    }
    return false;
}

// 耗时格式化：<1000ms 显示毫秒，否则显示秒
function formatElapsedMs(ms) {
    const v = Math.max(0, Number(ms) || 0);
    if (v < 1000) {
        return `${Math.round(v)}ms`;
    }
    return `${(v / 1000).toFixed(2)}s`;
}

// 总耗时：优先 trace.total_elapsed_ms（含全流程），否则退化为各 step 求和
function getTraceTotalMs(trace) {
    if (!trace) {
        return 0;
    }
    if (typeof trace.total_elapsed_ms === 'number' && trace.total_elapsed_ms > 0) {
        return trace.total_elapsed_ms;
    }
    const steps = Array.isArray(trace.steps) ? trace.steps : [];
    return steps.reduce((acc, s) => acc + Math.max(0, Number(s.elapsed_ms) || 0), 0);
}

// 单步耗时热度分级：'' 正常 / warm 略慢 / hot 偏高 / critical 严重
function classifyStepHeat(elapsedMs, totalMs) {
    const elapsed = Math.max(0, Number(elapsedMs) || 0);
    if (elapsed <= 0) {
        return '';
    }
    const share = totalMs > 0 ? elapsed / totalMs : 0;
    if (elapsed >= RD_TIMING.CRITICAL_STEP_MS
        || (share >= RD_TIMING.CRITICAL_STEP_SHARE && elapsed >= RD_TIMING.SLOW_STEP_MS)) {
        return 'critical';
    }
    if (elapsed >= RD_TIMING.HOT_STEP_MS
        || (elapsed >= RD_TIMING.SLOW_STEP_MS && share >= RD_TIMING.SLOW_STEP_SHARE)) {
        return 'hot';
    }
    if (elapsed >= RD_TIMING.WARM_STEP_MS) {
        return 'warm';
    }
    return '';
}

// 整体节奏评级：流畅 / 正常 / 偏慢
function getOverallPace(totalMs) {
    if (totalMs >= RD_TIMING.SLOW_TOTAL_MS) {
        return { cls: 'slow', label: '整体偏慢' };
    }
    if (totalMs >= RD_TIMING.HOT_STEP_MS) {
        return { cls: 'ok', label: '正常' };
    }
    return { cls: 'fast', label: '流畅' };
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

// ========== 总览卡（结果 + 耗时表现） ==========

function renderRecordHero(record, trace) {
    const status = record.status || '';
    const isSuccess = status === 'success' || status === 'retried';
    const heroCls = isSuccess ? 'success' : (status === 'error' ? 'error' : 'neutral');
    const statusTextMap = {
        success: '同步成功',
        retried: '重试成功',
        error: '同步失败',
        ignored: '已忽略',
        queued: '已排队',
    };
    const statusText = statusTextMap[status] || status || '未知状态';
    const statusIcon = isSuccess
        ? 'bi-check-circle-fill'
        : (status === 'error'
            ? 'bi-x-circle-fill'
            : (status === 'queued' ? 'bi-clock-history' : 'bi-dash-circle-fill'));

    const message = normalizeRecordText(record.message || trace?.final_message);
    const totalMs = getTraceTotalMs(trace);
    const pace = getOverallPace(totalMs);
    const steps = Array.isArray(trace?.steps) ? trace.steps : [];

    // 耗时最高的步骤（用于慢步骤提示）
    let slowest = null;
    steps.forEach((s, i) => {
        const elapsed = Math.max(0, Number(s.elapsed_ms) || 0);
        if (!slowest || elapsed > slowest.elapsed) {
            slowest = { idx: i + 1, name: getStepDisplayName(s), elapsed };
        }
    });

    let slowHtml = '';
    if (slowest && ['hot', 'critical'].includes(classifyStepHeat(slowest.elapsed, totalMs))) {
        const share = totalMs > 0 ? Math.round((slowest.elapsed / totalMs) * 100) : 0;
        slowHtml = `
            <div class="record-detail-hero__slow">
                <i class="bi bi-exclamation-triangle-fill"></i>
                <span class="record-detail-hero__slow-text">
                    「${escapeHtml(slowest.name)}」耗时 ${formatElapsedMs(slowest.elapsed)}，占总耗时 ${share}%
                </span>
                <button type="button" class="record-detail-hero__slow-jump" onclick="jumpToRecordStep(${slowest.idx})">
                    定位步骤
                </button>
            </div>`;
    }

    let ambiguousHtml = '';
    if (trace?.is_ambiguous) {
        ambiguousHtml = `
            <div class="record-detail-hero__notice">
                <i class="bi bi-intersect"></i>
                <span>匹配结果存在歧义候选，建议核对最终命中的条目</span>
            </div>`;
    }

    const totalChip = totalMs > 0
        ? `<div class="record-detail-hero__stat">
                <span class="record-detail-hero__stat-label">总耗时</span>
                <span class="record-detail-hero__stat-value">${formatElapsedMs(totalMs)}</span>
                <span class="record-detail-hero__pace record-detail-hero__pace--${pace.cls}">${pace.label}</span>
            </div>`
        : '';
    const stepsChip = steps.length > 0
        ? `<div class="record-detail-hero__stat">
                <span class="record-detail-hero__stat-label">流水线步骤</span>
                <span class="record-detail-hero__stat-value">${steps.length}</span>
            </div>`
        : '';

    return `
        <section class="record-detail-hero record-detail-hero--${heroCls}">
            <div class="record-detail-hero__top">
                <div class="record-detail-hero__main">
                    <span class="record-detail-hero__icon"><i class="bi ${statusIcon}"></i></span>
                    <div class="record-detail-hero__text">
                        <div class="record-detail-hero__status">${escapeHtml(statusText)}</div>
                        ${message ? `<div class="record-detail-hero__message">${escapeHtml(message)}</div>` : ''}
                    </div>
                </div>
                <div class="record-detail-hero__stats">${totalChip}${stepsChip}</div>
            </div>
            ${slowHtml}
            ${ambiguousHtml}
        </section>
    `;
}

// ========== 耗时瀑布（仅在有步骤耗时可见时展示） ==========

function renderTimingWaterfall(trace) {
    const steps = Array.isArray(trace?.steps) ? trace.steps : [];
    if (steps.length === 0) {
        return '';
    }
    const rows = steps.map((s, i) => ({
        idx: i + 1,
        stage: s.stage,
        stageName: getStepDisplayName(s),
        status: s.status || 'unknown',
        elapsed: Math.max(0, Number(s.elapsed_ms) || 0),
    }));
    const sum = rows.reduce((acc, r) => acc + r.elapsed, 0);
    if (sum <= 0) {
        return '';
    }
    const totalMs = Math.max(getTraceTotalMs(trace), sum, 1);

    // 全部步骤都很快（无 warm 及以上）时瀑布没有信息量，直接隐藏；
    // 总览卡已展示总耗时与节奏评级
    const hasVisibleHeat = rows.some((r) => classifyStepHeat(r.elapsed, totalMs) !== '');
    if (!hasVisibleHeat) {
        return '';
    }

    const segments = rows.map((r) => {
        const pct = (r.elapsed / totalMs) * 100;
        const heat = classifyStepHeat(r.elapsed, totalMs) || 'normal';
        const shareText = `${pct.toFixed(0)}%`;
        return `<button type="button"
                class="record-detail-waterfall__seg record-detail-waterfall__seg--${heat}"
                style="width:${pct.toFixed(2)}%"
                onclick="jumpToRecordStep(${r.idx})"
                title="${escapeHtml(r.stageName)} · ${formatElapsedMs(r.elapsed)} · ${shareText}"
                aria-label="跳转到「${escapeHtml(r.stageName)}」，耗时 ${formatElapsedMs(r.elapsed)}"></button>`;
    }).join('');

    // 慢步骤图例：仅列出偏慢/严重的步骤，点击跳转
    const slowChips = rows
        .filter((r) => ['hot', 'critical'].includes(classifyStepHeat(r.elapsed, totalMs)))
        .map((r) => {
            const share = Math.round((r.elapsed / totalMs) * 100);
            const heat = classifyStepHeat(r.elapsed, totalMs);
            return `<button type="button"
                    class="record-detail-waterfall__slow-chip record-detail-waterfall__slow-chip--${heat}"
                    onclick="jumpToRecordStep(${r.idx})">
                <i class="bi bi-exclamation-triangle-fill"></i>
                ${escapeHtml(r.stageName)} ${formatElapsedMs(r.elapsed)} · ${share}%
            </button>`;
        }).join('');

    return `
        <section class="record-detail-waterfall">
            <header class="record-detail-waterfall__head">
                <h6 class="record-detail-waterfall__title"><i class="bi bi-stopwatch"></i>耗时分布</h6>
                <span class="record-detail-waterfall__total">总耗时 <strong>${formatElapsedMs(totalMs)}</strong></span>
            </header>
            <div class="record-detail-waterfall__bar" role="list">${segments}</div>
            ${slowChips ? `<div class="record-detail-waterfall__slow-list">${slowChips}</div>` : ''}
        </section>
    `;
}

// 耗时表点击跳转到对应步骤：展开目标卡片与所在分组后滚动高亮
function jumpToRecordStep(idx) {
    const target = document.getElementById(`record-step-${idx}`);
    if (!target) return;
    const card = target.querySelector('.record-detail-step__card');
    if (card && card.tagName === 'DETAILS') {
        card.open = true;
    }
    let parent = target.parentElement;
    while (parent) {
        if (parent.tagName === 'DETAILS') {
            parent.open = true;
        }
        parent = parent.parentElement;
    }
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.remove('record-detail-step--flash');
    void target.offsetWidth;
    target.classList.add('record-detail-step--flash');
}

// ========== KV 网格（替代单行横表：字段多时更可读） ==========

// 值格式化：布尔→是/否，空值→—，score/top_ratio→百分比，其余浮点保留 4 位
function formatPayloadValueHtml(key, value) {
    if (value === null || value === undefined || value === '') {
        return { html: '—', empty: true };
    }
    if (typeof value === 'boolean') {
        return { html: value ? '是' : '否' };
    }
    if (typeof value === 'number') {
        if (key === 'score' || key === 'top_ratio') {
            return { html: `${(value * 100).toFixed(1)}%` };
        }
        const rounded = Math.round(value * 10000) / 10000;
        return { html: String(rounded) };
    }
    const str = String(value);
    if (key.endsWith('_url')) {
        return { html: `<a href="${escapeHtml(str)}" target="_blank" rel="noopener">${escapeHtml(str)}</a>` };
    }
    return { html: escapeHtml(str) };
}

function renderPayloadKv(payload, labels) {
    if (!payload || typeof payload !== 'object') {
        return '';
    }
    const keys = Object.keys(payload);
    if (keys.length === 0) {
        return '';
    }
    const labelMap = labels || {};
    const cells = keys.map((k) => {
        const label = labelMap[k] || k;
        const v = payload[k];
        const { html, empty } = formatPayloadValueHtml(k, v);
        const wideCls = !empty && String(v).length > 26 ? ' record-detail-kv__item--wide' : '';
        const emptyCls = empty ? ' record-detail-kv__value--empty' : '';
        return `<div class="record-detail-kv__item${wideCls}">
            <span class="record-detail-kv__label">${escapeHtml(label)}</span>
            <span class="record-detail-kv__value${emptyCls}">${html}</span>
        </div>`;
    }).join('');
    return `<div class="record-detail-kv">${cells}</div>`;
}

// receive step 输入字段：sync 开始时的输入
function renderReceiveInputKv(step) {
    if (!step || step.stage !== 'receive' || !step.processed_payload) {
        return '';
    }
    let html = renderPayloadKv(step.processed_payload, {
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

// result step 结果字段：状态/集数/链接/消息
function renderResultKv(step) {
    if (!step || step.stage !== 'result' || !step.processed_payload) {
        return '';
    }
    return renderPayloadKv(step.processed_payload, {
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

// 通用结构化输入/输出字段标签（steps.inputs / steps.outputs 由管线统一记录）
const COMMON_PAYLOAD_LABELS = {
    subject_id: '条目 ID',
    episode_id: '剧集 ID',
    is_season_id: '是否季度 ID',
    is_season_matched_id: '季度 ID 可信',
    season: '季',
    episode: '集',
    target_episode: '目标集',
    media_type: '媒体类型',
    release_date: '发布日期',
    title: '标题',
    ori_title: '原始标题',
    normalized_title: '归一化标题',
    search_title: '搜索标题',
    premiere_date: '首播日期',
    start_date: '开始日期',
    end_date: '结束日期',
    subject_types: '条目类型',
    is_movie: '剧场版',
    changed: '是否变更',
    match_path: '命中路径',
    match_path_label: '命中路径描述',
    match_method: '匹配方式',
    match_method_detail: '匹配方式细节',
    match_stage: '匹配阶段',
    is_archive_hit: 'archive 命中',
    mark_status: '标记状态',
    queued: '已入队',
    status: '状态',
    message: '消息',
    error: '错误',
    score: '置信度',
    total_candidates: '候选数',
    matched_title: '匹配标题',
    date_matched: '日期匹配',
    stripped_title: '剥离后缀标题',
    stripped_ori: '剥离后缀原标题',
    variants: '搜索变体',
    top_ratio: '首条相似度',
    matched_variant_method: '命中变体',
    subject_url: '条目链接',
    episode_url: '剧集链接',
    bgm_title: '番剧标题',
};

// 命中路径 → 中文描述（对齐后端 _PATH_DETAIL）
const CROSS_SEASON_PATH_TEXT = {
    chain: '前传/续集链',
    franchise_archive: '同 IP 闭包（本地归档）',
    franchise_online: '同 IP 改编一跳（在线）',
};

// 结构化输入/输出：上一步进去了什么、这一步出来了什么
function renderStepInputsOutputs(step) {
    const inputs = (step.inputs && typeof step.inputs === 'object' && Object.keys(step.inputs).length > 0)
        ? step.inputs
        : null;
    const outputs = (step.outputs && typeof step.outputs === 'object' && Object.keys(step.outputs).length > 0)
        ? step.outputs
        : null;
    if (!inputs && !outputs) {
        return '';
    }
    let html = '';
    if (inputs) {
        html += `<div class="record-detail-step__io-title"><i class="bi bi-box-arrow-in-right"></i>输入</div>`;
        html += renderPayloadKv(inputs, COMMON_PAYLOAD_LABELS);
    }
    if (outputs) {
        const out = { ...outputs };
        if (out.match_path) {
            out.match_path = CROSS_SEASON_PATH_TEXT[out.match_path] || out.match_path;
        }
        html += `<div class="record-detail-step__io-title"><i class="bi bi-box-arrow-out-right"></i>输出</div>`;
        html += renderPayloadKv(out, COMMON_PAYLOAD_LABELS);
    }
    return html;
}

// episode_resolve step：展示输入→输出的集数解析变更过程
function renderEpisodeResolveKv(step) {
    if (!step || step.stage !== 'episode_resolve' || !step.processed_payload) {
        return '';
    }
    return renderPayloadKv(step.processed_payload, {
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

// cross_season step：展示跨季链/同 IP 改编查找的变更过程
function renderCrossSeasonKv(step) {
    if (!step || step.stage !== 'cross_season' || !step.processed_payload) {
        return '';
    }
    const pathText = CROSS_SEASON_PATH_TEXT[step.processed_payload.match_path] || '';
    const payload = { ...step.processed_payload };
    if (pathText) {
        payload.match_path = pathText;
    }
    return renderPayloadKv(payload, {
        input_subject_id: '输入条目 ID',
        output_subject_id: '输出条目 ID',
        output_episode_id: '输出剧集 ID',
        target_episode: '目标集',
        changed: '是否变更',
        match_path: '命中路径',
        subject_url: '条目链接',
        episode_url: '剧集链接',
        error: '错误',
    });
}

// step.error_detail 异常详情（status=error 时默认展开）
function renderStepErrorDetail(detail, defaultOpen) {
    if (!detail) return '';
    const type = escapeHtml(detail.type || '');
    const message = escapeHtml(detail.message || '');
    const traceback = detail.traceback || '';
    const openAttr = defaultOpen ? ' open' : '';
    let html = `<details class="record-detail-error-detail record-detail-subpanel record-detail-subpanel--error mt-1"${openAttr}>
        <summary class="record-detail-subpanel__summary record-detail-subpanel__summary--error"><i class="bi bi-bug-fill"></i>异常详情：${type}</summary>
        <div class="mt-1 small">
            <div class="text-danger"><strong>${type}</strong>: ${message}</div>`;
    if (traceback) {
        html += `<pre class="record-detail-traceback mt-1 mb-0">${escapeHtml(traceback)}</pre>`;
    }
    html += '</div></details>';
    return html;
}

// step.request_params 实际发送的搜索参数
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

// step.api_response_summary API 返回质量摘要
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
        ? `<a href="https://bgm.tv/subject/${escapeHtml(String(firstId))}" target="_blank" rel="noopener" class="record-detail-step__pill record-detail-step__pill--link"><i class="bi bi-link-45deg"></i>${escapeHtml(firstName || String(firstId))}</a>`
        : '<span class="record-detail-step__pill record-detail-step__pill--muted">无候选</span>';
    return `<details class="record-detail-api-summary record-detail-subpanel mt-1">
        <summary class="record-detail-subpanel__summary"><i class="bi bi-graph-up"></i>API 返回摘要</summary>
        <div class="record-detail-step__pills">${totalBadge}${sourceBadge}${firstLink}</div>
    </details>`;
}

// ========== 步骤时间线 ==========

// 单步耗时 chip：热度着色，hot/critical 带警示图标
function renderStepTimeChip(elapsedMs, totalMs) {
    const elapsed = Math.max(0, Number(elapsedMs) || 0);
    const heat = classifyStepHeat(elapsed, totalMs);
    const share = totalMs > 0 ? Math.round((elapsed / totalMs) * 100) : 0;
    const heatCls = heat ? ` record-detail-step__time--${heat}` : '';
    const icon = ['hot', 'critical'].includes(heat)
        ? '<i class="bi bi-exclamation-triangle-fill"></i>'
        : '<i class="bi bi-stopwatch"></i>';
    const title = totalMs > 0 ? ` title="占总耗时 ${share}%"` : '';
    return `<span class="record-detail-step__time${heatCls}"${title}>${icon}${formatElapsedMs(elapsed)}</span>`;
}

// 慢步骤说明（hot/critical 时展示占比）
function renderStepSlowNote(elapsedMs, totalMs) {
    const elapsed = Math.max(0, Number(elapsedMs) || 0);
    const heat = classifyStepHeat(elapsed, totalMs);
    if (!['hot', 'critical'].includes(heat)) {
        return '';
    }
    const share = totalMs > 0 ? Math.round((elapsed / totalMs) * 100) : 0;
    const label = heat === 'critical' ? '耗时严重' : '耗时偏高';
    return `
        <div class="record-detail-step__slow-note record-detail-step__slow-note--${heat}">
            <i class="bi bi-speedometer2"></i>
            ${label}：本步 ${formatElapsedMs(elapsed)}，约占总耗时 ${share}%，通常是网络请求或 Bangumi API 响应耗时
        </div>`;
}

// 步骤详细内容（折叠区内）：异常 / 搜索参数 / API 摘要 / 候选 / 输入输出
function renderStepDetailContent(step, status, elapsed, totalMs) {
    const hasStructuredIO = !!(step.inputs && Object.keys(step.inputs).length > 0)
        || !!(step.outputs && Object.keys(step.outputs).length > 0);

    let body = '';

    // 慢步骤提示
    body += renderStepSlowNote(elapsed, totalMs);

    // 异常详情（error 时默认展开）
    if (step.error_detail) {
        body += renderStepErrorDetail(step.error_detail, status === 'error');
    }

    // 搜索参数 / API 返回摘要
    if (step.request_params) {
        body += renderStepRequestParams(step.request_params);
    }
    if (step.api_response_summary) {
        body += renderStepApiResponseSummary(step.api_response_summary);
    }

    // receive step：渲染 sync 开始时的输入字段
    if (step.stage === 'receive') {
        body += renderReceiveInputKv(step);
    }

    // 候选列表（多行数据保留表格）
    if (step.candidates && step.candidates.length > 0) {
        body += renderMatchCandidatesTable(step.candidates);
    }

    // 结构化输入/输出；旧记录回退到特化字段
    if (hasStructuredIO) {
        body += renderStepInputsOutputs(step);
    } else {
        if (step.stage === 'episode_resolve') {
            body += renderEpisodeResolveKv(step);
        }
        if (step.stage === 'cross_season') {
            body += renderCrossSeasonKv(step);
        }
        if (step.stage === 'result') {
            body += renderResultKv(step);
        }
    }

    return body;
}

// 单个步骤卡片：摘要行（阶段 + 状态 + reason + 耗时）常显，详情折叠；
// error / low_confidence / 慢步骤默认展开
function renderStepCard(step, idx, totalMs, opts) {
    const options = opts || {};
    const nested = !!options.nested;
    const status = step.status || 'unknown';
    const meta = getStepStatusMeta(status);
    const stageName = getStepDisplayName(step);
    const elapsed = Math.max(0, Number(step.elapsed_ms) || 0);
    const heat = classifyStepHeat(elapsed, totalMs);

    const detailHtml = renderStepDetailContent(step, status, elapsed, totalMs);
    const expandable = !!detailHtml.trim();
    const autoOpen = expandable
        && (status === 'error' || status === 'low_confidence' || ['hot', 'critical'].includes(heat));

    // 摘要行右侧：subject 链接 / 置信度 / 候选数 / 耗时 / 折叠箭头
    let headMeta = '';
    if (step.subject_id) {
        headMeta += `<a href="https://bgm.tv/subject/${escapeHtml(String(step.subject_id))}" target="_blank" rel="noopener" class="record-detail-step__subject" title="在 Bangumi 查看条目" onclick="event.stopPropagation()">`
            + `<i class="bi bi-collection"></i>subject/${escapeHtml(String(step.subject_id))}</a>`;
    }
    // ep 链接取自各 step 既有 JSON（outputs.episode_id 优先，回退 inputs.episode_id），
    // 不依赖单独字段，老记录同样可显示
    const stepEpId = (step.outputs && step.outputs.episode_id)
        || (step.inputs && step.inputs.episode_id)
        || null;
    if (stepEpId) {
        headMeta += `<a href="https://bgm.tv/ep/${escapeHtml(String(stepEpId))}" target="_blank" rel="noopener" class="record-detail-step__ep-link" title="在 Bangumi 查看剧集" onclick="event.stopPropagation()">`
            + `<i class="bi bi-play-circle"></i>ep/${escapeHtml(String(stepEpId))}</a>`;
    }
    if (step.score !== null && step.score !== undefined) {
        headMeta += `<span class="record-detail-step__score">${formatMatchScore(step.score)}</span>`;
    }
    if (step.candidates && step.candidates.length > 0) {
        headMeta += `<span class="record-detail-step__cand-count">候选 ${step.candidates.length}</span>`;
    }
    headMeta += renderStepTimeChip(elapsed, totalMs);

    const headInner = `
            <span class="record-detail-step__head-line">
                <strong class="record-detail-step__name">${escapeHtml(stageName)}</strong>
                <span class="record-detail-step__badge record-detail-step__badge--${status}"><i class="bi ${meta.icon}"></i>${escapeHtml(meta.label)}</span>
                ${headMeta}
                ${expandable ? '<i class="bi bi-chevron-down record-detail-step__chevron" aria-hidden="true"></i>' : ''}
            </span>
            ${step.reason ? `<span class="record-detail-step__reason">${escapeHtml(step.reason)}</span>` : ''}`;

    const slowCls = ['hot', 'critical'].includes(heat) ? ` record-detail-step--slow-${heat}` : '';
    const nestedCls = nested ? ' record-detail-step--nested' : '';

    let html = `<article class="record-detail-step record-detail-step--${status}${nestedCls}${slowCls}" id="record-step-${idx}" tabindex="-1">`;
    if (!nested) {
        html += '<div class="record-detail-step__rail" aria-hidden="true">';
        html += `<span class="record-detail-step__index">${idx}</span>`;
        if (!options.isLast) {
            html += '<span class="record-detail-step__line"></span>';
        }
        html += '</div>';
    } else {
        html += `<span class="record-detail-step__dot record-detail-step__dot--${status}" aria-hidden="true"></span>`;
    }

    if (expandable) {
        html += `<details class="record-detail-step__card"${autoOpen ? ' open' : ''}>`;
        html += `<summary class="record-detail-step__head">${headInner}</summary>`;
        html += `<div class="record-detail-step__content">${detailHtml}</div>`;
        html += '</details>';
    } else {
        // 无附加信息：不可折叠的简洁卡片
        html += `<div class="record-detail-step__card record-detail-step__card--plain">`;
        html += `<header class="record-detail-step__head">${headInner}</header>`;
        html += '</div>';
    }
    html += '</article>';
    return html;
}

// bgm_search 子管线分组：连续的 api_search_* 子步骤折叠为一个分组展示
function renderSubPipelineGroup(subSteps, startIdx, totalMs) {
    const errorStep = subSteps.find((s) => s.status === 'error');
    const hitStep = subSteps.find((s) => s.status === 'hit');
    let outcomeCls = 'muted';
    let outcomeText = '未命中';
    if (errorStep) {
        outcomeCls = 'error';
        outcomeText = `${getPipelineStageName(errorStep.stage)}出错`;
    } else if (hitStep) {
        outcomeCls = 'hit';
        outcomeText = `${getPipelineStageName(hitStep.stage)}命中`;
    }

    const shouldOpen = subSteps.some((s) => s.status === 'error'
        || ['hot', 'critical'].includes(classifyStepHeat(s.elapsed_ms, totalMs)));

    const inner = subSteps
        .map((s, k) => renderStepCard(s, startIdx + k + 1, totalMs, { nested: true }))
        .join('');

    return `
        <details class="record-detail-subgroup"${shouldOpen ? ' open' : ''}>
            <summary class="record-detail-subgroup__head">
                <i class="bi bi-diagram-2"></i>
                <span class="record-detail-subgroup__title">API 搜索内部流程</span>
                <span class="record-detail-subgroup__count">${subSteps.length} 步</span>
                <span class="record-detail-subgroup__outcome record-detail-subgroup__outcome--${outcomeCls}">${escapeHtml(outcomeText)}</span>
                <i class="bi bi-chevron-down record-detail-subgroup__chevron" aria-hidden="true"></i>
            </summary>
            <div class="record-detail-subgroup__steps">${inner}</div>
        </details>
    `;
}

// 匹配失败引导横幅
function renderMatchFailureBanner(record) {
    const mapTitle = encodeURIComponent(record.title || '');
    const mapSeason = record.season || 1;
    return `
        <div class="record-detail-banner record-detail-banner--warn">
            <i class="bi bi-exclamation-triangle-fill"></i>
            <span class="flex-grow-1">未匹配到 Bangumi 条目，可添加自定义映射解决</span>
            <a href="${appUrl('/mappings')}?title=${mapTitle}&season=${mapSeason}" class="record-detail-banner__action">
                前往映射 <i class="bi bi-arrow-up-right"></i>
            </a>
        </div>
    `;
}

// 流水线渲染：总览 + 耗时瀑布 + 步骤时间线（子管线步骤分组折叠）
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

    const steps = trace.steps;
    const totalMs = getTraceTotalMs(trace);

    let html = renderRecordHero(record, trace);
    html += renderTimingWaterfall(trace);

    // 顶层条目列表（含分组占位），用于计算连接线是否收尾
    const topLevel = [];
    let i = 0;
    while (i < steps.length) {
        if (isSubPipelineStep(steps[i])) {
            let j = i;
            while (j < steps.length && isSubPipelineStep(steps[j])) {
                j++;
            }
            topLevel.push({ type: 'group', from: i, to: j });
            i = j;
        } else {
            topLevel.push({ type: 'step', index: i });
            i++;
        }
    }

    html += '<div class="record-detail-steps">';
    topLevel.forEach((item, k) => {
        const isLast = k === topLevel.length - 1;
        if (item.type === 'group') {
            const subSteps = steps.slice(item.from, item.to);
            const groupHtml = renderSubPipelineGroup(subSteps, item.from, totalMs);
            if (isLast) {
                html += groupHtml;
            } else {
                html += `<div class="record-detail-steps__group-slot">${groupHtml}</div>`;
            }
        } else {
            html += renderStepCard(steps[item.index], item.index + 1, totalMs, { isLast });
        }
    });
    html += '</div>';

    if (isMatchFailure(record, trace)) {
        html += renderMatchFailureBanner(record);
    }

    return html;
}

// ========== 弹窗头部（保持轻量：身份事实 chips） ==========

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
        const parts = [
            `<span>${escapeHtml(record.timestamp || '')}</span>`,
            renderSourceBadge(record.source),
        ];
        const runId = (record.run_id || '').trim();
        if (runId) {
            parts.push(
                `<a class="record-detail-modal__chip record-detail-modal__chip--link" ` +
                `href="${appUrl('/logs')}?run_id=${encodeURIComponent(runId)}" target="_blank">` +
                `<i class="bi bi-card-list"></i> 同步日志</a>`
            );
        }
        subtitleEl.innerHTML = parts.join('<span class="record-detail-meta-dot">·</span>');
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

    // 匹配方式：粗粒度 final_match_method + 细粒度 final_match_method_detail
    // 优先取 trace（更准确，重试成功后会回写），回退 record.match_method
    const matchMethod = t.final_match_method || record.match_method || '';
    const matchMethodDetail = t.final_match_method_detail || '';
    if (matchMethod) {
        let methodHtml = renderMatchMethodBadge(matchMethod);
        if (matchMethodDetail) {
            methodHtml += ' ' + renderMatchMethodDetailBadge(matchMethodDetail);
        }
        chips.push(`<span class="record-detail-modal__chip record-detail-modal__chip--match-method">${methodHtml}</span>`);
    }

    // subject 链接
    if (subjectId) {
        let label = `subject/${subjectId}`;
        if (bgmTitle) {
            label += ` · ${bgmTitle}`;
        }
        chips.push(`<a href="https://bgm.tv/subject/${escapeHtml(subjectId)}" target="_blank" rel="noopener" class="record-detail-modal__chip record-detail-modal__chip--link"><i class="bi bi-collection"></i>${escapeHtml(label)}</a>`);
    }

    // episode 链接
    if (episodeId) {
        chips.push(`<a href="https://bgm.tv/ep/${escapeHtml(episodeId)}" target="_blank" rel="noopener" class="record-detail-modal__chip record-detail-modal__chip--link"><i class="bi bi-play-circle"></i>ep/${escapeHtml(episodeId)}</a>`);
    }

    // 置信度
    if (score !== null && score !== undefined && isSuccess) {
        chips.push(`<span class="record-detail-modal__chip record-detail-modal__chip--score">置信度 ${(score * 100).toFixed(0)}%</span>`);
    }

    return `<div class="record-detail-modal__chips">${chips.join('')}</div>`;
}

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

// ========== 弹窗加载流程 ==========

function renderRecordDetailLoading() {
    return `
        <div class="record-detail-loading" aria-busy="true">
            <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
            <span>正在加载匹配详情…</span>
        </div>
    `;
}

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
    const pipelineContent = document.getElementById('record-pipeline-content');
    if (!pipelineContent) {
        return;
    }

    setRecordSummaryHtml(record, parseRecordMatchTrace(record));
    pipelineContent.innerHTML = renderRecordDetailLoading();

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
        pipelineContent.innerHTML = renderRecordDetailLoading();
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

window.showRecordDetail = showRecordDetail;
window.jumpToRecordStep = jumpToRecordStep;
