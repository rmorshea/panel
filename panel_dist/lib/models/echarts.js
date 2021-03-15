import { HTMLBox, HTMLBoxView } from "@bokehjs/models/layouts/html_box";
export class EChartsView extends HTMLBoxView {
    connect_signals() {
        super.connect_signals();
        this.connect(this.model.properties.data.change, () => this._plot());
        const { width, height, renderer, theme } = this.model.properties;
        this.on_change([width, height], () => this._resize());
        this.on_change([theme, renderer], () => this.render());
    }
    render() {
        super.render();
        const config = { width: this.model.width, height: this.model.height, renderer: this.model.renderer };
        if (this._chart != null)
            window.echarts.dispose(this._chart);
        this._chart = window.echarts.init(this.el, this.model.theme, config);
        this._plot();
    }
    after_layout() {
        super.after_layout();
        this._chart.resize();
    }
    _plot() {
        if (window.echarts == null)
            return;
        this._chart.setOption(this.model.data);
    }
    _resize() {
        this._chart.resize({ width: this.model.width, height: this.model.height });
    }
}
EChartsView.__name__ = "EChartsView";
export class ECharts extends HTMLBox {
    constructor(attrs) {
        super(attrs);
    }
    static init_ECharts() {
        this.prototype.default_view = EChartsView;
        this.define(({ Any, String }) => ({
            data: [Any, {}],
            theme: [String, "default"],
            renderer: [String, "canvas"]
        }));
    }
}
ECharts.__name__ = "ECharts";
ECharts.__module__ = "panel.models.echarts";
ECharts.init_ECharts();
//# sourceMappingURL=echarts.js.map