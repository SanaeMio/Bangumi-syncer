// 主题管理器 —— 浅/深色主题与主色调切换（含 View Transitions 动画）

// ========== 主题管理器 ==========

class ThemeManager {
    constructor() {
        this.THEME_KEY = 'app-theme';
        this.COLOR_KEY = 'app-color';
        this.COLORS = {
            sakura:    '#f09199',
            amber:     '#f78c50',
            sakuragi:  '#e9485e',
            sanae:     '#8cb48c',
            hatsune:   '#39C5BB',
            tianyi:    '#00a2ff',
            violet:    '#a682e6'
        };
        this._deprecatedColors = { eggyolk: 'sakura', sapphire: 'sakura' };
        this._listeners = [];
        this._systemDark = window.matchMedia('(prefers-color-scheme: dark)');
        var self = this;
        this._migrateDeprecatedColor();
        this._systemDark.addEventListener('change', function() {
            if (!localStorage.getItem(self.THEME_KEY)) {
                self._apply(self._systemDark.matches ? 'dark' : 'light');
            }
        });
    }

    _migrateDeprecatedColor() {
        var stored = localStorage.getItem(this.COLOR_KEY);
        if (!stored) return;
        var migrated = this._deprecatedColors[stored];
        if (migrated) {
            localStorage.setItem(this.COLOR_KEY, migrated);
            document.documentElement.setAttribute('data-color', migrated);
            return;
        }
        if (!this.COLORS[stored]) {
            localStorage.setItem(this.COLOR_KEY, 'sakura');
            document.documentElement.setAttribute('data-color', 'sakura');
        }
    }

    /** 实际生效的主题（含系统跟随） */
    getTheme() {
        var stored = localStorage.getItem(this.THEME_KEY);
        if (stored === 'light' || stored === 'dark') return stored;
        return this._systemDark.matches ? 'dark' : 'light';
    }

    /** 是否跟随系统 */
    isSystem() {
        return !localStorage.getItem(this.THEME_KEY);
    }

    /** 恢复跟随系统 */
    followSystem() {
        localStorage.removeItem(this.THEME_KEY);
        this._apply(this._systemDark.matches ? 'dark' : 'light');
    }

    _apply(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        this._notify();
    }

    setTheme(theme) {
        localStorage.setItem(this.THEME_KEY, theme);
        this._apply(theme);
    }

    toggleTheme(triggerEl) {
        const newTheme = this.getTheme() === 'light' ? 'dark' : 'light';
        const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (prefersReduced || !triggerEl) {
            this.setTheme(newTheme);
            return;
        }
        this._animateThemeSwitch(triggerEl, newTheme);
    }

    _animateThemeSwitch(triggerEl, newTheme) {
        var self = this;
        var x, y;

        if (triggerEl) {
            var rect = triggerEl.getBoundingClientRect();
            x = rect.left + rect.width / 2;
            y = rect.top + rect.height / 2;
        } else {
            x = window.innerWidth / 2;
            y = window.innerHeight / 2;
        }

        var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        // 优先使用 View Transitions API（Chrome 111+）
        if (!prefersReduced && 'startViewTransition' in document) {
            var radius = Math.hypot(
                Math.max(x, window.innerWidth - x),
                Math.max(y, window.innerHeight - y)
            );
            var clipFrom = 'circle(0px at ' + x + 'px ' + y + 'px)';
            var clipTo = 'circle(' + radius + 'px at ' + x + 'px ' + y + 'px)';

            var transition = document.startViewTransition(function() {
                self.setTheme(newTheme);
            });

            transition.ready.then(function() {
                // 始终对 ::view-transition-new(root) 做扩散动画
                var target = document.documentElement.animate(
                    { clipPath: [clipFrom, clipTo] },
                    { duration: 700, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards', pseudoElement: '::view-transition-new(root)' }
                );
                target.finished.then(function() {
                    document.documentElement.getAnimations({ subtree: true }).forEach(function(a) { a.cancel(); });
                });
            });
            return;
        }

        // 回退：手动 overlay 动画
        var oldTheme = self.getTheme();
        document.documentElement.setAttribute('data-theme', newTheme);
        var newBg = getComputedStyle(document.body).backgroundColor;
        document.documentElement.setAttribute('data-theme', oldTheme);

        var overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;pointer-events:none;background:' + newBg;
        document.body.appendChild(overlay);

        var maxR = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));

        var anim = overlay.animate([
            { clipPath: 'circle(0px at ' + x + 'px ' + y + 'px)' },
            { clipPath: 'circle(' + maxR + 'px at ' + x + 'px ' + y + 'px)' }
        ], { duration: 700, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards' });

        anim.onfinish = function() {
            self.setTheme(newTheme);
            overlay.remove();
        };
    }

    getColor() {
        return localStorage.getItem(this.COLOR_KEY) || 'sakura';
    }

    setColor(color) {
        if (!this.COLORS[color]) return;
        localStorage.setItem(this.COLOR_KEY, color);
        document.documentElement.setAttribute('data-color', color);
        this._notify();
    }

    setColorAnimated(color, triggerEl) {
        if (!this.COLORS[color]) return;
        if (this.getColor() === color) return;
        var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (prefersReduced || !triggerEl) { this.setColor(color); return; }

        var self = this;
        var rect = triggerEl.getBoundingClientRect();
        var x = rect.left + rect.width / 2;
        var y = rect.top + rect.height / 2;

        if ('startViewTransition' in document) {
            var radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
            var transition = document.startViewTransition(function() { self.setColor(color); });
            transition.ready.then(function() {
                document.documentElement.animate(
                    { clipPath: ['circle(0px at ' + x + 'px ' + y + 'px)', 'circle(' + radius + 'px at ' + x + 'px ' + y + 'px)'] },
                    { duration: 700, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards', pseudoElement: '::view-transition-new(root)' }
                ).finished.then(function() {
                    document.documentElement.getAnimations({ subtree: true }).forEach(function(a) { a.cancel(); });
                });
            });
            return;
        }

        // 回退：overlay 扩散动画
        var oldColor = this.getColor();
        this.setColor(color);
        var overlay = document.createElement('div');
        var scrollY = window.scrollY || window.pageYOffset;
        overlay.style.cssText = 'position:absolute;left:0;top:' + scrollY + 'px;width:' + document.documentElement.scrollWidth + 'px;height:' + document.documentElement.scrollHeight + 'px;overflow:hidden;z-index:99999;pointer-events:none;';
        var clone = document.documentElement.cloneNode(true);
        clone.style.position = 'absolute';
        clone.style.left = '0';
        clone.style.top = '0';
        clone.style.width = document.documentElement.scrollWidth + 'px';
        clone.style.height = document.documentElement.scrollHeight + 'px';
        overlay.appendChild(clone);
        document.body.appendChild(overlay);
        self.setColor(oldColor);
        var maxR = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
        overlay.animate([
            { clipPath: 'circle(0px at ' + x + 'px ' + (y + scrollY) + 'px)' },
            { clipPath: 'circle(' + maxR + 'px at ' + x + 'px ' + (y + scrollY) + 'px)' }
        ], { duration: 700, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards' }).onfinish = function() {
            self.setColor(color);
            overlay.remove();
        };
    }

    getPrimaryColor() {
        var css = getComputedStyle(document.documentElement).getPropertyValue('--app-primary').trim();
        if (css) return css;
        return this.COLORS[this.getColor()] || this.COLORS.sakura;
    }

    getColors() {
        return this.COLORS;
    }

    onChange(fn) {
        this._listeners.push(fn);
    }

    _notify() {
        this._listeners.forEach(fn => fn());
    }
}

window.themeManager = new ThemeManager();
