// 徽章渲染工具：同步状态、匹配方式、来源、媒体类型等

function getSyncRecordStatusColor(status) {
    switch (status) {
        case 'success': return 'success';
        case 'error': return 'danger';
        case 'ignored': return 'warning';
        case 'retried': return 'success';
        case 'queued': return 'info';
        default: return 'secondary';
    }
}

function getSyncRecordStatusText(status) {
    switch (status) {
        case 'success': return '成功';
        case 'error': return '失败';
        case 'ignored': return '已忽略';
        case 'retried': return '已重试';
        case 'queued': return '已排队';
        default: return status;
    }
}

function renderSyncStatusBadge(status) {
    return `<span class="badge rounded-pill bg-${getSyncRecordStatusColor(status)}">${getSyncRecordStatusText(status)}</span>`;
}

// ===== sub_status 推断：从 message 关键字推断细粒度子状态 =====
// 仅对 error/queued/ignored/retried 状态推断，success 不显示 sub badge
const _SUB_STATUS_RULES = [
    // error 类
    { status: 'error', sub: 'match_failed', keywords: ['未查询到番剧信息', '未找到匹配的番剧', '未找到匹配'] },
    { status: 'error', sub: 'episode_not_found', keywords: ['未找到对应的剧集', '不存在或集数过多'] },
    { status: 'error', sub: 'auth_error', keywords: ['认证失败', 'access_token', '未授权', 'access token'] },
    { status: 'error', sub: 'api_error', keywords: ['API 搜索出错', 'API 请求出错', '请求失败'] },
    { status: 'error', sub: 'config_error', keywords: ['配置错误', '不支持的同步模式', 'bangumi配置'] },
    { status: 'error', sub: 'permission_denied', keywords: ['用户名', '用户映射', '允许同步', '账号配置无效'] },
    { status: 'error', sub: 'validation_error', keywords: ['同步类型', '同步名称', '不能为0', '不支持SP'] },
    // queued 类
    { status: 'queued', sub: 'api_unreachable', keywords: ['API 不可达', '已入待同步队列'] },
    { status: 'queued', sub: 'replay_abandoned', keywords: ['补发放弃', '超过最大重试次数'] },
    // ignored 类
    { status: 'ignored', sub: 'blocked_keyword', keywords: ['屏蔽关键词'] },
    { status: 'ignored', sub: 'config_disabled', keywords: ['配置中关闭', '仅剧场版', '仅支持'] },
    // retried 类
    { status: 'retried', sub: 'replay_success', keywords: ['已重试成功'] },
    { status: 'retried', sub: 'replay_ignored', keywords: ['重试被忽略'] },
];

const _SUB_STATUS_LABELS = {
    match_failed: ['danger', '匹配失败'],
    episode_not_found: ['danger', '集数未找到'],
    auth_error: ['danger', '认证失败'],
    api_error: ['danger', 'API错误'],
    config_error: ['warning', '配置错误'],
    permission_denied: ['warning', '权限不足'],
    validation_error: ['warning', '参数错误'],
    unknown_error: ['secondary', '未知错误'],
    api_unreachable: ['info', 'API不可达'],
    replay_abandoned: ['secondary', '补发放弃'],
    blocked_keyword: ['warning', '屏蔽词'],
    config_disabled: ['warning', '配置关闭'],
    replay_success: ['success', '重试成功'],
    replay_ignored: ['warning', '重试忽略'],
};

function inferSyncSubStatus(record) {
    const status = record.status;
    if (status === 'success') return null;
    const message = (record.message || '').toLowerCase();
    for (const rule of _SUB_STATUS_RULES) {
        if (rule.status !== status) continue;
        if (rule.keywords.some(kw => message.includes(kw.toLowerCase()))) {
            return rule.sub;
        }
    }
    // error 兜底
    if (status === 'error') return 'unknown_error';
    return null;
}

function renderSyncSubStatusBadge(record) {
    const sub = inferSyncSubStatus(record);
    if (!sub) return '';
    const [color, text] = _SUB_STATUS_LABELS[sub] || ['secondary', sub];
    return ` <span class="badge rounded-pill bg-${color} bg-opacity-75" style="font-size:0.7em">${text}</span>`;
}

function renderMatchMethodBadge(method) {
    const badges = {
        custom_mapping: ['primary', '自定义映射'],
        bangumi_data: ['success', 'bangumi-data'],
        archive: ['warning', '本地归档'],
        api_search: ['info', 'API 搜索'],
        failed: ['danger', '失败'],
    };
    const [color, text] = badges[method] || ['secondary', '未知'];
    return `<span class="badge rounded-pill bg-${color}">${text}</span>`;
}

// 细粒度匹配方式徽章：对应 MatchTrace.final_match_method_detail
// exact / prefix_variant / season_stripped / media_suffix_stripped /
// unwrapped / main_segment / fuzzy / cross_season_chain /
// cross_season_franchise_archive / cross_season_franchise_online
function renderMatchMethodDetailBadge(detail) {
    if (!detail) {
        return '';
    }
    const badges = {
        exact: ['success', '精确命中'],
        prefix_variant: ['info', '前缀变体'],
        season_stripped: ['info', '剥离季号'],
        media_suffix_stripped: ['info', '剥离后缀'],
        unwrapped: ['info', '去包裹'],
        main_segment: ['secondary', '主段'],
        fuzzy: ['warning', '模糊匹配'],
        cross_season_chain: ['primary', '跨季链'],
        cross_season_franchise_archive: ['purple', '同IP改编·归档'],
        cross_season_franchise_online: ['info', '同IP改编·在线'],
    };
    const [color, text] = badges[detail] || ['secondary', detail];
    return `<span class="badge rounded-pill bg-${color} bg-opacity-75" style="font-size:0.75em">${text}</span>`;
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

window.getSyncRecordStatusColor = getSyncRecordStatusColor;
window.getSyncRecordStatusText = getSyncRecordStatusText;
// 兼容旧别名
window.getStatusColor = getSyncRecordStatusColor;
window.getStatusText = getSyncRecordStatusText;
window.renderSyncStatusBadge = renderSyncStatusBadge;
window.renderMatchMethodBadge = renderMatchMethodBadge;
window.renderMatchMethodDetailBadge = renderMatchMethodDetailBadge;
window.renderCandidateStatusBadge = renderCandidateStatusBadge;
window.renderMediaTypeBadge = renderMediaTypeBadge;
window.getSourceColor = getSourceColor;
window.getSourceTlClass = getSourceTlClass;
window.renderSourceBadge = renderSourceBadge;
window.inferSyncSubStatus = inferSyncSubStatus;
window.renderSyncSubStatusBadge = renderSyncSubStatusBadge;
