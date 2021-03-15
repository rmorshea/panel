import { HTMLBox } from "@bokehjs/models/layouts/html_box";
import { div } from "@bokehjs/core/dom";
import { ColumnDataSource } from "@bokehjs/models/sources/column_data_source";
import { PanelHTMLBoxView, set_size } from "./layout";
const PERSPECTIVE_VIEWER_CLASSES = [
    "perspective-viewer-material",
    "perspective-viewer-material-dark",
    "perspective-viewer-material-dense",
    "perspective-viewer-material-dense-dark",
    "perspective-viewer-vaporwave",
];
function is_not_perspective_class(item) {
    return !PERSPECTIVE_VIEWER_CLASSES.includes(item);
}
function theme_to_class(theme) {
    return "perspective-viewer-" + theme;
}
/** Helper function used to incrementally build a html element string
 *
 *  For example toAttribute("columns", ['x','y']) returns ' columns="['x','y']"
 *  For example toAttribute("columns", null) returns ""
 *
 * @param attribute
 * @param value
 */
function toAttribute(attribute, value) {
    if (value == null)
        return "";
    else if (typeof value !== "string")
        value = JSON.stringify(value);
    return " " + attribute + "='" + value + "'";
}
export class PerspectiveView extends PanelHTMLBoxView {
    constructor() {
        super(...arguments);
        this._updating = false;
        this._config_listener = null;
        this._event_listener = null;
        this._loaded = false;
    }
    connect_signals() {
        super.connect_signals();
        this.connect(this.model.source.properties.data.change, () => this.setData());
        this.connect(this.model.properties.toggle_config.change, () => {
            this.perspective_element.toggleConfig();
            this.fix_layout();
        });
        this.connect(this.model.properties.columns.change, () => {
            this.updateAttribute("columns", this.model.columns, true);
        });
        this.connect(this.model.properties.computed_columns.change, () => {
            this.updateAttribute("computed-columns", this.model.computed_columns, true);
        });
        this.connect(this.model.properties.column_pivots.change, () => {
            this.updateAttribute("column-pivots", this.model.column_pivots, true);
        });
        this.connect(this.model.properties.row_pivots.change, () => {
            this.updateAttribute("row-pivots", this.model.row_pivots, true);
        });
        this.connect(this.model.properties.aggregates.change, () => {
            this.updateAttribute("aggregates", this.model.aggregates, true);
        });
        this.connect(this.model.properties.filters.change, () => {
            this.updateAttribute("filters", this.model.filters, true);
        });
        this.connect(this.model.properties.sort.change, () => {
            this.updateAttribute("sort", this.model.sort, true);
        });
        this.connect(this.model.properties.plugin.change, () => {
            this.updateAttribute("plugin", this.model.plugin, false);
        });
        this.connect(this.model.properties.selectable.change, () => {
            this.updateAttribute("selectable", this.model.selectable, true);
        });
        this.connect(this.model.properties.editable.change, () => {
            this.updateAttribute("editable", this.model.editable, true);
        });
        this.connect(this.model.properties.theme.change, () => this.updateTheme());
        if (this.model.document != null) {
            this._event_listener = (event) => this.on_event(event);
            this.model.document.on_change(this._event_listener);
        }
    }
    disconnect_signals() {
        if (this._config_listener != null)
            this.perspective_element.removeEventListener("perspective-config-update", this._config_listener);
        this._config_listener = null;
        if (this.model.document != null && this._event_listener != null)
            this.model.document.remove_on_change(this._event_listener);
        this._event_listener = null;
        super.disconnect_signals();
    }
    render() {
        super.render();
        this.worker = window.perspective.worker();
        this.table = this.worker.table(this.data);
        const container = div({ class: "pnx-perspective-viewer" });
        set_size(container, this.model);
        container.innerHTML = this.getInnerHTML();
        this.perspective_element = container.children[0];
        set_size(this.perspective_element, this.model);
        this.el.appendChild(container);
        this.perspective_element.load(this.table).then(() => {
            this.update_config();
            this._config_listener = () => this.sync_config();
            if (this.model.toggle_config)
                this.perspective_element.toggleConfig();
            this.perspective_element.addEventListener("perspective-config-update", this._config_listener);
            this._loaded = true;
        });
    }
    fix_layout() {
        this.update_layout();
        this.compute_layout();
        this.invalidate_layout();
    }
    sync_config() {
        if (this._updating)
            return;
        const config = this.perspective_element.save();
        const props = {};
        for (const option in config) {
            const prop = option.replace('-', '_');
            const value = config[option];
            if (value === undefined || (prop == 'plugin' && value === "debug"))
                continue;
            props[prop] = value;
        }
        this._updating = true;
        this.model.setv(props);
        this._updating = false;
    }
    update_config() {
        if (this._updating)
            return;
        const config = this.perspective_element.save();
        for (const option in config) {
            const prop = option.replace('-', '_');
            let value = this.model.property(prop).get_value();
            if (config[option] !== value) {
                this._updating = true;
                if (prop !== 'plugin')
                    value = JSON.stringify(value);
                this.perspective_element.setAttribute(option, value);
                this._updating = false;
            }
        }
    }
    on_event(event) {
        event = event.hint;
        if (event == null || event.column_source == null || event.column_source.id != this.model.source.id)
            return;
        else if (event.rollover !== undefined)
            this.stream(event.data, event.rollover);
        else if (event.patches !== undefined)
            this.patch(event.patches);
    }
    get data() {
        const data = {};
        for (const column of this.model.source.columns())
            data[column] = this.model.source.get_array(column);
        return data;
    }
    stream(data, rollover) {
        if (!this._loaded)
            return;
        else if (rollover == null)
            this.table.update(data);
        else
            this.table.replace(this.data);
    }
    patch(_) {
        this.table.replace(this.data);
    }
    getInnerHTML() {
        let innerHTML = "<perspective-viewer style='height:100%;width:100%;'";
        innerHTML += toAttribute("class", theme_to_class(this.model.theme));
        innerHTML += "></perspective-viewer>";
        return innerHTML;
    }
    setData() {
        if (this._loaded)
            this.table.load(this.data);
    }
    updateAttribute(attribute, value, stringify) {
        if (this._updating)
            return;
        const config = this.perspective_element.save();
        const old_value = config[attribute];
        if (value == old_value)
            return;
        if (stringify)
            value = JSON.stringify(value);
        this._updating = true;
        this.perspective_element.setAttribute(attribute, value);
        this._updating = false;
    }
    updateTheme() {
        // When you update the class attribute you have to be careful
        // For example when the user is dragging an element then 'dragging' is a part of the class attribute
        let old_class = this.perspective_element.getAttribute("class");
        let new_class = this.toNewClassAttribute(old_class, this.model.theme);
        this.perspective_element.setAttribute("class", new_class);
    }
    /** Helper function to generate the new class attribute string
     *
     * If old_class = 'perspective-viewer-material dragging' and theme = 'material-dark'
     * then 'perspective-viewer-material-dark dragging' is returned
     *
     * @param old_class For example 'perspective-viewer-material' or 'perspective-viewer-material dragging'
     * @param theme The name of the new theme. For example 'material-dark'
     */
    toNewClassAttribute(old_class, theme) {
        let new_classes = [];
        if (old_class != null)
            new_classes = old_class.split(" ").filter(is_not_perspective_class);
        new_classes.push(theme_to_class(theme));
        return new_classes.join(" ");
    }
}
PerspectiveView.__name__ = "PerspectiveView";
export class Perspective extends HTMLBox {
    constructor(attrs) {
        super(attrs);
    }
    static init_Perspective() {
        this.prototype.default_view = PerspectiveView;
        this.define(({ Any, Array, Boolean, Ref, Nullable, String }) => ({
            aggregates: [Any,],
            column_pivots: [Nullable(Array(String)),],
            columns: [Array(String),],
            computed_columns: [Nullable(Array(String)),],
            editable: [Nullable(Boolean),],
            filters: [Nullable(Array(Any)),],
            plugin: [String,],
            plugin_config: [Any,],
            row_pivots: [Nullable(Array(String)),],
            selectable: [Nullable(Boolean),],
            toggle_config: [Boolean, true],
            sort: [Nullable(Array(Array(String))),],
            source: [Ref(ColumnDataSource),],
            theme: [String,],
        }));
    }
}
Perspective.__name__ = "Perspective";
Perspective.__module__ = "panel.models.perspective";
Perspective.init_Perspective();
//# sourceMappingURL=perspective.js.map