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
        // 侧栏宽度是过渡出来的，滑块交给 ResizeObserver 每帧跟随，这里无需手动量
    }

    // ---------- 导航选中滑块 ----------
    // 两条规矩：
    // 1. 折叠/展开时侧栏宽度在过渡，滑块必须每帧重新贴合选中项，
    //    否则会停在展开时的宽度上，越过侧栏边界压到内容区；
    // 2. 切页不再拦截跳转（省掉等待），只把「从哪一项出发」记进
    //    sessionStorage，新页面加载后滑块从那一项滑到当前项，
    //    看起来是一次连续的位移，而不是滑到一半被整页刷新打断。

    var navSlider = null;
    var navObserver = null;
    var FROM_KEY = 'app-nav-slider-from';
    var ARRIVE_MS = 300;
    var ORIGIN_MS = 120;

    function navList() {
        return document.querySelector('.sidebar .sidebar-nav');
    }

    function navLinks() {
        var nav = navList();
        if (!nav) return [];
        return Array.prototype.slice.call(nav.querySelectorAll('.nav-link'));
    }

    function activeNavLink() {
        var nav = navList();
        return nav ? nav.querySelector('.nav-link.active') : null;
    }

    function placeSlider(link, animate) {
        var nav = navList();
        if (!nav || !link) return;
        if (!navSlider) {
            navSlider = document.createElement('span');
            navSlider.className = 'sidebar-nav__slider';
            navSlider.setAttribute('aria-hidden', 'true');
            nav.appendChild(navSlider);
        }
        if (!animate) navSlider.classList.add('sidebar-nav__slider--instant');
        var navRect = nav.getBoundingClientRect();
        var rect = link.getBoundingClientRect();
        navSlider.style.width = rect.width + 'px';
        navSlider.style.height = rect.height + 'px';
        navSlider.style.transform =
            'translate3d(' + (rect.left - navRect.left) + 'px,' +
            (rect.top - navRect.top) + 'px,0)';
        navSlider.classList.add('sidebar-nav__slider--visible');
        if (!animate) {
            // 强制一次布局，避免 --instant 被提前移除而滑进来
            void navSlider.offsetWidth;
            navSlider.classList.remove('sidebar-nav__slider--instant');
        }
    }

    // 侧栏宽度（折叠/展开、窗口缩放、字体加载）一变就贴一次，永远不做动画
    function watchSliderTargets() {
        if (!window.ResizeObserver) return;
        if (!navObserver) {
            navObserver = new ResizeObserver(function () {
                placeSlider(activeNavLink(), false);
            });
        }
        navObserver.disconnect();
        var nav = navList();
        if (nav) navObserver.observe(nav);
        var active = activeNavLink();
        if (active) navObserver.observe(active);
    }

    function rememberOrigin() {
        var active = activeNavLink();
        if (!active) return;
        try {
            sessionStorage.setItem(FROM_KEY, String(navLinks().indexOf(active)));
        } catch (e) {
            // 隐私模式下写不进去就算了，退化成没有入场滑动
        }
    }

    function initNavSlider() {
        var nav = navList();
        if (!nav) return;

        document.documentElement.classList.add('js-nav-slider');

        var links = navLinks();
        var active = activeNavLink();
        var from = -1;
        try {
            from = parseInt(sessionStorage.getItem(FROM_KEY) || '', 10);
            sessionStorage.removeItem(FROM_KEY);
        } catch (e) {
            from = -1;
        }
        var origin = isNaN(from) ? null : links[from];
        var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        if (origin && active && origin !== active && !reduce) {
            placeSlider(origin, false);
            origin.classList.add('is-slider-origin');
            document.documentElement.classList.add('js-nav-arriving');
            // 滑块离开旧条目后再把它还原成灰字，不必等整段动画结束
            window.setTimeout(function () {
                origin.classList.remove('is-slider-origin');
            }, ORIGIN_MS);
            // 先把滑块落在旧选中项上，再滑过来：抵消整页刷新带来的跳变
            window.requestAnimationFrame(function () {
                window.requestAnimationFrame(function () {
                    placeSlider(active, true);
                });
            });
            // 入场滑动期间不接管尺寸：ResizeObserver 一上来就会触发一次，
            // 那一下会把动画直接打断成瞬移，所以等滑完再开始跟随
            window.setTimeout(function () {
                document.documentElement.classList.remove('js-nav-arriving');
                watchSliderTargets();
            }, ARRIVE_MS);
        } else {
            placeSlider(active, false);
            watchSliderTargets();
        }

        nav.addEventListener('click', function (event) {
            var link = event.target.closest ? event.target.closest('.nav-link') : null;
            if (!link || link.classList.contains('active')) return;
            // 组合键与中键仍按浏览器原生行为处理（新标签页等）
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            if (event.button !== 0) return;
            var href = link.getAttribute('href');
            if (!href || href.charAt(0) === '#') return;
            // 不拦截跳转：记住出发项，新页面负责把滑块滑过来
            rememberOrigin();
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
