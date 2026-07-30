// 通用 UI 工具 —— HTML 转义、按钮加载态、模态框、防抖节流、表单验证、本地存储、确认对话框

function escapeHtml(text) {
    if (text == null) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * 按钮加载态封装
 *
 * 用法：
 *   const restore = setButtonLoading(btn, '测试中...');
 *   try { ... } finally { restore(); }
 *
 * 或传入 async 函数自动恢复：
 *   await setButtonLoading(btn, '保存中...', async () => { ... });
 *
 * @param {HTMLButtonElement} button 按钮
 * @param {string} [loadingText] 加载态文案，默认仅 spinner
 * @param {Function} [asyncFn] 可选，执行完自动恢复
 * @returns {Function|Promise} restore 函数；若传入 asyncFn 则返回 Promise
 */
function setButtonLoading(button, loadingText, asyncFn) {
    if (!button) return typeof asyncFn === 'function' ? Promise.resolve() : function () {};
    const originalHtml = button.innerHTML;
    const originalDisabled = button.disabled;
    button.disabled = true;
    button.innerHTML = loadingText
        ? '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>' + loadingText
        : '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';

    const restore = function () {
        button.disabled = originalDisabled;
        button.innerHTML = originalHtml;
    };

    if (typeof asyncFn === 'function') {
        return Promise.resolve()
            .then(asyncFn)
            .finally(restore);
    }
    return restore;
}

/**
 * 模态框工具：缓存实例，避免重复 new bootstrap.Modal
 *
 * getModal('modalId') 返回缓存的 Modal 实例（不存在则创建）
 * showModal('modalId') 显示模态框
 * hideModal('modalId') 隐藏模态框
 */
const _modalCache = {};

function getModal(modalId) {
    if (!_modalCache[modalId]) {
        const el = document.getElementById(modalId);
        if (!el) return null;
        _modalCache[modalId] = bootstrap.Modal.getOrCreateInstance(el);
    }
    return _modalCache[modalId];
}

function showModal(modalId) {
    const modal = getModal(modalId);
    if (modal) modal.show();
    return modal;
}

function hideModal(modalId) {
    const modal = getModal(modalId);
    if (modal) modal.hide();
    return modal;
}

// 防抖函数
function debounce(func, wait, immediate) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            timeout = null;
            if (!immediate) func(...args);
        };
        const callNow = immediate && !timeout;
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
        if (callNow) func(...args);
    };
}

// 节流函数
function throttle(func, limit) {
    let inThrottle;
    return function() {
        const args = arguments;
        const context = this;
        if (!inThrottle) {
            func.apply(context, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

// 加载状态管理
class LoadingManager {
    constructor() {
        this.loadingCount = 0;
        this.loadingElement = null;
    }

    show(element = null) {
        this.loadingCount++;
        if (element) {
            this.loadingElement = element;
            element.style.display = 'block';
        }
        document.body.style.cursor = 'wait';
    }

    hide(element = null) {
        this.loadingCount = Math.max(0, this.loadingCount - 1);
        if (this.loadingCount === 0) {
            document.body.style.cursor = 'default';
            if (element) {
                element.style.display = 'none';
            } else if (this.loadingElement) {
                this.loadingElement.style.display = 'none';
                this.loadingElement = null;
            }
        }
    }
}

const loadingManager = new LoadingManager();

// 表单验证
class FormValidator {
    constructor(form) {
        this.form = form;
        this.errors = {};
    }

    addRule(fieldName, rule, message) {
        if (!this.rules) this.rules = {};
        if (!this.rules[fieldName]) this.rules[fieldName] = [];
        this.rules[fieldName].push({ rule, message });
        return this;
    }

    validate() {
        this.errors = {};
        const formData = new FormData(this.form);

        for (const [fieldName, rules] of Object.entries(this.rules || {})) {
            const value = formData.get(fieldName);

            for (const { rule, message } of rules) {
                if (!rule(value)) {
                    if (!this.errors[fieldName]) this.errors[fieldName] = [];
                    this.errors[fieldName].push(message);
                }
            }
        }

        this.displayErrors();
        return Object.keys(this.errors).length === 0;
    }

    displayErrors() {
        // 清除之前的错误
        this.form.querySelectorAll('.is-invalid').forEach(el => {
            el.classList.remove('is-invalid');
        });
        this.form.querySelectorAll('.invalid-feedback').forEach(el => {
            el.remove();
        });

        // 显示新错误
        for (const [fieldName, messages] of Object.entries(this.errors)) {
            const field = this.form.querySelector(`[name="${fieldName}"]`);
            if (field) {
                field.classList.add('is-invalid');

                const feedback = document.createElement('div');
                feedback.className = 'invalid-feedback';
                feedback.textContent = messages[0];
                field.parentNode.appendChild(feedback);
            }
        }
    }
}

// 常用验证规则
const ValidationRules = {
    required: (value) => value && value.trim() !== '',
    email: (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value),
    url: (value) => {
        try {
            new URL(value);
            return true;
        } catch {
            return false;
        }
    },
    number: (value) => !isNaN(value) && isFinite(value),
    integer: (value) => Number.isInteger(Number(value)),
    positive: (value) => Number(value) > 0,
    minLength: (min) => (value) => value && value.length >= min,
    maxLength: (max) => (value) => !value || value.length <= max,
    pattern: (regex) => (value) => !value || regex.test(value)
};

// 本地存储管理
class StorageManager {
    static set(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (error) {
            console.error('Failed to save to localStorage:', error);
        }
    }

    static get(key, defaultValue = null) {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : defaultValue;
        } catch (error) {
            console.error('Failed to read from localStorage:', error);
            return defaultValue;
        }
    }

    static remove(key) {
        try {
            localStorage.removeItem(key);
        } catch (error) {
            console.error('Failed to remove from localStorage:', error);
        }
    }

    static clear() {
        try {
            localStorage.clear();
        } catch (error) {
            console.error('Failed to clear localStorage:', error);
        }
    }
}

// 页面初始化
document.addEventListener('DOMContentLoaded', function() {
    // 初始化所有提示工具
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // 初始化所有弹出框
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });

    // 自动关闭提示消息
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        if (alert.classList.contains('auto-dismiss')) {
            setTimeout(() => {
                alert.classList.remove('show');
                setTimeout(() => {
                    if (alert.parentNode) {
                        alert.parentNode.removeChild(alert);
                    }
                }, 150);
            }, 5000);
        }
    });

    // 加载 Webhook 配置列表
    if (typeof loadWebhookConfigs === 'function') {
        loadWebhookConfigs();
    }
});

// 异步确认对话框
async function confirmAction(message, callback) {
    return new Promise((resolve) => {
        if (confirm(message)) {
            Promise.resolve(callback()).then(resolve).catch((error) => {
                console.error('Callback error:', error);
                resolve(false);
            });
        } else {
            resolve(false);
        }
    });
}

window.escapeHtml = escapeHtml;
window.setButtonLoading = setButtonLoading;
window.getModal = getModal;
window.showModal = showModal;
window.hideModal = hideModal;
window.confirmAction = confirmAction;
window.debounce = debounce;
window.throttle = throttle;
window.loadingManager = loadingManager;
window.FormValidator = FormValidator;
window.ValidationRules = ValidationRules;
window.StorageManager = StorageManager;
