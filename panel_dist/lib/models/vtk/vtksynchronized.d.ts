import * as p from "@bokehjs/core/properties";
import { AbstractVTKView, AbstractVTKPlot } from "./vtklayout";
export declare class VTKSynchronizedPlotView extends AbstractVTKView {
    model: VTKSynchronizedPlot;
    protected _synchronizer_context: any;
    protected _arrays: any;
    protected _decoded_arrays: any;
    protected _pending_arrays: any;
    protected _promises: Promise<any>[];
    getArray: CallableFunction;
    registerArray: CallableFunction;
    initialize(): void;
    connect_signals(): void;
    init_vtk_renwin(): void;
    plot(): void;
    _decode_arrays(): void;
    _on_scene_ready(): void;
    _sync_plot(state: any, onSceneReady: CallableFunction): any;
}
export declare namespace VTKSynchronizedPlot {
    type Attrs = p.AttrsOf<Props>;
    type Props = AbstractVTKPlot.Props & {
        arrays: p.Property<any>;
        arrays_processed: p.Property<string[]>;
        one_time_reset: p.Property<boolean>;
        rebuild: p.Property<boolean>;
        scene: p.Property<any>;
    };
}
export interface VTKSynchronizedPlot extends VTKSynchronizedPlot.Attrs {
}
export declare class VTKSynchronizedPlot extends AbstractVTKPlot {
    properties: VTKSynchronizedPlot.Props;
    outline: any;
    outline_actor: any;
    static __module__: string;
    constructor(attrs?: Partial<VTKSynchronizedPlot.Attrs>);
    getActors(ptr_ref?: string): [any];
    static init_VTKSynchronizedPlot(): void;
}
