/**
 * Trakt 配置页面 JavaScript
 */

class TraktConfigPage {
    constructor() {
        this.currentPage = 1;
        this.pageSize = APP_TABLE_PAGE_SIZE;
        this.authWindow = null;
        this.authPollInterval = null;

        // 存储实例到全局变量
        window.traktConfigPage = this;

        this.init();
        this.setupMessageListener();
    }

    /**
     * 初始化页面
     */
    init() {
        this.bindEvents();
        this.loadConfig();
        this.loadSyncStatus();
        this.loadSyncHistory();
    }

    /**
     * 设置消息监听器
     */
    setupMessageListener() {
        window.addEventListener('message', (event) => {
            // 只接受来自同源的消息
            if (event.origin !== window.location.origin) {
                return;
            }

            if (event.data && event.data.type === 'trakt_auth_success') {
                console.log('收到 Trakt 授权成功消息');
                this.handleAuthSuccessFromChildWindow();
            } else if (event.data && event.data.type === 'trakt_auth_error') {
                console.log('收到 Trakt 授权错误消息:', event.data.message);
                this.showAuthError(event.data.message || '授权失败');
            } else if (event.data && event.data.type === 'trakt_auth_retry') {
                console.log('收到重试授权消息');
                this.showAuthStep(1);
                setTimeout(() => {
                    this.startAuthProcess();
                }, 500);
            }
        });
    }

    /**
     * 绑定事件监听器
     */
    bindEvents() {
        // 授权按钮
        document.getElementById('auth-button').addEventListener('click', () => {
            this.showAuthModal();
        });

        // 断开连接按钮
        document.getElementById('disconnect-button').addEventListener('click', () => {
            this.disconnectTrakt();
        });

        // 保存同步配置表单
        document.getElementById('sync-config-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveConfig();
        });

        // 保存 API 配置表单
        document.getElementById('api-config-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveApiConfig();
        });

        // 保存凭证配置表单
        document.getElementById('auth-mode-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveAuthMode();
        });

        // 手动同步按钮
        document.getElementById('manual-sync-button').addEventListener('click', () => {
            this.triggerManualSync(false);
        });

        // 全量同步按钮
        document.getElementById('full-sync-button').addEventListener('click', () => {
            this.triggerManualSync(true);
        });

        // 刷新历史按钮
        document.getElementById('refresh-history').addEventListener('click', () => {
            this.loadSyncHistory();
        });

        const syncHistoryDetailModalEl = document.getElementById('syncHistoryDetailModal');
        if (syncHistoryDetailModalEl) {
            this.syncHistoryDetailModal = new bootstrap.Modal(syncHistoryDetailModalEl);
        }
        bindAppTableMobileRowClick('#sync-history-table', (recordId) => {
            this.showSyncHistoryDetail(recordId);
        });

        // 授权模态框按钮
        document.getElementById('start-auth-button').addEventListener('click', () => {
            this.startAuthProcess();
        });

        document.getElementById('cancel-auth-button').addEventListener('click', () => {
            this.cancelAuthProcess();
        });

        document.getElementById('retry-auth-button').addEventListener('click', () => {
            this.showAuthStep(1);
            this.startAuthProcess();
        });

        // 启用 API 配置表单的保存按钮
        const apiSaveButton = document.querySelector('#api-config-form button[type="submit"]');
        if (apiSaveButton) {
            apiSaveButton.disabled = false;
        }
    }

    /**
     * 显示通知（委托全局 showAlert，保持调用签名兼容）
     */
    showNotification(message, type = 'info') {
        if (typeof window.showAlert === 'function') {
            window.showAlert(message, type, 3000);
        }
    }

    /**
     * 显示加载状态
     */
    showLoading(elementId, message = '加载中...') {
        const element = document.getElementById(elementId);
        if (element) {
            element.innerHTML = `
                <div class="d-flex align-items-center">
                    <div class="loading-spinner me-2"></div>
                    <span>${message}</span>
                </div>
            `;
        }
    }

    /**
     * 显示错误状态
     */
    showError(elementId, message) {
        const element = document.getElementById(elementId);
        if (element) {
            element.innerHTML = `
                <div class="text-danger">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    ${message}
                </div>
            `;
        }
    }

    /**
     * 加载 Trakt 配置
     */
    async loadConfig() {
        try {
            this.showLoading('connection-status', '正在检查连接状态...');
            const config = await apiFetch('/api/trakt/config');
            this.updateConfigDisplay(config);
        } catch (error) {
            console.error('加载配置失败:', error);
            this.showError('connection-status', '加载配置失败');
            this.updateConfigDisplay(null);
        }
    }

    /**
     * 更新配置显示
     */
    updateConfigDisplay(config) {
        const connectionStatus = document.getElementById('connection-status');
        const connectionDetails = document.getElementById('connection-details');
        const authButton = document.getElementById('auth-button');
        const disconnectButton = document.getElementById('disconnect-button');
        const syncSaveButton = document.querySelector('#sync-config-form button[type="submit"]');
        const apiSaveButton = document.querySelector('#api-config-form button[type="submit"]');
        const syncEnabled = document.getElementById('sync-enabled');
        const syncInterval = document.getElementById('sync-interval');

        // API 配置表单元素
        const clientId = document.getElementById('client-id');
        const clientSecret = document.getElementById('client-secret');
        const redirectUri = document.getElementById('redirect-uri');

        // API 配置表单应该始终可用
        apiSaveButton.disabled = false;

        if (!config) {
            // 没有配置
            connectionStatus.innerHTML = `
                <i class="bi bi-x-circle-fill text-danger me-2"></i>
                <span class="status-disconnected">未连接 Trakt</span>
            `;
            connectionDetails.textContent = '请先完成 Trakt 授权';
            authButton.disabled = false;
            disconnectButton.disabled = true;
            syncSaveButton.disabled = true;
            // 无配置时仍允许填写 Bearer 凭证直接创建（不依赖 OAuth 授权）
            const authModeSaveButton = document.querySelector('#auth-mode-form button[type="submit"]');
            if (authModeSaveButton) {
                authModeSaveButton.disabled = false;
            }
            const bearerStatusInfo = document.getElementById('bearer-status-info');
            if (bearerStatusInfo) {
                bearerStatusInfo.innerHTML = '<span class="text-muted"><i class="bi bi-dash-circle me-1"></i>未配置 Bearer 凭证</span>';
            }
            return;
        }

        // 更新连接状态
        if (config.is_connected) {
            connectionStatus.innerHTML = `
                <i class="bi bi-check-circle-fill text-success me-2"></i>
                <span class="status-connected">已连接 Trakt</span>
            `;
            connectionDetails.innerHTML = `
                用户ID: ${config.user_id} |
                最后同步: ${config.last_sync_time ? this.formatDate(config.last_sync_time) : '从未同步'} |
                令牌过期: ${config.token_expires_at ? this.formatDate(config.token_expires_at) : '未知'}
            `;
            authButton.disabled = true;
            disconnectButton.disabled = false;
            syncSaveButton.disabled = false;
        } else {
            connectionStatus.innerHTML = `
                <i class="bi bi-exclamation-triangle-fill text-warning me-2"></i>
                <span class="status-pending">连接异常</span>
            `;
            connectionDetails.textContent = 'Trakt 授权已过期或无效';
            authButton.disabled = false;
            disconnectButton.disabled = false;
            syncSaveButton.disabled = false;
        }

        syncEnabled.checked = config.enabled;
        syncInterval.value = config.sync_interval || '0 */6 * * *';
        
        const syncFilterEnabled = document.getElementById('sync-filter-enabled');
        if (syncFilterEnabled) {
            syncFilterEnabled.checked = config.sync_filter_enabled !== false;
        }
        clientId.value = config.client_id || '';
        // 不回传 client_secret 明文，仅根据是否已配置调整 placeholder 提示
        clientSecret.value = '';
        clientSecret.placeholder = config.client_secret_configured
            ? '已配置，留空则不修改'
            : '从 trakt.tv/oauth/applications 获取';
        redirectUri.value = config.redirect_uri || 'http://localhost:8000/api/trakt/auth/callback';

        // 凭证模式
        const authTypeOauth = document.getElementById('auth-type-oauth');
        const authTypeBearer = document.getElementById('auth-type-bearer');
        const authModeSaveButton = document.querySelector('#auth-mode-form button[type="submit"]');
        const accessTokenInput = document.getElementById('bearer-access-token');
        const refreshTokenInput = document.getElementById('bearer-refresh-token');
        const bearerStatusInfo = document.getElementById('bearer-status-info');

        if (authTypeOauth && authTypeBearer) {
            authTypeOauth.checked = config.auth_type !== 'bearer';
            authTypeBearer.checked = config.auth_type === 'bearer';
        }
        if (authModeSaveButton) {
            authModeSaveButton.disabled = false;
        }
        // 不回显 token；已配置时留空表示不修改
        if (accessTokenInput) {
            accessTokenInput.value = '';
            accessTokenInput.placeholder = config.token_configured
                ? '已配置，留空则不修改'
                : '粘贴 access_token（32 字符）';
        }
        if (refreshTokenInput) {
            refreshTokenInput.value = '';
            refreshTokenInput.placeholder = config.token_configured
                ? '已配置，留空则不修改'
                : '粘贴 refresh_token（32 字符）';
        }
        if (bearerStatusInfo) {
            bearerStatusInfo.innerHTML = this.renderTokenStatus(config);
        }
    }

    /**
     * 渲染 Bearer 凭证状态信息（token 本身绝不回显）
     */
    renderTokenStatus(config) {
        if (!config || !config.token_configured) {
            return '<span class="text-muted"><i class="bi bi-dash-circle me-1"></i>未配置 Bearer 凭证</span>';
        }

        const status = config.token_status || 'not_configured';
        const expiresAt = config.token_expires_at;

        let icon = '<i class="bi bi-check-circle text-success me-1"></i>';
        let statusText = '有效';
        if (status === 'expired') {
            icon = '<i class="bi bi-exclamation-triangle-fill text-warning me-1"></i>';
            statusText = '已失效，请重新填写';
        }

        const parts = [`<span>${icon}${statusText}</span>`];
        if (expiresAt) {
            const remainDays = Math.ceil((expiresAt - Math.floor(Date.now() / 1000)) / 86400);
            parts.push(`<span class="ms-2 text-muted">剩余 ${remainDays > 0 ? remainDays : 0} 天</span>`);
        }
        return parts.join('');
    }

    /**
     * 保存凭证模式与 Bearer 凭证（留空表示不修改）
     */
    async saveAuthMode() {
        const authType = document.querySelector('input[name="auth_type"]:checked');
        if (!authType) {
            this.showNotification('请选择凭证模式', 'warning');
            return;
        }
        const accessToken = document.getElementById('bearer-access-token');
        const refreshToken = document.getElementById('bearer-refresh-token');

        const config = {
            auth_type: authType.value
        };
        // 留空表示不修改；非空则一并提交（后端会同时校验两值并立即验证）
        if (accessToken && accessToken.value.trim()) {
            config.access_token = accessToken.value.trim();
        }
        if (refreshToken && refreshToken.value.trim()) {
            config.refresh_token = refreshToken.value.trim();
        }

        try {
            const result = await apiFetch('/api/trakt/config', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(config)
            });
            this.showNotification('凭证配置保存成功', 'success');
            this.updateConfigDisplay(result);
        } catch (error) {
            console.error('保存凭证配置失败:', error);
            this.showNotification(`保存凭证配置失败: ${error.message}`, 'danger');
        }
    }


    /**
     * 加载同步状态
     */
    async loadSyncStatus() {
        try {
            this.showLoading('sync-status', '正在检查同步状态...');
            const status = await apiFetch('/api/trakt/sync/status');
            this.updateSyncStatusDisplay(status);
        } catch (error) {
            console.error('加载同步状态失败:', error);
            this.showError('sync-status', '加载同步状态失败');
        }
    }

    /**
     * 更新同步状态显示
     */
    updateSyncStatusDisplay(status) {
        const syncStatus = document.getElementById('sync-status');
        const syncDetails = document.getElementById('sync-details');
        const manualSyncButton = document.getElementById('manual-sync-button');
        const fullSyncButton = document.getElementById('full-sync-button');

        // 更新状态
        if (status.is_running) {
            syncStatus.innerHTML = `
                <i class="bi bi-arrow-repeat text-primary me-2"></i>
                <span class="status-pending">同步进行中</span>
            `;
            syncDetails.textContent = '正在同步数据到 Bangumi...';
            manualSyncButton.disabled = true;
            fullSyncButton.disabled = true;
        } else {
            syncStatus.innerHTML = `
                <i class="bi bi-check-circle-fill text-success me-2"></i>
                <span class="status-connected">同步已就绪</span>
            `;
            syncDetails.innerHTML = `
                最后同步: ${status.last_sync_time ? this.formatDate(status.last_sync_time) : '从未同步'} |
                下次同步: ${status.next_sync_time ? this.formatDate(status.next_sync_time) : '未知'} |
                成功率: ${status.total_count > 0 ? Math.round((status.success_count / status.total_count) * 100) : 0}%
            `;
            manualSyncButton.disabled = false;
            fullSyncButton.disabled = false;
        }
    }

    /**
     * 同步历史表格加载态
     */
    setSyncHistoryLoading(show) {
        setAppTableLoading(show, 'sync-history-table-wrap', 'sync-history-loading');
    }

    /**
     * 加载同步历史
     */
    async loadSyncHistory() {
        try {
            this.setSyncHistoryLoading(true);

            const result = await apiFetch(`/api/records?limit=${this.pageSize}&offset=${(this.currentPage - 1) * this.pageSize}&source=trakt`);
            const data = result.data;
            this.updateSyncHistoryDisplay(data);
        } catch (error) {
            console.error('加载同步历史失败:', error);
            this.showError('sync-history-body', '加载同步历史失败');
        } finally {
            this.setSyncHistoryLoading(false);
        }
    }

    /**
     * 更新同步历史显示
     */
    updateSyncHistoryDisplay(data) {
        const tbody = document.getElementById('sync-history-body');

        const total = data?.total || 0;
        const records = data?.records || [];

        const totalPages = total > 0 ? Math.ceil(total / this.pageSize) : 0;

        if (totalPages > 0 && this.currentPage > totalPages) {
            this.currentPage = totalPages;
            this.loadSyncHistory();
            return;
        }

        if (!records || records.length === 0) {
            if (this.currentPage > 1) {
                this.currentPage--;
                this.loadSyncHistory();
                return;
            }

            tbody.innerHTML = `
                <tr>
                    <td colspan="6">
                        ${createAppEmptyStateHtml('暂无同步记录')}
                    </td>
                </tr>
            `;
            animateAppTableBody(tbody, 'sync-history-table-wrap');
            renderAppPagination({
                total,
                currentPage: this.currentPage,
                limit: this.pageSize,
                onPageChange: (page) => {
                    this.currentPage = page;
                    this.loadSyncHistory();
                },
            });
            return;
        }

        let rows = '';
        records.forEach(record => {
            const mt = (record.media_type || 'episode').toLowerCase();
            const message = this.escapeHtml(record.message);

            const title = this.escapeHtml(record.title || record.ori_title || '—');
            rows += `
                <tr data-record-id="${record.id}">
                    <td class="col-hide-sm col-hide-md">${renderMediaTypeBadge(mt)}</td>
                    <td class="col-hide-sm">${formatDate(record.timestamp)}</td>
                    <td class="records-table-col-title" title="${title}">${title}</td>
                    <td>S${String(record.season).padStart(2, '0')}E${String(record.episode).padStart(2, '0')}</td>
                    <td>${renderSyncStatusBadge(record.status)}</td>
                    <td class="col-hide-sm col-hide-md text-truncate text-truncate-cell" title="${message}">${message}</td>
                </tr>
            `;
        });

        tbody.innerHTML = rows;
        animateAppTableBody(tbody, 'sync-history-table-wrap');

        renderAppPagination({
            total,
            currentPage: this.currentPage,
            limit: this.pageSize,
            onPageChange: (page) => {
                this.currentPage = page;
                this.loadSyncHistory();
            },
        });
    }

    /**
     * 显示同步历史详情（移动端点击行）
     */
    async showSyncHistoryDetail(recordId) {
        try {
            const result = await apiFetch(`/api/records/${recordId}`);
            if (result.status !== 'success' || !result.data) {
                throw new Error('记录不存在');
            }

            const record = result.data;
            const content = document.getElementById('sync-history-detail-content');
            if (!content) return;

            const message = this.escapeHtml(record.message || '—');
            content.innerHTML = `
                <div class="row">
                    <div class="col-md-6">
                        <strong>类型:</strong> ${renderMediaTypeBadge(record.media_type)}
                    </div>
                    <div class="col-md-6">
                        <strong>时间:</strong> ${formatDate(record.timestamp)}
                    </div>
                </div>
                <div class="row mt-2">
                    <div class="col-md-6">
                        <strong>标题:</strong> ${this.escapeHtml(record.bgm_title || record.title || '—')}
                    </div>
                    <div class="col-md-6">
                        <strong>季/集:</strong> S${String(record.season).padStart(2, '0')}E${String(record.episode).padStart(2, '0')}
                    </div>
                </div>
                <div class="row mt-2">
                    <div class="col-md-6">
                        <strong>状态:</strong> ${renderSyncStatusBadge(record.status)}
                    </div>
                    <div class="col-md-6">
                        <strong>来源:</strong> ${renderSourceBadge(record.source)}
                    </div>
                </div>
                <div class="row mt-2">
                    <div class="col-12">
                        <strong>消息:</strong>
                        <div class="mt-1 text-break">${message}</div>
                    </div>
                </div>
            `;

            if (this.syncHistoryDetailModal) {
                this.syncHistoryDetailModal.show();
            }
        } catch (error) {
            console.error('加载同步历史详情失败:', error);
            this.showNotification('加载同步历史详情失败', 'danger');
        }
    }

    /**
     * 保存配置
     */
    async saveConfig() {
        const form = document.getElementById('sync-config-form');
        const formData = new FormData(form);

        const config = {
            enabled: formData.get('enabled') === 'on',
            sync_interval: formData.get('sync_interval'),
            sync_filter_enabled: formData.get('sync_filter_enabled') === 'on'
        };

        try {
            const result = await apiFetch('/api/trakt/config', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(config)
            });

            this.showNotification('同步配置保存成功', 'success');
            this.updateConfigDisplay(result)
        } catch (error) {
            console.error('保存配置失败:', error);
            this.showNotification(`保存配置失败: ${error.message}`, 'danger');
        }
    }

    /**
     * 保存 API 配置
     */
    async saveApiConfig() {
        const form = document.getElementById('api-config-form');
        const formData = new FormData(form);

        const apiConfig = {
            client_id: formData.get('client_id') || '',
            client_secret: formData.get('client_secret') || '',
            redirect_uri: formData.get('redirect_uri') || 'http://localhost:8000/api/trakt/auth/callback'
        };

        try {
            await apiFetch('/api/trakt/config/api', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(apiConfig)
            });

            this.showNotification('API 配置保存成功', 'success');
            // 更新配置显示
            this.loadConfig();
        } catch (error) {
            console.error('保存 API 配置失败:', error);
            this.showNotification(`保存 API 配置失败: ${error.message}`, 'danger');
        }
    }

    /**
     * 触发手动同步
     */
    async triggerManualSync(fullSync = false) {
        try {
            // 获取用户ID（从配置中）
            const config = await apiFetch('/api/trakt/config');
            const user_id = config.user_id || 'default_user';

            const result = await apiFetch('/api/trakt/sync/manual', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: user_id,
                    full_sync: fullSync
                })
            });

            this.showNotification(`同步任务已提交: ${result.message}`, 'success');

            // 刷新状态
            setTimeout(() => {
                this.loadSyncStatus();
            }, 1000);

        } catch (error) {
            console.error('触发同步失败:', error);
            this.showNotification(`触发同步失败: ${error.message}`, 'danger');
        }
    }

    /**
     * 断开 Trakt 连接
     */
    async disconnectTrakt() {
        if (!confirm('确定要断开 Trakt 连接吗？断开后需要重新授权才能使用。')) {
            return;
        }

        try {
            const result = await apiFetch('/api/trakt/disconnect', {
                method: 'DELETE'
            });

            this.showNotification(result.message, 'success');

            // 刷新配置
            setTimeout(() => {
                this.loadConfig();
            }, 1000);

        } catch (error) {
            console.error('断开连接失败:', error);
            this.showNotification(`断开连接失败: ${error.message}`, 'danger');
        }
    }

    /**
     * 显示授权模态框
     */
    showAuthModal() {
        const modal = new bootstrap.Modal(document.getElementById('authModal'));
        this.showAuthStep(1);
        modal.show();
    }

    /**
     * 显示授权步骤
     */
    showAuthStep(step) {
        // 隐藏所有步骤
        document.getElementById('auth-step-1').classList.add('d-none');
        document.getElementById('auth-step-2').classList.add('d-none');
        document.getElementById('auth-step-3').classList.add('d-none');
        document.getElementById('auth-step-error').classList.add('d-none');

        // 显示指定步骤
        document.getElementById(`auth-step-${step}`).classList.remove('d-none');
    }

    /**
     * 开始授权流程
     */
    async startAuthProcess() {
        try {
            this.showAuthStep(2);

            // 获取用户ID（从配置中或使用默认）
            const config = await apiFetch('/api/trakt/config');
            const user_id = config.user_id || 'default_user';

            // 初始化授权
            const authData = await apiFetch('/api/trakt/auth/init', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: user_id
                })
            });

            // 打开授权窗口
            this.authWindow = window.open(
                authData.auth_url,
                'Trakt Auth',
                'width=600,height=700,scrollbars=yes'
            );

            if (!this.authWindow) {
                throw new Error('无法打开授权窗口，请检查浏览器弹窗设置');
            }

            // 开始轮询授权状态
            this.startAuthPolling(authData.state);

        } catch (error) {
            console.error('启动授权失败:', error);
            this.showAuthError(error.message);
        }
    }

    /**
     * 开始轮询授权状态
     */
    startAuthPolling(_state) {
        // 清理现有轮询
        if (this.authPollInterval) {
            clearInterval(this.authPollInterval);
        }

        let pollCount = 0;
        const maxPolls = 60; // 最多轮询5分钟（每5秒一次）

        this.authPollInterval = setInterval(async () => {
            pollCount++;

            try {
                // 检查授权窗口是否已关闭
                // 如果窗口关闭，可能是用户手动关闭或授权成功后被关闭
                // 不立即显示错误，继续轮询检查后端状态
                if (this.authWindow && this.authWindow.closed) {
                    // 窗口已关闭，但我们还不知道是否成功
                    // 可以尝试检查后端状态，或者等待成功消息
                    // 暂时继续轮询，如果超过最大轮询次数再显示错误
                }

                // 检查授权状态
                const isAuthorized = await this.checkAuthStatus();
                if (isAuthorized) {
                    clearInterval(this.authPollInterval);
                    this.handleAuthSuccess();
                    return;
                }

                // 如果超过最大轮询次数，停止轮询
                if (pollCount >= maxPolls) {
                    clearInterval(this.authPollInterval);

                    // 检查窗口是否已关闭
                    if (this.authWindow && this.authWindow.closed) {
                        this.showAuthError('授权超时或窗口已关闭，请检查是否授权成功');
                    } else {
                        this.showAuthError('授权超时，请重试');
                    }
                }

            } catch (error) {
                console.error('轮询授权状态失败:', error);
                clearInterval(this.authPollInterval);
                this.showAuthError(error.message);
            }
        }, 5000); // 每5秒轮询一次

        // 设置超时（备用）
        setTimeout(() => {
            if (this.authPollInterval) {
                clearInterval(this.authPollInterval);
                this.showAuthError('授权超时，请重试');
            }
        }, 300000); // 5分钟超时
    }

    /**
     * 取消授权流程
     */
    cancelAuthProcess() {
        // 清理轮询
        if (this.authPollInterval) {
            clearInterval(this.authPollInterval);
            this.authPollInterval = null;
        }

        // 关闭授权窗口
        if (this.authWindow && !this.authWindow.closed) {
            this.authWindow.close();
        }

        // 隐藏模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('authModal'));
        if (modal) {
            modal.hide();
        }
    }

    /**
     * 显示授权错误
     */
    showAuthError(message) {
        document.getElementById('auth-error-message').textContent = message;
        this.showAuthStep('error');
    }

    /**
     * 处理来自子窗口的授权成功消息
     */
    handleAuthSuccessFromChildWindow() {
        this.handleAuthSuccess();
    }

    /**
     * 处理授权成功
     */
    handleAuthSuccess() {
        // 清理轮询
        if (this.authPollInterval) {
            clearInterval(this.authPollInterval);
            this.authPollInterval = null;
        }

        // 更新UI
        this.showAuthStep(3);

        // 关闭授权窗口（如果还开着）
        if (this.authWindow && !this.authWindow.closed) {
            this.authWindow.close();
        }

        // 刷新配置
        setTimeout(() => {
            this.loadConfig();
        }, 1000);

        // 隐藏模态框
        setTimeout(() => {
            const modal = bootstrap.Modal.getInstance(document.getElementById('authModal'));
            if (modal) {
                modal.hide();
            }
        }, 2000);
    }

    /**
     * 检查授权状态
     */
    async checkAuthStatus() {
        try {
            const config = await apiFetch('/api/trakt/config');
            return config.is_connected === true;
        } catch (error) {
            console.error('检查授权状态失败:', error);
            return false;
        }
    }

    /**
     * 格式化日期时间
     */
    formatDate(timestamp) {
        if (!timestamp) return '未知';

        try {
            const date = new Date(timestamp * 1000);
            return date.toLocaleString('zh-CN');
        } catch (e) {
            return '无效时间';
        }
    }

    /**
     * HTML 转义
     */
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    new TraktConfigPage();
});

// 全局函数，供子窗口调用
function handleTraktAuthSuccess() {
    // 重新加载配置
    const page = window.traktConfigPage;
    if (page && typeof page.loadConfig === 'function') {
        page.loadConfig();
        // 隐藏授权模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('authModal'));
        if (modal) {
            modal.hide();
        }
    }
}