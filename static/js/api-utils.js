// API 请求工具 —— 站内 URL 拼接与统一 fetch 封装

/**
 * 子路径反向代理下的站内 URL（与后端 join_public / 模板 | p 一致）
 */
function appUrl(path) {
    const base =
        typeof window.__APP_BASE_PATH__ === 'string' ? window.__APP_BASE_PATH__ : '';
    if (!path) {
        return base || '/';
    }
    const p = path.startsWith('/') ? path : '/' + path;
    return base + p;
}

/**
 * 统一 HTTP 请求封装
 *
 * - 默认带 credentials: 'include'
 * - 401 自动跳转登录（可用 skipAuthRedirect 绕过）
 * - !ok 抛异常并解析后端 detail/message（可用 returnResponse 绕过，返回原始 response）
 *
 * @param {string} path 站内路径，会自动拼接 appUrl
 * @param {object} options fetch options，可附加 skipAuthRedirect / returnResponse
 */
async function apiFetch(path, options = {}) {
    const opts = { credentials: 'include', ...options };
    const response = await fetch(appUrl(path), opts);

    if (response.status === 401 && !opts.skipAuthRedirect) {
        window.location.href = appUrl('/login');
        throw new Error('未登录，正在跳转登录页');
    }

    if (!response.ok && !opts.returnResponse) {
        let msg = `请求失败: ${response.status}`;
        try {
            const errData = await response.json();
            msg = errData.detail || errData.message || msg;
        } catch (_) {}
        throw new Error(msg);
    }

    // returnResponse: 返回原始 Response 对象（调用方需自行处理 .json()/.ok 等）
    // 否则：自动解析 JSON
    return opts.returnResponse ? response : response.json();
}

window.appUrl = appUrl;
window.apiFetch = apiFetch;
