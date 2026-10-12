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
        // 编辑既有映射时，标题下已有的完整配置对象（用于保留 segments 等字段）
        editingEntry: null,
        // 打开弹窗时，该条目已有的 segments 条数。用于区分两种「开关是关的」：
        //   - 本来就没有分段（0）→ 什么都不做
        //   - 有多条分段、界面无法编辑（>1）→ 必须原样保留，否则会静默清空
        originalSegmentsCount: 0,
    };

    function _val(id) {
        const el = document.getElementById(id);
        return el ? el.value : '';
    }

    function _setVal(id, v) {
        const el = document.getElementById(id);
        if (el) el.value = v;
    }

    // ---------------------------------------------------------------
    // 集数分段：场景选择 → 生成 segments
    //
    // segments 中 from/to 用的是**媒体库该季的实际集号**（即 Emby 的
    // ParentIndexNumber / IndexNumber 原样带来的值），offset 是该段第 1 集
    // 对应的目标条目集号。
    // ---------------------------------------------------------------

    function toggleSegments(enabled) {
        const body = document.getElementById('segments-body');
        if (body) body.classList.toggle('is-hidden', !enabled);
        if (enabled) {
            onSegmentModeChange();
        } else {
            const preview = document.getElementById('segment-preview');
            if (preview) preview.innerHTML = '';
        }
    }

    function onSegmentModeChange() {
        const mode = _val('segment-mode');
        const toWrap = document.getElementById('seg-to-wrap');
        const fromWrap = document.getElementById('seg-from-wrap');
        if (toWrap) toWrap.classList.toggle('is-hidden', mode !== 'range');
        if (fromWrap) fromWrap.classList.toggle('is-hidden', mode === 'whole');
        if (mode === 'whole') {
            _setVal('seg-from', 1);
        }
        updateSegmentPreview();
    }

    /** 由当前表单生成一个 segment 对象；输入非法时返回 null */
    function buildSegment() {
        const sid = _val('seg-subject-id').trim();
        if (!sid || !/^\d+$/.test(sid)) return null;

        const mode = _val('segment-mode');
        const fromRaw = _val('seg-from').trim();
        const toRaw = _val('seg-to').trim();
        const offRaw = _val('seg-offset').trim();

        const from = mode === 'whole' ? 1 : parseInt(fromRaw, 10);
        if (!from || from < 1) return null;

        let to = null;
        if (mode === 'range') {
            if (!toRaw) return null;
            to = parseInt(toRaw, 10);
            if (!to || to < from) return null;
        }

        // offset 允许 0 与负数（场景 J：媒体库集号比目标集号大时用），
        // 故只校验「是整数」，不校验正负；算出的目标集号 < 1 时后端会跳过并告警。
        const offsetRaw = offRaw === '' ? 1 : parseInt(offRaw, 10);
        if (Number.isNaN(offsetRaw)) return null;
        const offset = offsetRaw;

        const seg = { from: from, to: to, subject_id: sid, offset: offset };
        // season 取上方「季度」输入；为空则不写（对该标题所有季生效）
        const seasonRaw = _val('mapping-season').trim();
        if (seasonRaw) {
            const season = parseInt(seasonRaw, 10);
            if (season > 0) seg.season = season;
        }
        return seg;
    }

    function updateSegmentPreview() {
        const preview = document.getElementById('segment-preview');
        if (!preview) return;
        if (!document.getElementById('segments-enabled')?.checked) {
            preview.innerHTML = '';
            return;
        }
        const seg = buildSegment();
        if (!seg) {
            preview.innerHTML =
                '<span class="text-danger">请填写完整且合法的分段信息（集号与条目 ID 须为整数）</span>';
            return;
        }
        const seasonText = seg.season ? `S${seg.season} ` : '';
        const span = seg.to === null ? `E${seg.from} 起` : `E${seg.from}–E${seg.to}`;
        const first = seg.offset;
        const second = seg.offset + 1;
        preview.innerHTML =
            `<span class="text-success">` +
            `媒体库 ${seasonText}${span} → 条目 <a href="https://bgm.tv/subject/${seg.subject_id}" target="_blank">${seg.subject_id}</a>` +
            `（E${seg.from}→该条目第 ${first} 集，E${seg.from + 1}→第 ${second} 集，依次类推）` +
            `</span>`;
    }

    /**
     * 调后端拉取目标条目的章节列表，推算该段第 1 集对应的目标集号。
     *
     * offset 的语义是**条目内第几集（ep）**，不是章节的 sort。
     * 后端 `_match_target_ep_rows` 先按 ep（`sort − 首个正片 sort + 1`）匹配，
     * 匹配不上才回退 sort；所以「把本段第 1 集对齐到该条目第 1 集」= offset 1。
     *
     * 历史 bug：这里曾填 `eps[0].sort`。对 sort 不从 1 开始的条目（如 sort 13–36），
     * 会填出 offset=13，媒体 E1 被算成目标第 13 集，然后按 **ep** 命中真正的第 13 集
     * （sort 25）—— 静默标错且预览看不出来。现在改为 ep 语义。
     *
     * 仍会请求章节列表：用于确认该条目确实有正片章节（没有则无法推算），
     * 并让用户核对集数是否与预期一致。
     */
    async function prefillOffset() {
        const sid = _val('seg-subject-id').trim();
        if (!sid || !/^\d+$/.test(sid)) {
            showAlert('请先填写目标 Bangumi 条目 ID', 'warning');
            return;
        }
        const mode = _val('segment-mode');
        const from = mode === 'whole' ? 1 : parseInt(_val('seg-from').trim(), 10);
        if (!from || from < 1) {
            showAlert('请先填写有效的起始集号', 'warning');
            return;
        }

        try {
            const resp = await apiFetch(`/api/mappings/subject/${sid}/episodes`);
            if (resp.status !== 'success' || !Array.isArray(resp.episodes)) {
                showAlert('获取章节列表失败', 'danger');
                return;
            }
            const eps = resp.episodes;
            if (eps.length === 0) {
                showAlert('该条目下没有章节数据，无法自动推算，请手动填写', 'warning');
                return;
            }

            // offset 用 ep 语义（条目内第几集）：「本段第 1 集」对齐到该条目第 1 集，
            // 即 offset = 1。不能填 sort —— 后端按 ep 匹配，填 sort 会错位。
            _setVal('seg-offset', 1);
            updateSegmentPreview();

            // 若本段起始集号不是 1，提示用户确认意图 —— 这类配置最容易配错
            const hint =
                from > 1
                    ? `第 ${from} 集 → 该条目第 1 集。若你其实想从该条目的其它集开始，请手动改「对应该条目的第几集」。`
                    : `第 1 集 → 该条目第 1 集。`;
            showAlert(
                `已按该条目章节推算：${hint}（该条目共 ${eps.length} 个正片章节，请核对预览）`,
                'success'
            );
        } catch (error) {
            console.error('自动推算失败:', error);
            showAlert('自动推算失败，请手动填写', 'danger');
        }
    }

    function _resetSegments() {
        const enabled = document.getElementById('segments-enabled');
        if (enabled) enabled.checked = false;
        _setVal('segment-mode', 'whole');
        _setVal('seg-from', 1);
        _setVal('seg-to', '');
        _setVal('seg-subject-id', '');
        _setVal('seg-offset', 1);
        toggleSegments(false);
    }

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
        _state.editingEntry = null;
        _state.originalSegmentsCount = 0;
        _resetSegments();
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
            document.getElementById('rule-season').value =
                rule.season === undefined || rule.season === null ? '' : rule.season;
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
            _state.editingEntry =
                typeof value === 'object' && value !== null ? value : null;

            // 回填已有的集数分段（只支持一条；多条属手改 JSON 的高级用法，
            // 此处提示用户去手动编辑，避免界面上悄悄丢掉其余分段）
            const segs = _state.editingEntry && Array.isArray(_state.editingEntry.segments)
                ? _state.editingEntry.segments
                : [];
            _state.originalSegmentsCount = segs.length;
            if (segs.length === 1) {
                const s = segs[0];
                document.getElementById('segments-enabled').checked = true;
                toggleSegments(true);
                if (s.to === null || s.to === undefined) {
                    _setVal('segment-mode', (s.from || 1) > 1 ? 'from' : 'whole');
                } else {
                    _setVal('segment-mode', 'range');
                }
                _setVal('seg-from', s.from || 1);
                _setVal('seg-to', s.to === null || s.to === undefined ? '' : s.to);
                _setVal('seg-subject-id', s.subject_id || '');
                _setVal('seg-offset', s.offset || 1);
                onSegmentModeChange();
            } else if (segs.length > 1) {
                const preview = document.getElementById('segment-preview');
                if (preview) {
                    preview.innerHTML =
                        `<span class="text-warning">该条目配置了 ${segs.length} 条分段，` +
                        '此界面不支持下编辑多条；请直接编辑 bangumi_mapping.json，' +
                        '保存本表单不会改动 segments。</span>';
                }
            }
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

                // 集数分段（可选）
                const segEnabled = document.getElementById('segments-enabled')?.checked;
                let newSegment = null;
                if (segEnabled) {
                    newSegment = buildSegment();
                    if (!newSegment) {
                        showAlert(
                            '集数分段填写不完整：集号与目标条目 ID 必须为正整数，且结束集不小于起始集',
                            'warning'
                        );
                        return;
                    }
                }

                // 合并写回：已有配置对象时只更新 subject_id/season/segments，
                // 保留本弹窗不编辑的其它字段（原实现整体重建对象会抹掉它们）。
                const existing = newMappings[title];
                const isObj =
                    existing && typeof existing === 'object' && !Array.isArray(existing);
                const merged = isObj ? Object.assign({}, existing) : {};

                merged.subject_id = id;
                if (season) {
                    merged.season = season;
                } else {
                    delete merged.season;
                }
                if (segEnabled && newSegment) {
                    merged.segments = [newSegment];
                } else if (!segEnabled && _state.originalSegmentsCount === 1) {
                    // 只有「本来就有 1 条分段、用户在界面上把它关掉了」才删除。
                    //
                    // 不能只看 `!segEnabled && 'segments' in merged`：多条分段时
                    // 界面根本无法编辑（showEdit 已提示「保存本表单不会改动
                    // segments」），开关必然处于关闭状态，照上面那种写法会把这
                    // 些分段**静默删光**，与该提示自相矛盾。
                    delete merged.segments;
                }
                // 其余情况（本来没有分段、或多条分段界面不可编辑）保持 merged
                // 中的 segments 原样不动。

                // 仅当存在扩展字段时才写对象形式，否则保持简洁的简单格式
                const hasExtra = Object.keys(merged).some(
                    (k) => k !== 'subject_id' && k !== 'season'
                );
                if (season || hasExtra || isObj) {
                    newMappings[title] = merged;
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

            // 限定季度（可选）：同名番剧多季且各季是不同条目时用
            const seasonRaw = document.getElementById('rule-season').value.trim();
            if (seasonRaw) {
                const ruleSeason = parseInt(seasonRaw, 10);
                if (!ruleSeason || ruleSeason < 1) {
                    showAlert('限定季度必须为正整数（或留空表示对所有季生效）', 'warning');
                    return;
                }
                newRule.season = ruleSeason;
            }

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
        // 集数分段（场景选择式）
        toggleSegments,
        onSegmentModeChange,
        updateSegmentPreview,
        prefillOffset,
    };
})();
