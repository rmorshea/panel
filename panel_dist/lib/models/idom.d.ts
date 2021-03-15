import * as p from "@bokehjs/core/properties";
import { HTMLBox } from "@bokehjs/models/layouts/html_box";
import { PanelHTMLBoxView } from "./layout";
export declare function mountLayout(mountElement: any, saveUpdateHook: any, sendEvent: any, importSourceUrl: string): void;
export default function Layout({ saveUpdateHook, sendEvent, importSourceUrl }: {
    saveUpdateHook: any;
    sendEvent: any;
    importSourceUrl: string;
}): import("preact").VNode<{}>;
export declare class IDOMView extends PanelHTMLBoxView {
    model: IDOM;
    _update: any;
    connect_signals(): void;
    fix_layout(): void;
    initialize(): void;
    lazy_initialize(): Promise<void>;
    _save_update(update: any): any;
    render(): Promise<void>;
    _send(event: any): any;
}
export declare namespace IDOM {
    type Attrs = p.AttrsOf<Props>;
    type Props = HTMLBox.Props & {
        event: p.Property<any>;
        importSourceUrl: p.Property<string>;
        msg: p.Property<any>;
    };
}
export interface IDOM extends IDOM.Attrs {
}
export declare class IDOM extends HTMLBox {
    properties: IDOM.Props;
    constructor(attrs?: Partial<IDOM.Attrs>);
    static __module__: string;
    static init_IDOM(): void;
}
