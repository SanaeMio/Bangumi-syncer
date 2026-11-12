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
    }

    function init() {
        var collapsed = isCollapsed();
        syncUI(collapsed);
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
