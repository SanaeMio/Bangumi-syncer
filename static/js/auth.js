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

// 登录页面初始化
function initLoginPage() {
    // 聚焦到用户名输入框
    const usernameInput = document.getElementById('username');
    if (usernameInput) {
        usernameInput.focus();
    }

    // 登录表单处理
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', handleLoginSubmit);
    }

    // 回车键快速登录
    document.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            const loginForm = document.getElementById('loginForm');
            if (loginForm && document.activeElement && loginForm.contains(document.activeElement)) {
                e.preventDefault();
                loginForm.dispatchEvent(new Event('submit'));
            }
        }
    });
}

// 处理登录表单提交
async function handleLoginSubmit(e) {
    e.preventDefault();

    const loginBtn = document.getElementById('loginBtn');
    const loginText = loginBtn.querySelector('.login-text');
    const loadingSpinner = loginBtn.querySelector('.login-btn-status');
    const alertContainer = document.getElementById('alert-container');

    // 显示加载状态
    loginBtn.disabled = true;
    loginBtn.classList.add('login-btn-loading');

    // 清除之前的错误信息
    alertContainer.innerHTML = '';

    const formData = new FormData(e.target);
    const data = {
        username: formData.get('username'),
        password: formData.get('password')
    };

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

        if (response.ok && result.status === 'success') {
            // 登录成功，显示成功信息并跳转
            alertContainer.innerHTML = `
                <div class="alert alert-success">
                    <i class="bi bi-check-circle"></i> 登录成功，正在跳转...
                </div>
            `;

            // 延迟跳转以显示成功信息
            const params = new URLSearchParams(window.location.search);
            const next = params.get('next');
            // 只允许站内路径跳转，防止开放重定向
            const target = next && next.startsWith('/') && !next.startsWith('//')
                ? next : '/dashboard';
            setTimeout(() => {
                window.location.href = appUrl(target);
            }, 1000);
        } else {
            // 登录失败
            throw new Error(result.message || '登录失败');
        }
    } catch (error) {
        // 显示错误信息
        alertContainer.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle"></i> ${error.message}
            </div>
        `;

        // 恢复按钮状态
        loginBtn.disabled = false;
        loginBtn.classList.remove('login-btn-loading');

        // 清空密码字段
        const passwordInput = document.getElementById('password');
        if (passwordInput) {
            passwordInput.value = '';
            passwordInput.focus();
        }
    }
}

// 在页面加载时检查是否为登录页面
document.addEventListener('DOMContentLoaded', function() {
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
