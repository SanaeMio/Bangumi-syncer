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
/**
 * 将后端错误响应体归一化为人类可读的纯字符串。
 *
 * 处理多种响应形态：
 * - detail 为字符串（如 HTTPException 的 detail）
 * - detail 为对象数组（FastAPI 422 校验错误，每项含 msg 与 loc）
 * - detail 为单对象（含 message/msg）
 * - 顶层 message 兜底
 * 任何分支都保证返回字符串，杜绝 [object Object]。
 */
function apiErrorMessage(errData, fallback) {
    const detail = errData && errData.detail;
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail)) {
        // FastAPI 422 校验错误：提取各条 msg，附字段名（loc 末段）
        const parts = detail
            .map(function (e) {
                if (!e || typeof e !== 'object') return String(e);
                const loc = Array.isArray(e.loc) ? e.loc : [];
                const field = loc.length ? String(loc[loc.length - 1]) : '';
                const msg = typeof e.msg === 'string' ? e.msg : '';
                if (field && msg) return field + ': ' + msg;
                if (msg) return msg;
                return JSON.stringify(e);
            })
            .filter(function (s) { return s; });
        if (parts.length) return parts.join('; ');
    }
    if (detail && typeof detail === 'object') {
        if (typeof detail.message === 'string' && detail.message) return detail.message;
        if (typeof detail.msg === 'string' && detail.msg) return detail.msg;
        try { return JSON.stringify(detail); } catch (_) {}
    }
    if (typeof errData.message === 'string' && errData.message) return errData.message;
    return fallback;
}

async function apiFetch(path, options = {}) {
    const opts = { credentials: 'include', ...options };
    const response = await fetch(appUrl(path), opts);

    if (response.status === 401 && !opts.skipAuthRedirect) {
        window.location.href = appUrl('/login');
        throw new Error('未登录，正在跳转登录页');
    }

    if (!response.ok && !opts.returnResponse) {
        const fallback = `请求失败: ${response.status}`;
        let msg = fallback;
        try {
            const errData = await response.json();
            msg = apiErrorMessage(errData, fallback);
        } catch (_) {}
        throw new Error(msg);
    }

    // returnResponse: 返回原始 Response 对象（调用方需自行处理 .json()/.ok 等）
    // 否则：自动解析 JSON
    return opts.returnResponse ? response : response.json();
}

window.appUrl = appUrl;
window.apiFetch = apiFetch;
window.apiErrorMessage = apiErrorMessage;
