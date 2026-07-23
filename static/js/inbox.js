/**
 * 控制台收件箱：远程公告 + 本地同步失败通知
 */
(function () {
    'use strict';

    var POLL_MS = 60000;
    var _announcements = [];
    var _announcementModal = null;
    var _activeAnnouncementId = null;
    var _pollTimer = null;

    function escapeHtml(text) {
        if (text == null) return '';
        var div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    function levelBadgeClass(level) {
        if (level === 'important') return 'bg-danger';
        if (level === 'warning') return 'bg-warning text-dark';
        return 'bg-info text-dark';
    }

    function levelLabel(level) {
        if (level === 'important') return '重要';
        if (level === 'warning') return '提醒';
        return '信息';
    }

    function formatRelativeTime(isoOrLocal) {
        if (!isoOrLocal) return '';
        var d = new Date(isoOrLocal);
        if (isNaN(d.getTime())) return String(isoOrLocal);
        var diff = Date.now() - d.getTime();
        var mins = Math.floor(diff / 60000);
        if (mins < 1) return '刚刚';
        if (mins < 60) return mins + ' 分钟前';
        var hours = Math.floor(mins / 60);
        if (hours < 24) return hours + ' 小时前';
        var days = Math.floor(hours / 24);
        if (days < 30) return days + ' 天前';
        return d.toLocaleDateString('zh-CN');
    }

    function formatPublishedTime(isoOrLocal) {
        if (!isoOrLocal) {
            return { absolute: '', relative: '', datetime: '' };
        }
        var d = new Date(isoOrLocal);
        if (isNaN(d.getTime())) {
            return { absolute: String(isoOrLocal), relative: '', datetime: '' };
        }
        var absolute = d.toLocaleString('zh-CN', {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        });
        var pad = function (n) {
            return n < 10 ? '0' + n : String(n);
        };
        var datetime =
            d.getFullYear() +
            '-' +
            pad(d.getMonth() + 1) +
            '-' +
            pad(d.getDate()) +
            'T' +
            pad(d.getHours()) +
            ':' +
            pad(d.getMinutes());
        return {
            absolute: absolute,
            relative: formatRelativeTime(isoOrLocal),
            datetime: datetime,
        };
    }

    async function fetchJson(url, options) {
        var response = await fetch(appUrl(url), Object.assign({ credentials: 'include' }, options || {}));
        if (response.status === 401) {
            window.location.href = appUrl('/login');
            throw new Error('未登录');
        }
        if (!response.ok) {
            throw new Error('HTTP ' + response.status);
        }
        return response.json();
    }

    function updateBadge(summary) {
        var badge = document.getElementById('inboxBadge');
        if (!badge) return;
        var total = (summary && summary.total) || 0;
        if (total > 0) {
            badge.textContent = total > 99 ? '99+' : String(total);
            badge.classList.remove('d-none');
        } else {
            badge.classList.add('d-none');
        }
    }

    function updateRemoteAlert(remoteError) {
        var alertEl = document.getElementById('inboxRemoteAlert');
        var textEl = document.getElementById('inboxRemoteAlertText');
        if (!alertEl || !textEl) return;
        if (remoteError) {
            textEl.textContent = '远程公告拉取失败：' + remoteError;
            alertEl.classList.remove('d-none');
        } else {
            alertEl.classList.add('d-none');
            textEl.textContent = '';
        }
    }

    function getActiveInboxCategory() {
        var notifTab = document.getElementById('inboxTabNotifications');
        if (notifTab && notifTab.classList.contains('active')) {
            return 'notification';
        }
        return 'announcement';
    }

    async function refreshSummary() {
        try {
            var result = await fetchJson('/api/inbox/summary');
            if (result.status === 'success' && result.data) {
                updateBadge(result.data);
            }
        } catch (e) {
            console.warn('收件箱摘要刷新失败', e);
        }
    }

    function renderEmpty(container, message) {
        container.innerHTML =
            '<div class="inbox-list__empty text-muted small px-3 py-4 text-center">' +
            escapeHtml(message) +
            '</div>';
    }

    function renderAnnouncements(items) {
        var container = document.getElementById('inboxAnnouncementsList');
        if (!container) return;
        _announcements = items || [];
        if (!_announcements.length) {
            renderEmpty(container, '暂无公告');
            return;
        }
        container.innerHTML = _announcements
            .map(function (item) {
                var unread = !item.read;
                return (
                    '<button type="button" class="inbox-card inbox-card--announcement' +
                    (unread ? ' inbox-card--unread' : ' inbox-card--read') +
                    '" data-announcement-id="' +
                    escapeHtml(item.id) +
                    '">' +
                    '<span class="inbox-card__bar inbox-card__bar--' +
                    escapeHtml(item.level || 'info') +
                    '"></span>' +
                    '<span class="inbox-card__content">' +
                    '<span class="d-flex align-items-center gap-2 mb-1">' +
                    '<span class="badge rounded-pill ' +
                    levelBadgeClass(item.level) +
                    ' inbox-card__level">' +
                    levelLabel(item.level) +
                    '</span>' +
                    (unread ? '<span class="inbox-card__dot" aria-hidden="true"></span>' : '') +
                    '<span class="inbox-card__time small text-muted ms-auto">' +
                    formatRelativeTime(item.published_at) +
                    '</span>' +
                    '</span>' +
                    '<span class="inbox-card__title">' +
                    escapeHtml(item.title) +
                    '</span>' +
                    '</span>' +
                    '</button>'
                );
            })
            .join('');
    }

    function renderNotifications(items) {
        var container = document.getElementById('inboxNotificationsList');
        if (!container) return;
        if (!items || !items.length) {
            renderEmpty(container, '暂无通知');
            return;
        }
        container.innerHTML = items
            .map(function (item) {
                var unread = !item.read;
                var ids = item.notification_ids && item.notification_ids.length
                    ? item.notification_ids.join(',')
                    : String(item.id);
                return (
                    '<button type="button" class="inbox-card inbox-card--notification' +
                    (unread ? ' inbox-card--unread' : ' inbox-card--read') +
                    '" data-notification-id="' +
                    item.id +
                    '" data-notification-ids="' +
                    escapeHtml(ids) +
                    '" data-ref-id="' +
                    (item.ref_id || '') +
                    '">' +
                    '<span class="inbox-card__icon"><i class="bi bi-exclamation-circle"></i></span>' +
                    '<span class="inbox-card__content">' +
                    '<span class="d-flex align-items-center gap-2 mb-1">' +
                    '<span class="inbox-card__title">' +
                    escapeHtml(item.title) +
                    '</span>' +
                    (unread ? '<span class="inbox-card__dot" aria-hidden="true"></span>' : '') +
                    '<span class="inbox-card__time small text-muted ms-auto">' +
                    formatRelativeTime(item.created_at) +
                    '</span>' +
                    '</span>' +
                    (item.body
                        ? '<span class="inbox-card__summary small text-muted">' +
                          escapeHtml(item.body) +
                          '</span>'
                        : '') +
                    '</span>' +
                    '</button>'
                );
            })
            .join('');
    }

    async function loadInboxList() {
        try {
            var result = await fetchJson('/api/inbox?category=all&limit=30');
            if (result.status !== 'success' || !result.data) return;
            updateRemoteAlert(result.data.remote_error);
            renderAnnouncements(result.data.announcements);
            renderNotifications(result.data.notifications);
        } catch (e) {
            console.warn('收件箱列表加载失败', e);
            var ann = document.getElementById('inboxAnnouncementsList');
            var notif = document.getElementById('inboxNotificationsList');
            if (ann) renderEmpty(ann, '加载失败');
            if (notif) renderEmpty(notif, '加载失败');
        }
    }

    function findAnnouncement(id) {
        for (var i = 0; i < _announcements.length; i++) {
            if (_announcements[i].id === id) return _announcements[i];
        }
        return null;
    }

    function openAnnouncementModal(item) {
        if (!item || !_announcementModal) return;
        _activeAnnouncementId = item.id;
        document.getElementById('inboxAnnouncementModalLabel').textContent = item.title;
        var levelEl = document.getElementById('inboxAnnouncementLevel');
        levelEl.textContent = levelLabel(item.level);
        levelEl.className = 'badge rounded-pill inbox-level-badge ' + levelBadgeClass(item.level);
        var timeMeta = formatPublishedTime(item.published_at);
        var timeAbs = document.getElementById('inboxAnnouncementTimeAbs');
        var timeRel = document.getElementById('inboxAnnouncementTimeRel');
        var timeWrap = document.getElementById('inboxAnnouncementTimeWrap');
        if (timeAbs && timeRel && timeWrap) {
            if (timeMeta.absolute) {
                timeAbs.textContent = timeMeta.absolute;
                if (timeMeta.datetime) {
                    timeAbs.setAttribute('datetime', timeMeta.datetime);
                } else {
                    timeAbs.removeAttribute('datetime');
                }
                timeRel.textContent = timeMeta.relative || '';
                timeWrap.classList.toggle('d-none', false);
                timeWrap.querySelector('.inbox-announcement-modal__time-dot').classList.toggle(
                    'd-none',
                    !timeMeta.relative
                );
            } else {
                timeWrap.classList.add('d-none');
            }
        }
        var bodyEl = document.getElementById('inboxAnnouncementBody');
        bodyEl.innerHTML = item.body_html || '<p class="text-muted small mb-0">加载中…</p>';
        bodyEl.querySelectorAll('a[href]').forEach(function (a) {
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer');
        });
        _announcementModal.show();
    }

    async function openAnnouncementById(id) {
        var cached = findAnnouncement(id);
        if (cached) {
            openAnnouncementModal(cached);
        } else {
            openAnnouncementModal({ id: id, title: '公告', level: 'info', published_at: '', body_html: '' });
        }
        try {
            var result = await fetchJson('/api/inbox/announcements/' + encodeURIComponent(id));
            if (result.status === 'success' && result.data) {
                openAnnouncementModal(result.data);
            }
        } catch (e) {
            console.warn('公告详情加载失败', e);
            var bodyEl = document.getElementById('inboxAnnouncementBody');
            if (bodyEl) {
                bodyEl.innerHTML = '<p class="text-danger small mb-0">公告内容加载失败</p>';
            }
        }
    }

    async function markAnnouncementRead(id) {
        try {
            await fetchJson('/api/inbox/announcements/' + encodeURIComponent(id) + '/read', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
        } catch (e) {
            console.warn('标记公告已读失败', e);
        }
    }

    async function markNotificationRead(id) {
        try {
            await fetchJson('/api/inbox/notifications/' + id + '/read', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
        } catch (e) {
            console.warn('标记通知已读失败', e);
        }
    }

    async function readAll() {
        var category = getActiveInboxCategory();
        try {
            await fetchJson('/api/inbox/read-all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ category: category }),
            });
            await refreshSummary();
            await loadInboxList();
            if (typeof showAlert === 'function') {
                var label = category === 'notification' ? '通知' : '公告';
                showAlert('已将当前「' + label + '」全部标为已读', 'success', 2000);
            }
        } catch (e) {
            if (typeof showAlert === 'function') {
                showAlert('操作失败', 'danger');
            }
        }
    }

    function bindEvents() {
        var bellBtn = document.getElementById('inboxBellBtn');
        if (bellBtn) {
            bellBtn.addEventListener('show.bs.dropdown', function () {
                loadInboxList();
            });
        }

        var annList = document.getElementById('inboxAnnouncementsList');
        if (annList) {
            annList.addEventListener('click', function (e) {
                var card = e.target.closest('[data-announcement-id]');
                if (!card) return;
                var id = card.getAttribute('data-announcement-id');
                openAnnouncementById(id);
            });
        }

        var notifList = document.getElementById('inboxNotificationsList');
        if (notifList) {
            notifList.addEventListener('click', async function (e) {
                var card = e.target.closest('[data-notification-id]');
                if (!card) return;
                var notifId = card.getAttribute('data-notification-id');
                await markNotificationRead(notifId);
                await refreshSummary();
                var url = appUrl('/records');
                var refId = card.getAttribute('data-ref-id');
                if (refId) url += '?open=' + encodeURIComponent(refId);
                window.location.href = url;
            });
        }

        var readAllBtn = document.getElementById('inboxReadAllBtn');
        if (readAllBtn) {
            readAllBtn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                readAll();
            });
        }

        var dismissBtn = document.getElementById('inboxAnnouncementDismissBtn');
        var modalEl = document.getElementById('inboxAnnouncementModal');
        if (modalEl) {
            modalEl.addEventListener('hidden.bs.modal', async function () {
                if (_activeAnnouncementId) {
                    await markAnnouncementRead(_activeAnnouncementId);
                    _activeAnnouncementId = null;
                    await refreshSummary();
                    await loadInboxList();
                }
            });
        }
        if (dismissBtn) {
            dismissBtn.addEventListener('click', function () {
                /* hidden.bs.modal 统一处理已读 */
            });
        }
    }

    function init() {
        if (!document.getElementById('inboxBellBtn')) {
            return;
        }
        var modalEl = document.getElementById('inboxAnnouncementModal');
        if (modalEl && typeof bootstrap !== 'undefined') {
            _announcementModal = new bootstrap.Modal(modalEl);
        }
        bindEvents();
        refreshSummary();
        _pollTimer = setInterval(refreshSummary, POLL_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.BangumiInbox = {
        openAnnouncementById: openAnnouncementById
    };
})();
