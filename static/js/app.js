// 通用JavaScript功能

// 显示提示消息
function showAlert(message, type = 'info', duration = 5000) {
    // 创建Toast容器（如果不存在）
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '1055';
        document.body.appendChild(toastContainer);
    }
    
    // 创建Toast
    const toastId = 'toast-' + Date.now();
    const toastDiv = document.createElement('div');
    toastDiv.id = toastId;
    toastDiv.className = `toast align-items-center text-white bg-${type} border-0`;
    toastDiv.setAttribute('role', 'alert');
    toastDiv.setAttribute('aria-live', 'assertive');
    toastDiv.setAttribute('aria-atomic', 'true');
    
    // 获取图标
    const icon = getToastIcon(type);
    
    toastDiv.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                <i class="${icon} me-2"></i>${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="关闭"></button>
        </div>
    `;
    
    // 添加到容器
    toastContainer.appendChild(toastDiv);
    
    // 初始化Toast
    const toast = new bootstrap.Toast(toastDiv, {
        autohide: duration > 0,
        delay: duration
    });
    
    // 显示Toast
    toast.show();
    
    // 监听隐藏事件，移除DOM元素
    toastDiv.addEventListener('hidden.bs.toast', () => {
        if (toastDiv.parentNode) {
            toastDiv.parentNode.removeChild(toastDiv);
        }
    });
}

// 获取Toast图标
function getToastIcon(type) {
    switch (type) {
        case 'success': return 'bi bi-check-circle-fill';
        case 'danger': return 'bi bi-exclamation-triangle-fill';
        case 'warning': return 'bi bi-exclamation-triangle-fill';
        case 'info': return 'bi bi-info-circle-fill';
        default: return 'bi bi-info-circle-fill';
    }
}

// 格式化日期
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 复制到剪贴板
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        showAlert('已复制到剪贴板', 'success', 2000);
    } catch (err) {
        // 备用方法
        const textArea = document.createElement('textarea');
        textArea.value = text;
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
            document.execCommand('copy');
            showAlert('已复制到剪贴板', 'success', 2000);
        } catch (err) {
            showAlert('复制失败', 'danger');
        }
        document.body.removeChild(textArea);
    }
}

// 确认对话框
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
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

// HTTP请求封装
class ApiClient {
    constructor(baseURL = '') {
        this.baseURL = baseURL;
    }
    
    async request(url, options = {}) {
        const config = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        };
        
        try {
            loadingManager.show();
            const response = await fetch(this.baseURL + url, config);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            return data;
        } catch (error) {
            console.error('API request failed:', error);
            throw error;
        } finally {
            loadingManager.hide();
        }
    }
    
    async get(url, params = {}) {
        const queryString = new URLSearchParams(params).toString();
        const fullUrl = queryString ? `${url}?${queryString}` : url;
        return this.request(fullUrl);
    }
    
    async post(url, data = {}) {
        return this.request(url, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }
    
    async put(url, data = {}) {
        return this.request(url, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }
    
    async delete(url) {
        return this.request(url, {
            method: 'DELETE'
        });
    }
}

const api = new ApiClient();

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
});

// 导出全局函数
window.showAlert = showAlert;
window.formatDate = formatDate;
window.formatFileSize = formatFileSize;
window.copyToClipboard = copyToClipboard;
window.confirmAction = confirmAction;
window.debounce = debounce;
window.throttle = throttle;
window.loadingManager = loadingManager;
window.api = api;
window.FormValidator = FormValidator;
window.ValidationRules = ValidationRules;
window.StorageManager = StorageManager; 