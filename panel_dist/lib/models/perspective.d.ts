import { HTMLBox } from "@bokehjs/models/layouts/html_box";
import * as p from "@bokehjs/core/properties";
import { ColumnDataSource } from "@bokehjs/models/sources/column_data_source";
import { PanelHTMLBoxView } from "./layout";
export declare class PerspectiveView extends PanelHTMLBoxView {
    model: Perspective;
    perspective_element: any;
    table: any;
    worker: any;
    _updating: boolean;
    _config_listener: any;
    _event_listener: any;
    _loaded: boolean;
    connect_signals(): void;
    disconnect_signals(): void;
    render(): void;
    fix_layout(): void;
    sync_config(): void;
    update_config(): void;
    on_event(event: any): void;
    get data(): any;
    stream(data: any, rollover: any): void;
    patch(_: any): void;
    private getInnerHTML;
    setData(): void;
    updateAttribute(attribute: string, value: any, stringify: boolean): void;
    updateTheme(): void;
    /** Helper function to generate the new class attribute string
     *
     * If old_class = 'perspective-viewer-material dragging' and theme = 'material-dark'
     * then 'perspective-viewer-material-dark dragging' is returned
     *
     * @param old_class For example 'perspective-viewer-material' or 'perspective-viewer-material dragging'
     * @param theme The name of the new theme. For example 'material-dark'
     */
    private toNewClassAttribute;
}
export declare namespace Perspective {
    type Attrs = p.AttrsOf<Props>;
    type Props = HTMLBox.Props & {
        aggregates: p.Property<any>;
        column_pivots: p.Property<any[] | null>;
        columns: p.Property<any[]>;
        computed_columns: p.Property<any[] | null>;
        editable: p.Property<boolean | null>;
        filters: p.Property<any[] | null>;
        plugin: p.Property<any>;
        plugin_config: p.Property<any>;
        row_pivots: p.Property<any[] | null>;
        selectable: p.Property<boolean | null>;
        toggle_config: p.Property<boolean>;
        sort: p.Property<any[] | null>;
        source: p.Property<ColumnDataSource>;
        theme: p.Property<any>;
    };
}
export interface Perspective extends Perspective.Attrs {
}
export declare class Perspective extends HTMLBox {
    properties: Perspective.Props;
    constructor(attrs?: Partial<Perspective.Attrs>);
    static __module__: string;
    static init_Perspective(): void;
}
