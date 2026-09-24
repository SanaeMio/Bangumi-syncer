/**
 * 屏蔽关键词卡片组件 + 一键加入黑名单
 *
 * 数据源：数据库 ``blocked_rules`` 表，经 ``/api/blocked-keywords`` 管理。
 * 历史实现读写 INI 的 ``[sync] blocked_keywords``；该配置项已废弃并迁入 DB
 * （原因：与 title_blacklist 合并 —— 见 app/core/database/blocked_rules.py）。
 *
 * 用法：
 *   // 配置页：初始化关键词列表（绑定到 _blocked_keywords.html 中的元素）
 *   BlockedKeywords.initChips()
 *   // 同步记录页：一键加入
 *   BlockedKeywords.addFromRecord(title)
 */
const BlockedKeywords = {
  _chipsEl: null,
  _addInput: null,
  /** 当前关键词列表（含来源信息，供渲染区分手填 / 拒绝自动记录） */
  _items: [],

  /**
   * 初始化屏蔽关键词列表
   * @param {string} [chipsId] 列表容器 id（默认 blocked-keywords-chips）
   */
  async initChips(chipsId) {
    const chipsEl = document.getElementById(chipsId || 'blocked-keywords-chips');
    if (!chipsEl) return;

    this._chipsEl = chipsEl;
    this._addInput = document.getElementById('blocked-keywords-input');

    await this._reload();

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

  /** 从后端拉取并渲染 */
  async _reload() {
    try {
      const resp = await apiFetch('/api/blocked-keywords', { method: 'GET' });
      if (resp.status === 'success') {
        this._items = (resp.data && resp.data.keywords) || [];
      } else {
        this._items = [];
      }
    } catch (e) {
      this._items = [];
    }
    this._render();
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
    if (!this._addInput) return;
    const val = this._addInput.value.trim();
    if (!val) return;

    // 本地去重（大小写不敏感），避免多余的请求
    if (this._items.some((it) => (it.keyword || '').toLowerCase() === val.toLowerCase())) {
      showAlert(`「${val}」已在屏蔽关键词中`, 'info');
      this._addInput.value = '';
      return;
    }

    try {
      const resp = await apiFetch('/api/blocked-keywords', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keyword: val }),
      });
      if (resp.status === 'success') {
        showAlert(
          resp.data && resp.data.added
            ? `已添加屏蔽关键词：${val}`
            : `「${val}」已在屏蔽关键词中`,
          resp.data && resp.data.added ? 'success' : 'info',
        );
        this._addInput.value = '';
        await this._reload();
      } else {
        showAlert('添加失败: ' + (resp.message || ''), 'danger');
      }
    } catch (e) {
      showAlert('添加失败: ' + e.message, 'danger');
    }
    this._addInput?.focus();
  },

  /** 渲染关键词列表 */
  _render() {
    if (!this._chipsEl) return;
    this._chipsEl.innerHTML = '';

    if (!this._items.length) {
      this._chipsEl.innerHTML =
        '<div class="text-muted small py-2">暂无屏蔽关键词</div>';
      return;
    }

    const list = document.createElement('div');
    list.className = 'd-flex flex-wrap gap-2';
    this._items.forEach((item) => {
      const kw = item.keyword || '';
      const isReject = item.source === 'reject';
      const chip = document.createElement('span');
      chip.className =
        'badge rounded-pill bg-danger bg-opacity-75 d-inline-flex align-items-center py-2 px-3';
      chip.style.fontSize = '0.85rem';
      // 拒绝候选时自动记录的来源标注，便于用户区分"我填的"与"系统记的"
      const srcHint = isReject
        ? '<span class="ms-2 opacity-75" style="font-size:.7rem;">拒绝时记录</span>'
        : '';
      chip.innerHTML =
        `<span>${escapeHtml(kw)}</span>${srcHint}` +
        `<button type="button" class="btn btn-sm btn-light ms-2 px-1 py-0 lh-1" ` +
        `style="font-size:.75rem;border-radius:50%;" aria-label="删除" title="删除">` +
        `<i class="bi bi-trash-fill text-danger"></i></button>`;
      chip.querySelector('button').addEventListener('click', async () => {
        try {
          const resp = await apiFetch(
            `/api/blocked-keywords/${encodeURIComponent(kw)}`,
            { method: 'DELETE' },
          );
          if (resp.status === 'success') {
            showAlert(`已删除屏蔽关键词：${kw}`, 'success');
            await this._reload();
          } else {
            showAlert('删除失败: ' + (resp.message || ''), 'danger');
          }
        } catch (e) {
          showAlert('删除失败: ' + e.message, 'danger');
        }
      });
      list.appendChild(chip);
    });
    this._chipsEl.appendChild(list);
  },

  /**
   * 一键把标题加入屏蔽关键词
   * @param {string} title
   */
  async addFromRecord(title) {
    if (!title || !title.trim()) {
      showAlert('标题为空，无法加入黑名单', 'warning');
      return;
    }
    const kw = title.trim();
    try {
      const resp = await apiFetch('/api/blocked-keywords', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keyword: kw }),
      });
      if (resp.status === 'success') {
        showAlert(
          resp.data && resp.data.added
            ? `已加入屏蔽关键词：${kw}`
            : `「${kw}」已在屏蔽关键词中`,
          resp.data && resp.data.added ? 'success' : 'info',
        );
        // 若配置页已打开，同步刷新列表
        if (this._chipsEl) await this._reload();
      } else {
        showAlert('加入黑名单失败: ' + (resp.message || ''), 'danger');
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
