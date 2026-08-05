/**
 * 屏蔽关键词卡片组件 + 一键加入黑名单
 *
 * 用法：
 *   // 配置页：初始化关键词列表（绑定到 _blocked_keywords.html 中的元素）
 *   BlockedKeywords.initChips('blocked-keywords', 'blocked-keywords-chips')
 *   // 同步记录页：一键加入
 *   BlockedKeywords.addFromRecord(title)
 */
const BlockedKeywords = {
  _input: null,
  _chipsEl: null,
  _addInput: null,

  /**
   * 初始化屏蔽关键词列表
   * @param {string} inputId  隐藏 input 的 id（用于 ConfigForm.serialize）
   * @param {string} chipsId  列表容器的 id
   */
  initChips(inputId, chipsId) {
    const input = document.getElementById(inputId);
    const chipsEl = document.getElementById(chipsId);
    if (!input || !chipsEl) return;

    this._input = input;
    this._chipsEl = chipsEl;
    this._addInput = document.getElementById('blocked-keywords-input');

    this._render();

    // 绑定回车添加
    if (this._addInput) {
      this._addInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          this.confirmAdd();
        }
      });
    }
  },

  /** 显示添加输入区并聚焦 */
  showAddInput() {
    const area = document.getElementById('blocked-keywords-input-area');
    if (!area) return;
    area.style.display = 'flex';
    const input = document.getElementById('blocked-keywords-input');
    if (input) input.focus();
  },

  /** 取消添加，隐藏输入区并清空 */
  cancelAdd() {
    const area = document.getElementById('blocked-keywords-input-area');
    if (area) area.style.display = 'none';
    if (this._addInput) this._addInput.value = '';
  },

  /** 确认添加关键词 */
  async confirmAdd() {
    if (!this._addInput || !this._input) return;
    const val = this._addInput.value.trim();
    if (!val) return;
    const list = this._parse(this._input.value);
    const existed = list.some((k) => k.toLowerCase() === val.toLowerCase());
    if (!existed) {
      list.push(val);
      this._input.value = list.join(',');
    }
    this._addInput.value = '';
    this._render();
    // 仅当确实新增时才提交
    if (!existed) {
      const ok = await this._save();
      if (ok) showAlert(`已添加屏蔽关键词：${val}`, 'success');
    }
    this._addInput?.focus();
  },

  /** 渲染关键词列表 */
  _render() {
    if (!this._chipsEl || !this._input) return;
    const keywords = this._parse(this._input.value);
    this._chipsEl.innerHTML = '';

    if (keywords.length === 0) {
      this._chipsEl.innerHTML =
        '<div class="text-muted small py-2">暂无屏蔽关键词</div>';
      return;
    }

    const list = document.createElement('div');
    list.className = 'd-flex flex-wrap gap-2';
    keywords.forEach((kw, idx) => {
      const chip = document.createElement('span');
      chip.className =
        'badge rounded-pill bg-danger bg-opacity-75 d-inline-flex align-items-center py-2 px-3';
      chip.style.fontSize = '0.85rem';
      chip.innerHTML =
        `<span>${escapeHtml(kw)}</span>` +
        `<button type="button" class="btn btn-sm btn-light ms-2 px-1 py-0 lh-1" ` +
        `style="font-size:.75rem;border-radius:50%;" aria-label="删除" title="删除">` +
        `<i class="bi bi-trash-fill text-danger"></i></button>`;
      chip.querySelector('button').addEventListener('click', async () => {
        const arr = this._parse(this._input.value);
        const removed = arr.splice(idx, 1)[0];
        this._input.value = arr.join(',');
        this._render();
        const ok = await this._save();
        if (ok) showAlert(`已删除屏蔽关键词：${removed}`, 'success');
      });
      list.appendChild(chip);
    });
    this._chipsEl.appendChild(list);
  },

  /**
   * 提交当前屏蔽关键词到后端（部分更新 sync.blocked_keywords）
   * @returns {Promise<boolean>} 是否保存成功
   */
  async _save() {
    if (!this._input) return false;
    try {
      const resp = await apiFetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sync: { blocked_keywords: this._input.value },
        }),
      });
      if (resp.status !== 'success') {
        showAlert('保存屏蔽关键词失败: ' + (resp.message || ''), 'danger');
        return false;
      }
      return true;
    } catch (e) {
      showAlert('保存屏蔽关键词失败: ' + e.message, 'danger');
      return false;
    }
  },

  /**
   * 一键把标题加入屏蔽关键词（调用配置 API）
   * @param {string} title
   */
  async addFromRecord(title) {
    if (!title || !title.trim()) {
      showAlert('标题为空，无法加入黑名单', 'warning');
      return;
    }
    const kw = title.trim();
    try {
      // 1. 读取当前配置（同步记录页可能未初始化 _input）
      const cfgResp = await apiFetch('/api/config', { method: 'GET' });
      if (cfgResp.status !== 'success') {
        showAlert('读取配置失败', 'danger');
        return;
      }
      const current =
        (cfgResp.data.sync && cfgResp.data.sync.blocked_keywords) || '';
      const list = this._parse(current);
      if (list.some((k) => k.toLowerCase() === kw.toLowerCase())) {
        showAlert(`「${kw}」已在屏蔽关键词中`, 'info');
        return;
      }
      list.push(kw);
      // 2. 保存
      const saveResp = await apiFetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sync: { blocked_keywords: list.join(',') } }),
      });
      if (saveResp.status === 'success') {
        showAlert(`已加入屏蔽关键词：${kw}`, 'success');
        // 若配置页已打开，同步刷新列表
        if (this._input) {
          this._input.value = list.join(',');
          this._render();
        }
      } else {
        showAlert('加入黑名单失败: ' + (saveResp.message || ''), 'danger');
      }
    } catch (e) {
      showAlert('加入黑名单失败: ' + e.message, 'danger');
    }
  },

  /** 解析逗号分隔字符串为关键词数组（去空白、去空、去重） */
  _parse(str) {
    if (!str) return [];
    return Array.from(
      new Set(
        String(str)
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean),
      ),
    );
  },
};

window.BlockedKeywords = BlockedKeywords;
