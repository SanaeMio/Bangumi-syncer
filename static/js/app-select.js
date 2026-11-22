// 把原生 <select class="form-select"> 换成自定义下拉，保留原 select 的 id / name / change。
(function () {
    var valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
    var indexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex");

    function optionLabel(opt) {
        return (opt && (opt.textContent || opt.label || opt.value) || "").trim();
    }

    function interceptProgrammaticValue(select, sync) {
        if (!valueDesc || !valueDesc.get || !valueDesc.set) return;
        Object.defineProperty(select, "value", {
            configurable: true,
            enumerable: true,
            get: function () {
                return valueDesc.get.call(select);
            },
            set: function (next) {
                valueDesc.set.call(select, next);
                sync();
            },
        });
        if (!indexDesc || !indexDesc.get || !indexDesc.set) return;
        Object.defineProperty(select, "selectedIndex", {
            configurable: true,
            enumerable: true,
            get: function () {
                return indexDesc.get.call(select);
            },
            set: function (next) {
                indexDesc.set.call(select, next);
                sync();
            },
        });
    }

    function rebuildPanel(select, panel, labelEl) {
        panel.replaceChildren();
        Array.from(select.options).forEach(function (opt) {
            if (opt.hidden) return;
            var item = document.createElement("button");
            item.type = "button";
            item.className = "dropdown-item" + (opt.selected ? " active" : "");
            item.textContent = optionLabel(opt);
            item.disabled = opt.disabled;
            item.addEventListener("click", function () {
                if (select.value !== opt.value) {
                    select.value = opt.value;
                    select.dispatchEvent(new Event("change", { bubbles: true }));
                }
            });
            panel.appendChild(item);
        });
        var selected = select.options[select.selectedIndex];
        if (labelEl) labelEl.textContent = optionLabel(selected) || "";
    }

    function enhance(select) {
        if (!select || select.dataset.appSelect === "1" || select.multiple) return;
        select.dataset.appSelect = "1";

        var wrap = document.createElement("div");
        wrap.className = "dropdown app-select-host";
        select.parentNode.insertBefore(wrap, select);
        wrap.appendChild(select);
        select.classList.add("visually-hidden");
        select.tabIndex = -1;
        select.setAttribute("aria-hidden", "true");

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "app-select";
        btn.setAttribute("data-bs-toggle", "dropdown");
        btn.setAttribute("data-bs-display", "static");
        btn.setAttribute("aria-expanded", "false");
        btn.disabled = select.disabled;

        var labelEl = document.createElement("span");
        var icon = document.createElement("i");
        icon.className = "bi bi-chevron-down";
        icon.setAttribute("aria-hidden", "true");
        btn.appendChild(labelEl);
        btn.appendChild(icon);

        var menu = document.createElement("div");
        menu.className = "dropdown-menu";
        var panel = document.createElement("div");
        panel.className = "dropdown-menu__panel";
        menu.appendChild(panel);
        wrap.appendChild(btn);
        wrap.appendChild(menu);

        function sync() {
            rebuildPanel(select, panel, labelEl);
            btn.disabled = select.disabled;
        }

        interceptProgrammaticValue(select, sync);
        select.addEventListener("change", sync);
        if (select.form) {
            select.form.addEventListener("reset", function () {
                requestAnimationFrame(sync);
            });
        }
        new MutationObserver(sync).observe(select, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["disabled", "selected"],
        });
        sync();
    }

    function enhanceAll(root) {
        (root.querySelectorAll ? root : document).querySelectorAll("select.form-select").forEach(enhance);
    }

    enhanceAll(document);
    new MutationObserver(function (records) {
        records.forEach(function (record) {
            record.addedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (node.matches && node.matches("select.form-select")) enhance(node);
                else if (node.querySelectorAll) enhanceAll(node);
            });
        });
    }).observe(document.documentElement, { childList: true, subtree: true });
})();
