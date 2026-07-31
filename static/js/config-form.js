/**
 * 配置表单自动序列化工具
 *
 * 基于 name="section.key" 属性自动收集/填充表单，消除 populateForm/saveConfig
 * 的镜像重复。特殊字段（敏感字段、多用户、多实例段）通过钩子处理。
 *
 * 使用方式：
 *   // 填充表单
 *   ConfigForm.populate(config);
 *   // 收集表单
 *   const data = ConfigForm.serialize();
 */

const ConfigForm = {
    /**
     * 从配置对象填充表单（基于 name="section.key"）
     * @param {Object} config - 配置对象 { section: { key: value } }
     * @param {Object} options - 选项
     * @param {string[]} options.skipSections - 跳过的段名（如多实例段 webhook/email）
     */
    populate(config, options = {}) {
        const { skipSections = [] } = options;
        document.querySelectorAll('#config-form [name]').forEach((el) => {
            const parts = el.name.split('.');
            if (parts.length < 2) return;
            const section = parts[0];
            const key = parts.slice(1).join('.');
            if (skipSections.includes(section)) return;

            const value = this._getPath(config, el.name);
            if (el.type === 'checkbox') {
                el.checked = Boolean(value);
            } else if (value != null) {
                el.value = String(value);
            } else {
                // 值为 null/undefined 时清空
                el.value = '';
            }
        });
    },

    /**
     * 收集表单数据（基于 name="section.key"）
     * @param {Object} options - 选项
     * @param {string[]} options.skipSections - 跳过的段名
     * @param {string[]} options.skipEmpty - 空值跳过的字段（如敏感字段）
     * @returns {Object} 配置对象 { section: { key: value } }
     */
    serialize(options = {}) {
        const { skipSections = [], skipEmpty = [] } = options;
        const data = {};

        document.querySelectorAll('#config-form [name]').forEach((el) => {
            const parts = el.name.split('.');
            if (parts.length < 2) return;
            const section = parts[0];
            const key = parts.slice(1).join('.');
            if (skipSections.includes(section)) return;

            let value;
            if (el.type === 'checkbox') {
                value = el.checked;
            } else if (el.type === 'number' || this._isNumericField(el)) {
                value = el.value === '' ? '' : Number(el.value);
                if (isNaN(value)) value = 0;
            } else {
                value = el.value;
            }

            // 敏感字段空值跳过
            if (skipEmpty.includes(el.name) && value === '') return;

            this._setPath(data, el.name, value);
        });

        return data;
    },

    /**
     * 判断是否为数值字段（通过 data-type 属性或 number 类型）
     */
    _isNumericField(el) {
        return el.dataset.type === 'number' || el.dataset.type === 'int' || el.dataset.type === 'float';
    },

    /**
     * 按 "section.key" 路径获取值
     */
    _getPath(obj, path) {
        return path.split('.').reduce((acc, key) => (acc == null ? acc : acc[key]), obj);
    },

    /**
     * 按 "section.key" 路径设置值
     */
    _setPath(obj, path, value) {
        const parts = path.split('.');
        let cur = obj;
        for (let i = 0; i < parts.length - 1; i++) {
            if (!cur[parts[i]]) cur[parts[i]] = {};
            cur = cur[parts[i]];
        }
        cur[parts[parts.length - 1]] = value;
    },
};

// 暴露到全局（兼容现有非 ES Module 加载方式）
window.ConfigForm = ConfigForm;
