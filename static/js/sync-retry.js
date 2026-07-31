// 同步重试功能 —— SSE 流式日志弹窗与重试触发

// ========== 同步重试功能（SSE 流式日志） ==========

let _retryEventSource = null;
let _retryLogModal = null;
let _retryDone = false;

/**
 * 根据同步记录跳转到关联的候选详情；若无候选则提示
 * @param {number} recordId - 记录ID
 */
async function viewCandidateByRecord(recordId) {
    if (!recordId) {
        showAlert('记录ID无效', 'danger');
        return;
    }
    try {
        const data = await apiFetch(`/api/records/${recordId}/pending-candidate`);
        if (data && data.status === 'success' && data.data && data.data.record) {
            const candidateId = data.data.record.id;
            window.location.href = appUrl(`/pending-candidates?focus=${candidateId}`);
            return;
        }
        showAlert('该记录无关联候选', 'info');
    } catch (err) {
        console.error('查询候选失败:', err);
        showAlert('查询候选失败', 'danger');
    }
}

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
