// 认证与登录 —— 登出、认证状态检查、登录页表单处理

// 认证相关功能
async function logout() {
    try {
        const result = await confirmAction('确定要登出吗？', async () => {
            await apiFetch('/api/logout', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            if (typeof window.clearBgmReleaseInfoCache === 'function') {
                window.clearBgmReleaseInfoCache();
            }
            showAlert('登出成功', 'success', 2000);
            // 延迟跳转到登录页面
            setTimeout(() => {
                window.location.href = appUrl('/login');
            }, 1000);
        });
    } catch (error) {
        showAlert('登出失败: ' + error.message, 'danger');
    }
}

// 检查认证状态
async function checkAuthStatus() {
    try {
        const result = await apiFetch('/api/auth/status', { skipAuthRedirect: true });

        if (result.status === 'success' && result.data) {
            return result.data;
        }
        return { authenticated: false };
    } catch (error) {
        console.error('检查认证状态失败:', error);
        return { authenticated: false };
    }
}

// 页面认证检查
async function initAuth() {
    const authStatus = await checkAuthStatus();

    // 如果未认证且不在登录页面，跳转到登录页面
    if (!authStatus.authenticated && !window.location.pathname.includes('/login')) {
        window.location.href = appUrl('/login');
        return false;
    }

    return true;
}

// ========== 登录页面专用功能 ==========

// 表单提交中标记：防止 Enter 与点击同时触发两次请求
let loginSubmitting = false;

/** 密码显隐切换：同步 type / aria-pressed / 文案 / 图标态 */
function togglePasswordVisibility() {
    const passwordInput = document.getElementById('password');
    const toggleBtn = document.getElementById('togglePasswordVisibility');
    if (!passwordInput || !toggleBtn) return;

    const revealed = passwordInput.type === 'password';
    passwordInput.type = revealed ? 'text' : 'password';
    toggleBtn.classList.toggle('is-revealed', revealed);
    toggleBtn.setAttribute('aria-pressed', revealed ? 'true' : 'false');
    toggleBtn.setAttribute('aria-label', revealed ? '隐藏密码' : '显示密码');

    // 切换后把光标放回末尾，避免 type 变更导致选区丢失
    if (document.activeElement === passwordInput) {
        const end = passwordInput.value.length;
        try {
            passwordInput.setSelectionRange(end, end);
        } catch (_) {
            /* 部分浏览器对 text 类型以外的输入框不支持，忽略 */
        }
    }
}

/** 密码框聚焦时提示 Caps Lock，失焦收起 */
function wireCapsLockHint() {
    const passwordInput = document.getElementById('password');
    const hint = document.getElementById('capsLockHint');
    if (!passwordInput || !hint) return;

    const sync = function (event) {
        if (!event.getModifierState) return;
        hint.hidden = !event.getModifierState('CapsLock');
    };

    passwordInput.addEventListener('keydown', sync);
    passwordInput.addEventListener('keyup', sync);
    passwordInput.addEventListener('blur', function () {
        hint.hidden = true;
    });
}

/** 输入修正时立刻清掉该字段的无效态 */
function wireFieldReset() {
    document.querySelectorAll('.login-field').forEach(function (field) {
        const input = field.querySelector('.form-control');
        if (!input) return;
        input.addEventListener('input', function () {
            field.classList.remove('is-invalid');
            input.removeAttribute('aria-invalid');
        });
    });
}

/** 逐字段校验，返回是否全部通过 */
function validateLoginFields() {
    let firstInvalid = null;

    document.querySelectorAll('.login-field').forEach(function (field) {
        const input = field.querySelector('.form-control');
        if (!input) return;
        if (input.value.trim()) {
            field.classList.remove('is-invalid');
            input.removeAttribute('aria-invalid');
            return;
        }
        field.classList.add('is-invalid');
        input.setAttribute('aria-invalid', 'true');
        if (!firstInvalid) firstInvalid = input;
    });

    if (firstInvalid) firstInvalid.focus({ preventScroll: true });
    return !firstInvalid;
}

/** 首屏聚焦：仅在精细指针设备上自动聚焦，移动端不弹键盘 */
function focusFirstField() {
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    if (!usernameInput) return;

    const canHover = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    if (!canHover) return;

    // 浏览器已自动填充用户名时直接跳到密码框
    if (usernameInput.value && passwordInput && !passwordInput.value) {
        passwordInput.focus({ preventScroll: true });
        return;
    }
    usernameInput.focus({ preventScroll: true });
}

/** 按钮状态：idle / loading / success */
function setLoginButtonState(state) {
    const loginBtn = document.getElementById('loginBtn');
    if (!loginBtn) return;
    loginBtn.dataset.state = state;
    loginBtn.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
}

/** 切换表单可编辑性（提交中禁用，失败/成功后恢复） */
function setLoginFormBusy(busy) {
    const loginForm = document.getElementById('loginForm');
    const toggleBtn = document.getElementById('togglePasswordVisibility');
    if (loginForm) {
        loginForm.setAttribute('aria-busy', busy ? 'true' : 'false');
        loginForm.querySelectorAll('.form-control').forEach(function (input) {
            input.disabled = busy;
        });
    }
    if (toggleBtn) toggleBtn.disabled = busy;
}

/** 用文本节点渲染告警，避免服务端消息被当成 HTML 解析 */
function renderLoginAlert(message) {
    const alertContainer = document.getElementById('alert-container');
    if (!alertContainer) return;

    alertContainer.textContent = '';
    if (!message) return;

    const alertEl = document.createElement('div');
    alertEl.className = 'login-alert login-alert--danger';
    alertEl.setAttribute('role', 'alert');

    const icon = document.createElement('i');
    icon.className = 'bi bi-exclamation-triangle';
    icon.setAttribute('aria-hidden', 'true');

    const text = document.createElement('span');
    text.textContent = message;

    alertEl.appendChild(icon);
    alertEl.appendChild(text);
    alertContainer.appendChild(alertEl);
}

/** 登录成功后跳转目标：只允许站内路径，防止开放重定向 */
function resolveLoginRedirect() {
    const params = new URLSearchParams(window.location.search);
    const next = params.get('next');
    return next && next.startsWith('/') && !next.startsWith('//')
        ? next : '/dashboard';
}

/** 预拉取跳转页，减轻登录成功后的硬刷新顿挫感 */
function prefetchLoginRedirectTarget(url) {
    if (!url) return;
    try {
        const link = document.createElement('link');
        link.rel = 'prefetch';
        link.href = url;
        document.head.appendChild(link);
    } catch (_) {
        /* prefetch 不可用时不影响主流程 */
    }
}

/**
 * 登录成功离场时间线（与 pages.css 中 --login-motion-* 对齐）
 * 1) 展示成功态  2) 表单退場  3) 整卡淡出  4) 跳转（可走 View Transition）
 */
const LOGIN_SUCCESS_HOLD_MS = 500;
const LOGIN_EXIT_PREP_MS = 280;
const LOGIN_EXIT_FADE_MS = 720;
const LOGIN_EXIT_NAV_BUFFER_MS = 90;

const LOGIN_ARRIVAL_KEY = 'app-login-arriving';

/** 标记「刚登录完成」，目标页首屏与离场同一套节奏 */
function markLoginArrivalHandoff() {
    try {
        sessionStorage.setItem(LOGIN_ARRIVAL_KEY, '1');
    } catch (_) {
        /* 隐私模式等不可用时不影响跳转 */
    }
}

/**
 * 登录离场已在 CSS 中完整淡出（含铺底），此处直接跳转即可。
 * 不再使用 View Transitions：MPA 全页刷新时 ::view-transition-old(login-exit)
 * 会从 opacity:1 重播退场，造成「登录页又闪回来一次」；与新页 handoff 叠成双重加载感。
 */
function navigateAfterLoginExit(url) {
    markLoginArrivalHandoff();
    window.location.assign(url);
}

/** 登录成功后的三阶段离场 */
function runLoginSuccessExit(target, prefersReducedMotion) {
    if (prefersReducedMotion) {
        window.setTimeout(function () {
            markLoginArrivalHandoff();
            window.location.assign(target);
        }, 160);
        return;
    }

    window.setTimeout(function () {
        document.body.classList.add('login-body--exit-prep');
    }, LOGIN_SUCCESS_HOLD_MS);

    window.setTimeout(function () {
        document.body.classList.add('login-body--leaving');
    }, LOGIN_SUCCESS_HOLD_MS + LOGIN_EXIT_PREP_MS);

    window.setTimeout(function () {
        navigateAfterLoginExit(target);
    }, LOGIN_SUCCESS_HOLD_MS + LOGIN_EXIT_PREP_MS + LOGIN_EXIT_FADE_MS + LOGIN_EXIT_NAV_BUFFER_MS);
}

// 登录页面初始化
function initLoginPage() {
    const loginForm = document.getElementById('loginForm');
    if (!loginForm) return;

    const toggleBtn = document.getElementById('togglePasswordVisibility');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', togglePasswordVisibility);
    }

    wireCapsLockHint();
    wireFieldReset();
    loginForm.addEventListener('submit', handleLoginSubmit);
    focusFirstField();
}

// 处理登录表单提交
async function handleLoginSubmit(e) {
    e.preventDefault();
    if (loginSubmitting) return;

    const alertContainer = document.getElementById('alert-container');
    const passwordInput = document.getElementById('password');
    const loginStatus = document.getElementById('loginStatus');

    renderLoginAlert('');
    if (!validateLoginFields()) return;

    // 先取表单值：禁用输入框后 FormData 会读不到内容
    const formData = new FormData(e.target);
    const data = {
        username: formData.get('username'),
        password: formData.get('password')
    };

    loginSubmitting = true;
    setLoginButtonState('loading');
    setLoginFormBusy(true);
    if (alertContainer) alertContainer.setAttribute('aria-busy', 'true');

    try {
        const response = await apiFetch('/api/login', {
            method: 'POST',
            returnResponse: true,
            skipAuthRedirect: true,
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (!response.ok || result.status !== 'success') {
            // 失败响应形如 {"detail": "..."}；交给 apiErrorMessage 统一解析，
            // 避免 401/423 的提示退化成「登录失败（401）」
            throw new Error(apiErrorMessage(result, `登录失败（${response.status}）`));
        }

        const target = appUrl(resolveLoginRedirect());
        prefetchLoginRedirectTarget(target);

        // 登录成功：先读成功态，再整卡淡出，最后再跳转
        setLoginButtonState('success');
        if (loginStatus) loginStatus.textContent = '登录成功，正在跳转';

        const prefersReducedMotion = window.matchMedia(
            '(prefers-reduced-motion: reduce)'
        ).matches;
        runLoginSuccessExit(target, prefersReducedMotion);
    } catch (error) {
        renderLoginAlert(error.message || '登录失败');
        if (alertContainer) alertContainer.removeAttribute('aria-busy');

        setLoginButtonState('idle');
        setLoginFormBusy(false);
        loginSubmitting = false;

        // 清空密码并把焦点交还，便于直接重试
        if (passwordInput) {
            passwordInput.value = '';
            passwordInput.focus({ preventScroll: true });
        }
    }
}

/** 首屏衔接 class 用毕后移除，避免影响后续站内跳转 */
function initLoginArrivalHandoff() {
    if (!document.documentElement.classList.contains('js-login-arriving')) return;
    window.setTimeout(function () {
        document.documentElement.classList.remove('js-login-arriving');
    }, 1400);
}

// 在页面加载时检查是否为登录页面
document.addEventListener('DOMContentLoaded', function() {
    initLoginArrivalHandoff();

    // 如果是登录页面，初始化登录功能
    if (window.location.pathname.includes('/login') || document.getElementById('loginForm')) {
        initLoginPage();
    }
});

window.logout = logout;
window.checkAuthStatus = checkAuthStatus;
window.initAuth = initAuth;
window.initLoginPage = initLoginPage;
window.handleLoginSubmit = handleLoginSubmit;
