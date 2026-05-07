/**
 * 一键升级功能
 * 仅支持直装模式，Docker 模式提示用户手动升级。
 * 使用 SSE（Server-Sent Events）实时接收升级进度。
 */
(function () {
    'use strict';

    var _eventSource = null;

    function appUrl(path) {
        var base = window.__APP_BASE_PATH__ || '';
        return base + path;
    }

    function showElement(id, show) {
        var el = document.getElementById(id);
        if (el) {
            el.classList.toggle('d-none', !show);
        }
    }

    function setText(id, text) {
        var el = document.getElementById(id);
        if (el) {
            el.textContent = text;
        }
    }

    function setProgress(percent) {
        var bar = document.getElementById('upgradeProgressBar');
        if (bar) {
            bar.style.width = percent + '%';
            bar.setAttribute('aria-valuenow', percent);
        }
        setText('upgradePercentText', percent + '%');
    }

    function stopSSE() {
        if (_eventSource) {
            _eventSource.close();
            _eventSource = null;
        }
    }

    function applyUpgradeUI(releaseInfo, env, capable) {
        if (env === 'docker') {
            if (releaseInfo.update_available !== false) {
                showElement('upgradeDockerHint', true);
            }
            showElement('upgradeBtn', false);
            return;
        }

        if (!capable) return;

        var targetVer = releaseInfo.latest_version_display ||
            releaseInfo.latest_version || '';
        setText('upgradeBtnText', targetVer ? '一键升级到 ' + targetVer : '一键升级');
        showElement('upgradeBtn', true);
    }

    /**
     * 由 version-release.js 在弹窗渲染后调用。
     * 优先使用 releaseInfo 中的字段；若缓存数据缺少则回退到 API。
     */
    window.initUpgradeUI = function (releaseInfo) {
        if (!releaseInfo) return;

        // 当已确认无新版本时，不显示升级相关 UI
        if (releaseInfo.update_available === false) {
            showElement('upgradeBtn', false);
            showElement('upgradeDockerHint', false);
            return;
        }

        // 缓存数据可能缺少新字段，回退到 API
        if (releaseInfo.environment === undefined || releaseInfo.upgrade_available === undefined) {
            fetch(appUrl('/api/app/upgrade/status'), {
                method: 'GET',
                credentials: 'include',
                headers: { Accept: 'application/json' },
            })
                .then(function (r) { return r.json(); })
                .then(function (status) {
                    applyUpgradeUI(releaseInfo, status.environment, status.upgrade_capable);
                })
                .catch(function () {});
            return;
        }

        applyUpgradeUI(releaseInfo, releaseInfo.environment, releaseInfo.upgrade_available);
    };

    window.startUpgrade = function () {
        if (!confirm('确定要升级吗？升级前会自动备份数据库，升级过程中服务将短暂不可用。')) {
            return;
        }

        var btn = document.getElementById('upgradeBtn');
        if (btn) btn.disabled = true;
        showElement('upgradeProgress', true);
        showElement('upgradeBtn', false);
        showElement('upgradeError', false);
        setText('upgradeStatusText', '准备开始升级...');
        setProgress(0);

        fetch(appUrl('/api/app/upgrade'), {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                Accept: 'application/json',
            },
            body: JSON.stringify({}),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'error') {
                    showUpgradeError(data.detail || '升级启动失败');
                    return;
                }
                startSSE(data.upgrade_id);
            })
            .catch(function (e) {
                showUpgradeError('请求失败: ' + e.message);
            });
    };

    function startSSE(upgradeId) {
        stopSSE();

        var url = appUrl('/api/app/upgrade/progress?upgrade_id=' + encodeURIComponent(upgradeId));
        _eventSource = new EventSource(url);

        _eventSource.addEventListener('progress', function (e) {
            try {
                var data = JSON.parse(e.data);
                setText('upgradeStatusText', data.message || '');
                setProgress(data.percent || 0);

                if (data.stage === 'done') {
                    stopSSE();
                    onUpgradeDone();
                } else if (data.stage === 'error') {
                    stopSSE();
                    showUpgradeError(data.error || data.message || '升级失败');
                }
            } catch (_) {}
        });

        _eventSource.addEventListener('error', function (e) {
            try {
                var data = JSON.parse(e.data);
                stopSSE();
                showUpgradeError(data.error || '连接中断');
            } catch (_) {
                // EventSource 自动重试，非业务错误不处理
            }
        });

        _eventSource.onerror = function () {
            // SSE 连接断开（服务端关闭或网络中断），不做额外处理
            // 业务层面的 done/error 已通过事件处理
        };
    }

    function onUpgradeDone() {
        setText('upgradeStatusText', '升级完成，正在重启服务...');
        setProgress(100);
        // 延迟 2 秒后自动重启，确保 SSE 事件已被前端接收
        setTimeout(function () {
            triggerRestartInternal();
        }, 2000);
    }

    function triggerRestartInternal() {
        setText('upgradeStatusText', '正在重启...');

        fetch(appUrl('/api/app/upgrade/restart'), {
            method: 'POST',
            credentials: 'include',
            headers: { Accept: 'application/json' },
        }).catch(function () {});

        pollHealthUntilReady(0);
    }

    function pollHealthUntilReady(attempt) {
        var maxAttempts = 60;
        if (attempt >= maxAttempts) {
            setText('upgradeStatusText', '服务重启超时，请手动刷新页面。');
            return;
        }

        setTimeout(function () {
            fetch(appUrl('/health'), {
                method: 'GET',
                cache: 'no-store',
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.status === 'healthy') {
                        setText('upgradeStatusText', '服务已重启，正在刷新页面...');
                        setTimeout(function () {
                            // 清除版本信息缓存，避免刷新后仍显示"有新版本"
                            if (window.clearBgmReleaseInfoCache) {
                                window.clearBgmReleaseInfoCache();
                            }
                            window.location.reload();
                        }, 1000);
                    }
                })
                .catch(function () {
                    pollHealthUntilReady(attempt + 1);
                });
        }, 2000);
    }

    window.triggerRestart = function () {
        var btn = document.getElementById('upgradeRestartBtn');
        if (btn) btn.disabled = true;
        triggerRestartInternal();
    };

    function showUpgradeError(message) {
        stopSSE();
        showElement('upgradeProgress', false);
        showElement('upgradeError', true);
        setText('upgradeErrorMessage', message);
        var btn = document.getElementById('upgradeBtn');
        if (btn) btn.disabled = false;
        showElement('upgradeBtn', true);
    }
})();
