/**
 * 侧栏版本展示与「有新版本」说明 Modal（数据来自 /api/app/release-info）
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

    function setClassAll(selector, className, add) {
        document.querySelectorAll(selector).forEach(function (el) {
            el.classList.toggle(className, add);
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

    /** 与 tag / semver 比较用：去首尾空白、去前缀 v、小写。 */
    function normReleaseVersion(s) {
        return String(s || '')
            .trim()
            .replace(/^v/i, '')
            .toLowerCase();
    }

    /**
     * 是否为本机当前版本对应发行：用 tag_name / semver / version_display 与运行版本比对
     *（不用 title，避免标题里符号、文案干扰）。
     */
    function releaseTagMarksCurrent(item, curDisp, curRaw) {
        var curNorms = [];
        if (curRaw) {
            curNorms.push(normReleaseVersion(curRaw));
        }
        if (curDisp) {
            var d = normReleaseVersion(curDisp);
            if (curNorms.indexOf(d) < 0) {
                curNorms.push(d);
            }
        }
        if (!curNorms.length) {
            return false;
        }

        var sem = normReleaseVersion(item.semver);
        var tag = normReleaseVersion(item.tag_name);
        var vd = normReleaseVersion(item.version_display);

        for (var i = 0; i < curNorms.length; i++) {
            var c = curNorms[i];
            if (!c) {
                continue;
            }
            if (sem && sem === c) {
                return true;
            }
            if (tag && tag === c) {
                return true;
            }
            if (vd && vd === c) {
                return true;
            }
        }
        return false;
    }

    function setHeaderCurrentTagVisible(show) {
        var el = document.getElementById('releaseNotesHeaderCurrentTag');
        if (el) {
            el.classList.toggle('d-none', !show);
            el.setAttribute('aria-hidden', show ? 'false' : 'true');
        }
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
        var list = j.newer_releases || [];
        setBehindPill(j, list.length);

        var curRaw = j.current_version || '';
        var foundTagMatchesCurrent =
            list.length > 0 &&
            list.some(function (item) {
                return releaseTagMarksCurrent(item, curDisp, curRaw);
            });
        setHeaderCurrentTagVisible(foundTagMatchesCurrent);

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
                openReleaseNotesModal();
            });
            btn.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openReleaseNotesModal();
                }
            });
        });
    }

    /** 将接口 JSON 应用到侧栏与 Modal（与是否来自网络或缓存无关）。 */
    function applyReleaseInfoResponse(j) {
        var verDisp =
            j.current_version_display || j.current_version || '—';

        setTextAll('.js-app-version-pill-label', verDisp);

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
            return;
        }

        if (j.github_error) {
            setHeaderCurrentTagVisible(false);
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
            return;
        }

        renderModal(j);

        if (j.update_available === true) {
            setClassAll('.js-app-update-pill', 'd-none', false);
            setTextAll('.js-app-update-pill-label', '有新版本');
        } else {
            setClassAll('.js-app-update-pill', 'd-none', true);
        }
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
        setTextAll('.js-app-version-pill-label', '…');
        setClassAll('.js-app-update-pill', 'd-none', true);
        setBannerLoading(true);

        try {
            var r = await fetch(appUrl('/api/app/release-info'), {
                method: 'GET',
                credentials: 'include',
                headers: { Accept: 'application/json' },
            });
            if (!r.ok) {
                setTextAll('.js-app-version-pill-label', '加载失败');
                setBannerLoading(false);
                return;
            }
            var j = await r.json();
            writeCachedReleaseInfo(j);
            applyReleaseInfoResponse(j);
            setBannerLoading(false);
        } catch (e) {
            setTextAll('.js-app-version-pill-label', '网络错误');
            setBannerLoading(false);
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
