// 公共映射弹窗组件
//
// 被 mappings.html / pending_candidates.html 等页面复用。
// 调用方负责维护 currentMappings / currentRules，通过 showAdd/showEdit 传入；
// 保存成功后调用 onSaved 回调（如刷新列表、标记候选已确认）。
const MappingModal = (function () {
    let _modal = null;
    // 内部状态：由调用方传入，保存时用于合并提交
    const _state = {
        currentMappings: {},
        currentRules: [],
        editingType: 'exact', // 'exact' | 'regex'
        editingTitle: null,
        editingRuleIndex: null,
        onSaved: null,
    };

    function _ensureModal() {
        if (!_modal) {
            _modal = getModal('mappingModal');
        }
        return _modal;
    }

    function setMappingType(type) {
        const exactFields = document.getElementById('exact-fields');
        const regexFields = document.getElementById('regex-fields');
        if (!exactFields || !regexFields) return;
        if (type === 'regex') {
            exactFields.classList.add('is-hidden');
            regexFields.classList.remove('is-hidden');
        } else {
            exactFields.classList.remove('is-hidden');
            regexFields.classList.add('is-hidden');
        }
    }

    function _resetForm() {
        const form = document.getElementById('mapping-form');
        if (form) form.reset();
        const previewId = document.getElementById('preview-id');
        if (previewId) previewId.value = '';
        const previewLink = document.getElementById('preview-link');
        if (previewLink) previewLink.classList.add('is-hidden');
    }

    /**
     * 打开"添加映射"弹窗
     * @param {object} opts
     *   - title {string} 预填标题
     *   - season {number|string} 预填季度
     *   - currentMappings {object} 当前映射快照（必传）
     *   - currentRules {array} 当前规则快照（必传）
     *   - onSaved {function(subjectId, title, season)} 保存成功回调
     */
    function showAdd(opts) {
        opts = opts || {};
        _state.currentMappings = opts.currentMappings || {};
        _state.currentRules = opts.currentRules || [];
        _state.editingType = 'exact';
        _state.editingTitle = null;
        _state.editingRuleIndex = null;
        _state.onSaved = opts.onSaved || null;

        _ensureModal();
        document.getElementById('mappingModalTitle').textContent = '添加映射';
        _resetForm();
        document.getElementById('type-exact').checked = true;
        setMappingType('exact');

        if (opts.title) {
            const titleInput = document.getElementById('mapping-title');
            if (titleInput) titleInput.value = opts.title;
        }
        if (opts.season !== undefined && opts.season !== null && opts.season !== '') {
            const seasonInput = document.getElementById('mapping-season');
            if (seasonInput) seasonInput.value = opts.season;
        }
        _modal.show();
    }

    /**
     * 打开"编辑映射"弹窗
     * @param {object} opts
     *   - type {'exact'|'regex'}
     *   - key {string|number} exact: title; regex: index
     *   - currentMappings {object}
     *   - currentRules {array}
     *   - onSaved {function(subjectId, title, season)} 保存成功回调
     */
    function showEdit(opts) {
        opts = opts || {};
        _state.currentMappings = opts.currentMappings || {};
        _state.currentRules = opts.currentRules || [];
        _state.onSaved = opts.onSaved || null;

        _ensureModal();
        _resetForm();

        if (opts.type === 'regex') {
            const idx = opts.key;
            const rule = _state.currentRules[idx];
            if (!rule) return;
            document.getElementById('mappingModalTitle').textContent = '编辑正则规则';
            document.getElementById('type-regex').checked = true;
            setMappingType('regex');
            document.getElementById('rule-pattern').value = rule.pattern || '';
            document.getElementById('mapping-id').value = rule.subject_id || '';
            document.getElementById('rule-desc').value = rule.description || '';
            _state.editingType = 'regex';
            _state.editingTitle = null;
            _state.editingRuleIndex = idx;
        } else {
            const title = opts.key;
            const value = _state.currentMappings[title];
            let id = '';
            let season = '';
            if (typeof value === 'object' && value !== null) {
                id = value.subject_id || '';
                season = value.season || '';
            } else {
                id = value;
            }
            document.getElementById('mappingModalTitle').textContent = '编辑映射';
            document.getElementById('type-exact').checked = true;
            setMappingType('exact');
            document.getElementById('mapping-title').value = title;
            document.getElementById('mapping-id').value = id;
            document.getElementById('mapping-season').value = season;
            _state.editingType = 'exact';
            _state.editingTitle = title;
            _state.editingRuleIndex = null;
        }
        updatePreview();
        _modal.show();
    }

    function updatePreview() {
        const id = document.getElementById('mapping-id').value;
        const previewId = document.getElementById('preview-id');
        const previewLink = document.getElementById('preview-link');
        if (!previewId || !previewLink) return;
        previewId.value = id;
        if (id) {
            previewLink.href = `https://bgm.tv/subject/${id}`;
            previewLink.classList.remove('is-hidden');
        } else {
            previewLink.classList.add('is-hidden');
        }
    }

    async function save() {
        const type = document.querySelector('input[name="mapping-type"]:checked').value;
        const id = document.getElementById('mapping-id').value.trim();

        if (!id) {
            showAlert('请填写 Bangumi ID', 'warning');
            return;
        }
        if (!/^\d+$/.test(id)) {
            showAlert('Bangumi ID必须是数字', 'warning');
            return;
        }

        if (type === 'exact') {
            const title = document.getElementById('mapping-title').value.trim();
            const seasonRaw = document.getElementById('mapping-season').value.trim();

            if (!title) {
                showAlert('请填写番剧名称', 'warning');
                return;
            }
            const season = seasonRaw ? parseInt(seasonRaw, 10) : null;
            if (seasonRaw && (!season || season < 1)) {
                showAlert('季度必须为正整数', 'warning');
                return;
            }

            try {
                let newMappings = { ..._state.currentMappings };

                // 编辑时若标题变更，删除旧映射
                if (_state.editingType === 'exact' && _state.editingTitle && _state.editingTitle !== title) {
                    delete newMappings[_state.editingTitle];
                }
                // 从正则规则切换为精确映射时，删除原规则
                if (_state.editingType === 'regex' && _state.editingRuleIndex !== null) {
                    let newRules = [..._state.currentRules];
                    newRules.splice(_state.editingRuleIndex, 1);
                    const preBody = { mappings: _state.currentMappings, rules: newRules };
                    const preResult = await apiFetch('/api/mappings', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(preBody)
                    });
                    if (preResult.status !== 'success') {
                        showAlert('保存映射失败: ' + preResult.message, 'danger');
                        return;
                    }
                    _state.currentRules = newRules;
                }

                if (season) {
                    newMappings[title] = { subject_id: id, season: season };
                } else {
                    newMappings[title] = id;
                }

                const result = await apiFetch('/api/mappings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mappings: newMappings, rules: _state.currentRules })
                });

                if (result.status === 'success') {
                    showAlert(_state.editingTitle ? '映射更新成功' : '映射添加成功', 'success');
                    _modal.hide();
                    if (_state.onSaved) {
                        _state.onSaved(id, title, season);
                    }
                } else {
                    showAlert('保存映射失败: ' + result.message, 'danger');
                }
            } catch (error) {
                console.error('保存映射失败:', error);
                showAlert('保存映射失败', 'danger');
            }
        } else {
            // 正则规则
            const pattern = document.getElementById('rule-pattern').value.trim();
            const desc = document.getElementById('rule-desc').value.trim();

            if (!pattern) {
                showAlert('请填写正则表达式', 'warning');
                return;
            }

            let newRules = [..._state.currentRules];
            const newRule = { pattern: pattern, subject_id: id };
            if (desc) newRule.description = desc;

            if (_state.editingType === 'regex' && _state.editingRuleIndex !== null) {
                newRules[_state.editingRuleIndex] = newRule;
            } else {
                newRules.push(newRule);
            }

            try {
                let bodyMappings = _state.currentMappings;
                // 从精确映射切换为正则规则时，删除原映射
                if (_state.editingType === 'exact' && _state.editingTitle) {
                    bodyMappings = { ..._state.currentMappings };
                    delete bodyMappings[_state.editingTitle];
                }

                const result = await apiFetch('/api/mappings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mappings: bodyMappings, rules: newRules })
                });
                if (result.status === 'success') {
                    showAlert(_state.editingRuleIndex !== null ? '规则更新成功' : '规则添加成功', 'success');
                    _modal.hide();
                    if (_state.onSaved) {
                        _state.onSaved(id, pattern, null);
                    }
                } else {
                    showAlert('保存规则失败: ' + result.message, 'danger');
                }
            } catch (error) {
                console.error('保存规则失败:', error);
                showAlert('保存规则失败', 'danger');
            }
        }
    }

    return {
        showAdd,
        showEdit,
        save,
        setMappingType,
        updatePreview,
    };
})();
