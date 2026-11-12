// 侧边栏折叠 —— 宽屏下把左侧导航收成图标栏，状态记在 localStorage

(function () {
    'use strict';

    var STORAGE_KEY = 'app-sidebar';
    var COLLAPSED = 'collapsed';
    var EXPANDED = 'expanded';

    function isCollapsed() {
        return document.documentElement.getAttribute('data-sidebar') === COLLAPSED;
    }

    // 折叠后文字被隐藏，用原生 title 补回提示；展开时还原成模板原有的 title
    function syncNavTitles(collapsed) {
        var links = document.querySelectorAll('.sidebar .nav-link');
        links.forEach(function (link) {
            if (collapsed) {
                if (link.getAttribute('title') === null) {
                    var text = link.textContent.replace(/\s+/g, ' ').trim();
                    if (text) {
                        link.setAttribute('title', text);
                        // 折叠态的浮层标签由 data 属性驱动（见 theme-base.css）
                        link.dataset.sidebarLabel = text;
                        link.dataset.sidebarTitle = 'auto';
                    }
                }
            } else if (link.dataset.sidebarTitle === 'auto') {
                link.removeAttribute('title');
                delete link.dataset.sidebarTitle;
                delete link.dataset.sidebarLabel;
            }
        });
    }

    function syncUI(collapsed) {
        var btn = document.getElementById('sidebarCollapseBtn');
        if (btn) {
            btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            var label = collapsed ? '展开侧栏' : '收起侧栏';
            btn.setAttribute('aria-label', label);
            btn.setAttribute('title', label);
            var icon = btn.querySelector('.sidebar-brand__toggle-icon');
            if (icon) {
                icon.classList.toggle('bi-chevron-double-left', !collapsed);
                icon.classList.toggle('bi-chevron-double-right', collapsed);
            }
        }
        syncNavTitles(collapsed);
    }

    function setCollapsed(collapsed) {
        document.documentElement.setAttribute(
            'data-sidebar',
            collapsed ? COLLAPSED : EXPANDED
        );
        try {
            localStorage.setItem(STORAGE_KEY, collapsed ? COLLAPSED : EXPANDED);
        } catch (e) {
            // 隐私模式下 localStorage 可能不可写，忽略即可
        }
        syncUI(collapsed);
        // 折叠时导航宽度变了，滑块跟着重新贴合（不做动画）
        moveNavSlider(activeNavLink(), false);
    }

    // ---------- 导航选中滑块：切换页面时从旧条目滑到新条目 ----------

    var navSlider = null;
    var navTimer = 0;
    var navSwapTimer = 0;

    function navList() {
        return document.querySelector('.sidebar .sidebar-nav');
    }

    function moveNavSlider(link, animate) {
        var nav = navList();
        if (!nav || !link) return;
        if (!navSlider) {
            navSlider = document.createElement('span');
            navSlider.className = 'sidebar-nav__slider';
            navSlider.setAttribute('aria-hidden', 'true');
            nav.appendChild(navSlider);
        }
        if (!animate) navSlider.classList.add('sidebar-nav__slider--instant');
        navSlider.style.width = link.offsetWidth + 'px';
        navSlider.style.height = link.offsetHeight + 'px';
        navSlider.style.transform =
            'translate3d(' + link.offsetLeft + 'px,' + link.offsetTop + 'px,0)';
        navSlider.classList.add('sidebar-nav__slider--visible');
        if (!animate) {
            // 强制一次布局，避免 --instant 被提前移除而滑进来
            void navSlider.offsetWidth;
            navSlider.classList.remove('sidebar-nav__slider--instant');
        }
    }

    function activeNavLink() {
        var nav = navList();
        return nav ? nav.querySelector('.nav-link.active') : null;
    }

    function initNavSlider() {
        var nav = navList();
        if (!nav) return;

        document.documentElement.classList.add('js-nav-slider');
        moveNavSlider(activeNavLink(), false);

        nav.addEventListener('click', function (event) {
            var link = event.target.closest ? event.target.closest('.nav-link') : null;
            if (!link || link.classList.contains('active')) return;
            // 组合键与中键仍走浏览器原生行为（新标签页等）
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            if (event.button !== 0) return;
            var href = link.getAttribute('href');
            if (!href || href.charAt(0) === '#') return;

            event.preventDefault();
            var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

            function swapActive() {
                nav.querySelectorAll('.nav-link').forEach(function (el) {
                    el.classList.remove('active');
                });
                link.classList.add('active');
            }

            moveNavSlider(link, !reduce);
            window.clearTimeout(navSwapTimer);
            window.clearTimeout(navTimer);
            if (reduce) {
                swapActive();
                window.location.href = href;
                return;
            }
            // 白字在滑块走到一半时再换，避免新条目提前变白压在淡底上看不清
            navSwapTimer = window.setTimeout(swapActive, 190);
            navTimer = window.setTimeout(function () {
                window.location.href = href;
            }, 300);
        });

        window.addEventListener('resize', function () {
            window.clearTimeout(navTimer);
            moveNavSlider(activeNavLink(), false);
        });
    }

    function init() {
        var collapsed = isCollapsed();
        syncUI(collapsed);
        initNavSlider();
        var btn = document.getElementById('sidebarCollapseBtn');
        if (btn) {
            btn.addEventListener('click', function () {
                setCollapsed(!isCollapsed());
            });
        }
        // 仅在宽屏（侧栏可见）时支持折叠，窄屏保持原样
        window.addEventListener('resize', function () {
            if (window.innerWidth < 768) syncNavTitles(false);
            else syncNavTitles(isCollapsed());
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
