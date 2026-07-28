// 徽章渲染工具：同步状态、匹配方式、来源、媒体类型等

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
        archive: ['warning', '本地归档'],
        api_search: ['info', 'API 搜索'],
        failed: ['danger', '失败'],
    };
    const [color, text] = badges[method] || ['secondary', '未知'];
    return `<span class="badge rounded-pill bg-${color}">${text}</span>`;
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
window.renderCandidateStatusBadge = renderCandidateStatusBadge;
window.renderMediaTypeBadge = renderMediaTypeBadge;
window.getSourceColor = getSourceColor;
window.getSourceTlClass = getSourceTlClass;
window.renderSourceBadge = renderSourceBadge;
