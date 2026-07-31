// 提示与剪贴板 —— Toast 通知与复制到剪贴板工具

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

window.showAlert = showAlert;
window.copyToClipboard = copyToClipboard;
