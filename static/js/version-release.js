/**
 * 仪表盘版本展示与发行说明 Modal（数据来自 /api/app/release-info）
 */
(function () {
    'use strict';

    /** 与后端 GitHub 缓存（约 5min）同量级，减少 MPA 下每页都请求 release-info */
    var CACHE_PREFIX = 'bangumi-syncer:release-info';
    var TTL_AUTH_MS = 5 * 60 * 1000;
    var TTL_ANON_MS = 2 * 60 * 1000;

    function hasSessionCookie() {
        return /(?:^|;\s*)session_token=/.test(document.cookie || '');
    }

    function cacheStorageKey() {
        return CACHE_PREFIX + (hasSessionCookie() ? ':auth' : ':anon');
    }

    function ttlForBody(body) {
        if (body && body.remote_loaded === false) {
            return TTL_ANON_MS;
        }
        return TTL_AUTH_MS;
    }

    function readCachedReleaseInfo() {
        try {
            var raw = sessionStorage.getItem(cacheStorageKey());
            if (!raw) {
                return null;
            }
            var rec = JSON.parse(raw);
            if (!rec || typeof rec.t !== 'number' || !rec.body) {
                return null;
            }
            if (Date.now() - rec.t > ttlForBody(rec.body)) {
                sessionStorage.removeItem(cacheStorageKey());
                return null;
            }
            return rec.body;
        } catch (e) {
            return null;
        }
    }

    function writeCachedReleaseInfo(body) {
        if (!body || body.github_error) {
            return;
        }
        try {
            sessionStorage.setItem(
                cacheStorageKey(),
                JSON.stringify({ t: Date.now(), body: body }),
            );
        } catch (e) {
            /* 隐私模式等可能不可用 */
        }
    }

    function setTextAll(selector, text) {
        document.querySelectorAll(selector).forEach(function (el) {
            el.textContent = text;
        });
    }

    function setDisplayedVersionText(text) {
        document.querySelectorAll('.js-app-version-pill-label').forEach(function (el) {
            el.textContent = text;
            if (text && text !== '—') {
                el.setAttribute('title', text);
            } else {
                el.removeAttribute('title');
            }
        });
    }

    function setClassAll(selector, className, add) {
        document.querySelectorAll(selector).forEach(function (el) {
            el.classList.toggle(className, add);
        });
    }

    var HEADER_CHIP_ICON_STATES = [
        'loading',
        'latest',
        'upgrade',
        'neutral',
        'error',
    ];

    /** 仪表盘标题行胶囊：左侧状态圆标（绿勾 / 黄箭头等） */
    function setHeaderChipVersionIcon(state) {
        var wraps = document.querySelectorAll(
            '.app-version-banner--header-chip .app-version-banner__header-chip-icon',
        );
        if (!wraps.length) {
            return;
        }
        var iconByState = {
            loading: 'bi-arrow-repeat',
            latest: 'bi-check-lg',
            upgrade: 'bi-arrow-up-circle-fill',
            neutral: 'bi-cpu',
            error: 'bi-exclamation-triangle-fill',
        };
        var iconClass = iconByState[state] || iconByState.neutral;
        wraps.forEach(function (wrap) {
            HEADER_CHIP_ICON_STATES.forEach(function (s) {
                wrap.classList.remove('app-version-banner__header-chip-icon--' + s);
            });
            wrap.classList.toggle('app-version-banner__header-chip-icon--spinning', state === 'loading');
            wrap.classList.add('app-version-banner__header-chip-icon--' + state);
            var i = wrap.querySelector('i');
            if (i) {
                i.className = 'bi ' + iconClass;
            }
        });
    }

    function setBannerLoading(on) {
        document.querySelectorAll('.app-version-banner').forEach(function (el) {
            el.classList.toggle('app-version-banner--loading', on);
        });
    }

    function escapeHtml(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    /** ISO 8601 → 本地常规日期时间（如 2026/1/1 20:00） */
    function formatPublishedAt(iso) {
        if (!iso || typeof iso !== 'string') {
            return '';
        }
        var d = new Date(iso);
        if (Number.isNaN(d.getTime())) {
            return iso;
        }
        try {
            return new Intl.DateTimeFormat('zh-CN', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
            }).format(d);
        } catch (e) {
            return iso;
        }
    }

    function openReleaseNotesModal() {
        var el = document.getElementById('releaseNotesModal');
        if (!el || typeof bootstrap === 'undefined') {
            return;
        }
        var modal = bootstrap.Modal.getOrCreateInstance(el);
        modal.show();
    }

    function setBehindPill(j, listLen) {
        var pill = document.getElementById('releaseNotesBehindPill');
        if (!pill) {
            return;
        }
        var n =
            typeof j.updates_behind === 'number' ? j.updates_behind : listLen;
        if (listLen > 0 && n > 0) {
            pill.classList.remove('d-none');
            pill.textContent = n === 1 ? '差1版' : '落后' + n + '版';
        } else {
            pill.classList.add('d-none');
            pill.textContent = '';
        }
    }

    function renderModal(j) {
        var currentEl = document.getElementById('releaseNotesCurrentVersion');
        var stackEl = document.getElementById('releaseNotesStack');
        var warnEl = document.getElementById('releaseNotesFetchWarn');
        if (!stackEl) {
            return;
        }

        var curDisp =
            j.current_version_display || j.current_version || '—';
        if (currentEl) {
            currentEl.textContent = curDisp;
        }

        if (warnEl) {
            if (j.releases_fetch_error) {
                warnEl.classList.remove('d-none');
                warnEl.textContent = j.releases_fetch_error;
            } else {
                warnEl.classList.add('d-none');
                warnEl.textContent = '';
            }
        }

        stackEl.innerHTML = '';
        var rawNewer = j.newer_releases || [];
        var list = rawNewer.slice();
        if (
            !list.length &&
            j.release_history &&
            j.release_history.length &&
            j.remote_loaded !== false &&
            !j.github_error
        ) {
            list = j.release_history.slice();
        }
        setBehindPill(j, rawNewer.length);

        if (!list.length) {
            var pillEmpty = document.getElementById('releaseNotesBehindPill');
            if (pillEmpty) {
                pillEmpty.classList.add('d-none');
                pillEmpty.textContent = '';
            }
            var empty = document.createElement('p');
            empty.className = 'text-muted small mb-0';
            if (j.remote_loaded === false) {
                empty.textContent =
                    '登录后将显示与 GitHub 的对比与各版发行说明。';
            } else if (j.update_available === true) {
                empty.textContent = '暂无可展示的发行说明。';
            } else {
                empty.textContent = '当前运行版本已不低于远端 latest。';
            }
            stackEl.appendChild(empty);
            return;
        }

        list.forEach(function (item, index) {
            var art = document.createElement('article');
            art.className = 'release-notes-card';

            var head = document.createElement('div');
            head.className = 'release-notes-card__head';

            var h = document.createElement('h4');
            h.className = 'release-notes-card__title';

            var titleTrim = (item.title || '').trim();
            var titleText = titleTrim
                ? titleTrim
                : item.version_display || item.semver || 'Release';

            if (item.html_url) {
                var titleLink = document.createElement('a');
                titleLink.className = 'release-notes-card__title-link';
                titleLink.href = item.html_url;
                titleLink.target = '_blank';
                titleLink.rel = 'noopener noreferrer';
                titleLink.textContent = titleText;
                h.appendChild(titleLink);
            } else {
                h.appendChild(document.createTextNode(titleText));
            }

            var tags = document.createElement('span');
            tags.className = 'release-notes-card__title-tags';

            if (index === 0) {
                var latestBadge = document.createElement('span');
                latestBadge.className =
                    'release-notes-card__title-badge release-notes-card__title-badge--latest';
                latestBadge.textContent = '最新';
                tags.appendChild(latestBadge);
            }

            if (tags.childNodes.length) {
                h.appendChild(tags);
            }

            head.appendChild(h);

            if (item.published_at) {
                var time = document.createElement('time');
                time.className = 'release-notes-card__meta';
                time.setAttribute('datetime', item.published_at);
                time.textContent = formatPublishedAt(item.published_at);
                head.appendChild(time);
            }

            art.appendChild(head);

            var wrap = document.createElement('div');
            wrap.className = 'release-notes-card__body';
            var body = document.createElement('div');
            body.className = 'markdown-body';
            if (item.body_html) {
                body.innerHTML = item.body_html;
            } else {
                body.innerHTML =
                    '<p class="text-muted mb-0">' +
                    escapeHtml('此版本未提供说明正文。') +
                    '</p>';
            }
            wrap.appendChild(body);
            art.appendChild(wrap);

            stackEl.appendChild(art);
        });
    }

    function wireUpdatePills() {
        document.querySelectorAll('.js-app-update-pill').forEach(function (btn) {
            btn.addEventListener('click', function () {
                if (btn.disabled) {
                    return;
                }
                openReleaseNotesModal();
            });
            btn.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    if (!btn.disabled) {
                        openReleaseNotesModal();
                    }
                }
            });
        });
    }

    function resetUpdatePillVisual(btn) {
        btn.classList.remove(
            'app-version-banner__update--busy',
            'app-version-banner__update--neutral',
            'app-version-banner__update--danger',
        );
    }

    /** 仪表盘「检查更新」按钮：与远端对比后的文案与样式（无按钮的页面直接跳过）。 */
    function applyUpdatePillAfterResponse(j) {
        var btns = document.querySelectorAll('.js-app-update-pill');
        if (!btns.length) {
            return;
        }
        setBannerLoading(false);
        btns.forEach(function (btn) {
            resetUpdatePillVisual(btn);
            btn.disabled = false;
        });

        if (!j.remote_loaded) {
            setClassAll('.js-app-github-error', 'd-none', true);
            setClassAll('.js-app-update-pill', 'd-none', false);
            btns.forEach(function (btn) {
                btn.classList.add('app-version-banner__update--neutral');
            });
            setTextAll('.js-app-update-pill-label', '登录后检查');
            setHeaderChipVersionIcon('neutral');
            return;
        }
        if (j.github_error) {
            setClassAll('.js-app-update-pill', 'd-none', false);
            btns.forEach(function (btn) {
                btn.classList.add('app-version-banner__update--danger');
            });
            setTextAll('.js-app-update-pill-label', '检查失败');
            setHeaderChipVersionIcon('error');
            return;
        }

        setClassAll('.js-app-github-error', 'd-none', true);
        var hasNew =
            j.update_available === true ||
            (Array.isArray(j.newer_releases) && j.newer_releases.length > 0);
        setClassAll('.js-app-update-pill', 'd-none', false);
        if (hasNew) {
            setTextAll('.js-app-update-pill-label', '有新版本');
            setHeaderChipVersionIcon('upgrade');
        } else {
            btns.forEach(function (btn) {
                btn.classList.add('app-version-banner__update--neutral');
            });
            setTextAll('.js-app-update-pill-label', '已是最新');
            setHeaderChipVersionIcon('latest');
        }
    }

    function setUpdatePillsFetching() {
        setHeaderChipVersionIcon('loading');
        var btns = document.querySelectorAll('.js-app-update-pill');
        if (!btns.length) {
            return;
        }
        btns.forEach(function (btn) {
            resetUpdatePillVisual(btn);
            btn.classList.add('app-version-banner__update--busy');
            btn.disabled = true;
        });
        setClassAll('.js-app-update-pill', 'd-none', false);
        setTextAll('.js-app-update-pill-label', '获取中…');
    }

    function setUpdatePillsRequestFailed(message) {
        setHeaderChipVersionIcon('error');
        var btns = document.querySelectorAll('.js-app-update-pill');
        if (!btns.length) {
            return;
        }
        setBannerLoading(false);
        btns.forEach(function (btn) {
            resetUpdatePillVisual(btn);
            btn.classList.add('app-version-banner__update--danger');
            btn.disabled = false;
        });
        setClassAll('.js-app-update-pill', 'd-none', false);
        setTextAll('.js-app-update-pill-label', message);
    }

    /** 将接口 JSON 应用到仪表盘与 Modal（与是否来自网络或缓存无关）。 */
    function applyReleaseInfoResponse(j) {
        var verDisp =
            j.current_version_display || j.current_version || '—';

        setDisplayedVersionText(verDisp);

        if (!j.remote_loaded) {
            var pillNl = document.getElementById('releaseNotesBehindPill');
            if (pillNl) {
                pillNl.classList.add('d-none');
                pillNl.textContent = '';
            }
            renderModal({
                remote_loaded: false,
                current_version_display: verDisp,
                current_version: j.current_version,
                releases_fetch_error: null,
                newer_releases: [],
                updates_behind: 0,
            });
            applyUpdatePillAfterResponse(j);
            return;
        }

        if (j.github_error) {
            setClassAll('.js-app-github-error', 'd-none', false);
            setTextAll('.js-app-github-error', j.github_error);
            var curGh = document.getElementById('releaseNotesCurrentVersion');
            if (curGh) {
                curGh.textContent = verDisp;
            }
            var pillGh = document.getElementById('releaseNotesBehindPill');
            if (pillGh) {
                pillGh.classList.add('d-none');
                pillGh.textContent = '';
            }
            var stackGh = document.getElementById('releaseNotesStack');
            if (stackGh) {
                stackGh.textContent = '';
                var pGh = document.createElement('p');
                pGh.className = 'text-danger small mb-0';
                pGh.textContent = j.github_error;
                stackGh.appendChild(pGh);
            }
            var warnGh = document.getElementById('releaseNotesFetchWarn');
            if (warnGh) {
                warnGh.classList.add('d-none');
                warnGh.textContent = '';
            }
            applyUpdatePillAfterResponse(j);
            return;
        }

        renderModal(j);
        applyUpdatePillAfterResponse(j);
    }

    /** 若有 sessionStorage 缓存则立刻渲染，不阻塞首屏。 */
    function applyCachedReleaseInfoIfAny() {
        var c = readCachedReleaseInfo();
        if (c) {
            applyReleaseInfoResponse(c);
        }
    }

    /**
     * 无有效缓存时再请求（由 requestIdleCallback / setTimeout 延后执行，
     * 避免版本接口拖慢或阻塞主文档绘制）。
     */
    async function fetchReleaseInfoWhenNeeded() {
        if (readCachedReleaseInfo()) {
            return;
        }

        setClassAll('.js-app-github-error', 'd-none', true);
        setUpdatePillsFetching();
        setBannerLoading(true);

        try {
            var r = await fetch(appUrl('/api/app/release-info'), {
                method: 'GET',
                credentials: 'include',
                headers: { Accept: 'application/json' },
            });
            if (!r.ok) {
                setUpdatePillsRequestFailed('检查失败');
                return;
            }
            var j = await r.json();
            writeCachedReleaseInfo(j);
            applyReleaseInfoResponse(j);
        } catch (e) {
            setUpdatePillsRequestFailed('网络错误');
        }
    }

    function scheduleFetchReleaseInfoWhenNeeded() {
        function run() {
            fetchReleaseInfoWhenNeeded();
        }
        if (typeof window.requestIdleCallback === 'function') {
            window.requestIdleCallback(run, { timeout: 2500 });
        } else {
            window.setTimeout(run, 200);
        }
    }

    function clearReleaseInfoCache() {
        try {
            sessionStorage.removeItem(CACHE_PREFIX + ':auth');
            sessionStorage.removeItem(CACHE_PREFIX + ':anon');
        } catch (e) {
            /* ignore */
        }
    }

    window.clearBgmReleaseInfoCache = clearReleaseInfoCache;

    document.addEventListener('DOMContentLoaded', function () {
        wireUpdatePills();
        setClassAll('.js-app-github-error', 'd-none', true);
        applyCachedReleaseInfoIfAny();
        scheduleFetchReleaseInfoWhenNeeded();
    });
})();
