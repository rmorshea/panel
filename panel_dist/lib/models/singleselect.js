import { select, option } from "@bokehjs/core/dom";
import { isString } from "@bokehjs/core/util/types";
import { InputWidget, InputWidgetView } from "@bokehjs/models/widgets/input_widget";
import * as inputs from "@bokehjs/styles/widgets/inputs.css";
export class SingleSelectView extends InputWidgetView {
    connect_signals() {
        super.connect_signals();
        this.connect(this.model.properties.value.change, () => this.render_selection());
        this.connect(this.model.properties.options.change, () => this.render());
        this.connect(this.model.properties.name.change, () => this.render());
        this.connect(this.model.properties.title.change, () => this.render());
        this.connect(this.model.properties.size.change, () => this.render());
        this.connect(this.model.properties.disabled.change, () => this.render());
    }
    render() {
        super.render();
        const options = this.model.options.map((opt) => {
            let value, _label;
            if (isString(opt))
                value = _label = opt;
            else
                [value, _label] = opt;
            return option({ value }, _label);
        });
        this.select_el = select({
            multiple: false,
            class: inputs.input,
            name: this.model.name,
            disabled: this.model.disabled,
        }, options);
        this.select_el.style.backgroundImage = 'none';
        this.select_el.addEventListener("change", () => this.change_input());
        this.group_el.appendChild(this.select_el);
        this.render_selection();
    }
    render_selection() {
        const selected = this.model.value;
        for (const el of this.el.querySelectorAll('option'))
            if (el.value === selected)
                el.selected = true;
        // Note that some browser implementations might not reduce
        // the number of visible options for size <= 3.
        this.select_el.size = this.model.size;
    }
    change_input() {
        const is_focused = this.el.querySelector('select:focus') != null;
        let value = null;
        for (const el of this.el.querySelectorAll('option')) {
            if (el.selected) {
                value = el.value;
                break;
            }
        }
        this.model.value = value;
        super.change_input();
        // Restore focus back to the <select> afterwards,
        // so that even if python on_change callback is invoked,
        // focus remains on <select> and one can seamlessly scroll
        // up/down.
        if (is_focused)
            this.select_el.focus();
    }
}
SingleSelectView.__name__ = "SingleSelectView";
export class SingleSelect extends InputWidget {
    constructor(attrs) {
        super(attrs);
    }
    static init_SingleSelect() {
        this.prototype.default_view = SingleSelectView;
        this.define(({ Any, Array, Int, String }) => ({
            value: [String, ""],
            options: [Array(Any), []],
            size: [Int, 4],
        }));
    }
}
SingleSelect.__name__ = "SingleSelect";
SingleSelect.__module__ = "panel.models.widgets";
SingleSelect.init_SingleSelect();
//# sourceMappingURL=singleselect.js.map