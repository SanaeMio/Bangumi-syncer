// 数据表格通用工具：空状态、加载态、行入场动画、分页

const APP_TABLE_PAGE_SIZE = 10;
window.APP_TABLE_PAGE_SIZE = APP_TABLE_PAGE_SIZE;

/**
 * 生成空状态 HTML
 */
function createAppEmptyStateHtml(title, subtitle) {
    let html = `<div class="app-empty-state"><i class="bi bi-inbox app-empty-state__icon"></i><div>${title}</div>`;
    if (subtitle) {
        html += `<div class="text-muted small mt-1">${subtitle}</div>`;
    }
    html += '</div>';
    return html;
}

/**
 * 切换表格加载态
 * @param {boolean} show 是否显示加载态
 * @param {string} wrapId 表格容器 id
 * @param {string} [loadingId='loading'] 加载态元素 id
 */
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

/**
 * 绑定移动端整行点击事件
 * @param {string} tableSelector 表格选择器
 * @param {(recordId: number) => void} onRowClick 点击回调
 */
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

/**
 * 重放表格入场动画
 */
function replayAppTableAnimation(wrapId) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;
    wrap.classList.remove('records-table-wrap--enter');
    void wrap.offsetWidth;
    wrap.classList.add('records-table-wrap--enter');
}

/**
 * 给行添加入场动画类与延迟
 */
function applyAppTableRowEnter(row, index) {
    row.classList.add('records-table-row--enter');
    row.style.animationDelay = `${Math.min(index * 0.04, 0.36)}s`;
}

/**
 * 批量给 tbody 所有行应用入场动画并重放容器动画
 */
function animateAppTableBody(tbody, wrapId) {
    if (!tbody) return;
    tbody.querySelectorAll('tr').forEach((row, index) => applyAppTableRowEnter(row, index));
    replayAppTableAnimation(wrapId);
}

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

/**
 * 渲染通用分页
 * @param {Object} options
 * @param {number} options.total 总记录数
 * @param {number} options.currentPage 当前页
 * @param {number} options.limit 每页条数
 * @param {string} [options.navId='pagination-nav']
 * @param {string} [options.listId='pagination']
 * @param {string} [options.summaryId='pagination-summary']
 * @param {(page: number) => void} options.onPageChange
 * @param {boolean} [options.animate=true]
 */
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

// ========== 匹配候选表格通用组件 ==========

/**
 * 格式化匹配分数为百分比字符串
 * 统一规则：null/undefined → '-'，否则 (s*100).toFixed(1)%
 * @param {number|null|undefined} score
 * @returns {string}
 */
function formatMatchScore(score) {
    if (score === null || score === undefined) return '-';
    return `${(score * 100).toFixed(1)}%`;
}

/**
 * 候选列表按 score 降序排序（无 score 的排在最后）
 * 返回新数组，不修改原数组
 * @param {Array} candidates
 * @returns {Array}
 */
function sortCandidatesByScore(candidates) {
    if (!candidates || candidates.length === 0) return [];
    return candidates.slice().sort((a, b) => {
        const sa = typeof a.score === 'number' ? a.score : -1;
        const sb = typeof b.score === 'number' ? b.score : -1;
        return sb - sa;
    });
}

/**
 * 渲染匹配候选表格（统一组件，供 records 详情 / pending_candidates / debug 等共用）
 *
 * 统一规则：
 * - 候选按 score 降序排序
 * - score 格式：formatMatchScore
 * - 空值显示为空字符串（视觉更干净）
 * - 候选数 > maxCollapsed 时折叠其余项
 * - 媒体类型列展示 detect_media_type 判断结果（P0 增强字段）
 *
 * @param {Array} candidates 候选列表
 * @param {Object} [options]
 * @param {number} [options.maxCollapsed=3] 超过此数量时折叠其余候选
 * @returns {string} HTML 字符串；空列表返回 ''
 */
function renderMatchCandidatesTable(candidates, options) {
    if (!candidates || candidates.length === 0) {
        return '';
    }
    const opts = options || {};
    const maxCollapsed = typeof opts.maxCollapsed === 'number' ? opts.maxCollapsed : 3;
    const sorted = sortCandidatesByScore(candidates);

    const renderCell = (val) => `<small>${escapeHtml(val === null || val === undefined || val === '' ? '' : String(val))}</small>`;
    const renderMediaType = (t) => {
        if (!t) return '<small class="text-muted">-</small>';
        const map = {
            episode: '剧集',
            movie: '剧场版',
            ova: 'OVA',
            oad: 'OAD',
            real_action: '三次元',
        };
        const label = map[t] || t;
        return `<small><span class="badge rounded-pill bg-secondary bg-opacity-75">${escapeHtml(label)}</span></small>`;
    };
    const renderRows = (items) => items.map((cand) => {
        const name = escapeHtml(cand.name_cn || cand.name || cand.subject_id || '-');
        const subjectId = cand.subject_id || '';
        // 候选别名 tooltip（P2 infobox_aliases）
        const aliases = cand.infobox_aliases && cand.infobox_aliases.length > 0
            ? ` title="别名：${escapeHtml(cand.infobox_aliases.join(' / '))}"`
            : '';
        return `<tr>
            <td><a href="https://bgm.tv/subject/${subjectId}" target="_blank"${aliases}>${name}</a></td>
            <td>${renderCell(subjectId)}</td>
            <td><small>${formatMatchScore(cand.score)}</small></td>
            <td>${renderMediaType(cand.media_type)}</td>
            <td>${renderCell(cand.platform)}</td>
            <td>${renderCell(cand.air_date)}</td>
            <td>${renderCell(cand.source)}</td>
        </tr>`;
    }).join('');

    const tableHead = '<thead><tr><th>条目</th><th>subject_id</th><th>置信度</th><th>类型</th><th>平台</th><th>放送日期</th><th>来源</th></tr></thead>';
    const tableStart = '<div class="table-responsive"><table class="table table-sm table-bordered align-middle mb-0">';
    const tableEnd = '</table></div>';

    if (sorted.length <= maxCollapsed) {
        return tableStart + tableHead + '<tbody>' + renderRows(sorted) + '</tbody>' + tableEnd;
    }

    const rest = sorted.slice(maxCollapsed);
    return tableStart + tableHead + '<tbody>' + renderRows(sorted.slice(0, maxCollapsed)) + '</tbody>' + tableEnd
        + `<details class="record-detail-candidates mt-2">
            <summary>展开其余 ${rest.length} 条候选</summary>
            ${tableStart + tableHead + '<tbody>' + renderRows(rest) + '</tbody>' + tableEnd}
        </details>`;
}

window.createAppEmptyStateHtml = createAppEmptyStateHtml;
window.setAppTableLoading = setAppTableLoading;
window.bindAppTableMobileRowClick = bindAppTableMobileRowClick;
window.replayAppTableAnimation = replayAppTableAnimation;
window.applyAppTableRowEnter = applyAppTableRowEnter;
window.animateAppTableBody = animateAppTableBody;
window.renderAppPagination = renderAppPagination;
window.renderMatchCandidatesTable = renderMatchCandidatesTable;
window.formatMatchScore = formatMatchScore;
window.sortCandidatesByScore = sortCandidatesByScore;
