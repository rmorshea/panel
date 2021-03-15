import * as p from "@bokehjs/core/properties";
import { clone } from "@bokehjs/core/util/object";
import { AbstractVTKView, AbstractVTKPlot } from "./vtklayout";
import { vtkns } from "./util";
import { FullScreenRenderWindowSynchronized } from "./panel_fullscreen_renwin_sync";
const CONTEXT_NAME = "panel";
export class VTKSynchronizedPlotView extends AbstractVTKView {
    initialize() {
        super.initialize();
        this._promises = [];
        this._renderable = false;
        this._arrays = {};
        this._decoded_arrays = {};
        this._pending_arrays = {};
        // Internal closures
        this.getArray = (hash) => {
            if (this._arrays[hash]) {
                return Promise.resolve(this._arrays[hash]);
            }
            return new Promise((resolve, reject) => {
                this._pending_arrays[hash] = { resolve, reject };
            });
        };
        this.registerArray = (hash, array) => {
            this._arrays[hash] = array;
            if (this._pending_arrays[hash]) {
                this._pending_arrays[hash].resolve(array);
            }
            return true;
        };
        // Context initialisation
        this._synchronizer_context = vtkns.SynchronizableRenderWindow.getSynchronizerContext(CONTEXT_NAME);
    }
    connect_signals() {
        super.connect_signals();
        this.connect(this.model.properties.arrays.change, () => this._decode_arrays());
        this.connect(this.model.properties.scene.change, () => {
            if (this.model.rebuild) {
                this._vtk_renwin = null;
                this.invalidate_render();
            }
            else {
                const state = clone(this.model.scene);
                Promise.all(this._promises).then(() => {
                    this._sync_plot(state, () => {
                        this._on_scene_ready();
                    });
                });
            }
        });
        this.connect(this.model.properties.one_time_reset.change, () => {
            this._vtk_renwin.getRenderWindow().clearOneTimeUpdaters();
        });
    }
    init_vtk_renwin() {
        this._vtk_renwin = FullScreenRenderWindowSynchronized.newInstance({
            rootContainer: this.el,
            container: this._vtk_container,
            synchronizerContext: this._synchronizer_context,
        });
    }
    plot() {
        this._vtk_renwin.getRenderWindow().clearOneTimeUpdaters();
        this._decode_arrays();
        const state = clone(this.model.scene);
        Promise.all(this._promises).then(() => {
            this._sync_plot(state, () => this._on_scene_ready()).then(() => {
                this._set_camera_state();
                this._get_camera_state();
            });
        });
    }
    _decode_arrays() {
        const jszip = new vtkns.ThirdParty.JSZip();
        const arrays = this.model.arrays;
        const registerArray = this.registerArray;
        const arrays_processed = this.model.arrays_processed;
        const model = this.model;
        function load(key) {
            return jszip
                .loadAsync(atob(arrays[key]))
                .then((zip) => zip.file("data/" + key))
                .then((zipEntry) => zipEntry.async("arraybuffer"))
                .then((arraybuffer) => registerArray(key, arraybuffer))
                .then(() => {
                arrays_processed.push(key);
                model.properties.arrays_processed.change.emit();
            });
        }
        Object.keys(arrays).forEach((key) => {
            if (!this._decoded_arrays[key]) {
                this._decoded_arrays[key] = true;
                this._promises.push(load(key));
            }
        });
    }
    _on_scene_ready() {
        if (this._promises.length > 0)
            return;
        this._renderable = true;
        this._camera_callbacks.push(this._vtk_renwin
            .getRenderer()
            .getActiveCamera()
            .onModified(() => this._vtk_render()));
        if (!this._orientationWidget)
            this._create_orientation_widget();
        if (!this._axes)
            this._set_axes();
        this._vtk_renwin.resize();
        this._vtk_render();
    }
    _sync_plot(state, onSceneReady) {
        // Need to ensure all promises are resolved before calling this function
        this._renderable = false;
        this._promises = [];
        this._unsubscribe_camera_cb();
        this._synchronizer_context.setFetchArrayFunction((hash) => {
            return Promise.resolve(this._arrays[hash]);
        });
        const renderer = this._synchronizer_context.getInstance(this.model.scene.dependencies[0].id);
        if (renderer && !this._vtk_renwin.getRenderer())
            this._vtk_renwin.getRenderWindow().addRenderer(renderer);
        return this._vtk_renwin
            .getRenderWindow()
            .synchronize(state).then(onSceneReady);
    }
}
VTKSynchronizedPlotView.__name__ = "VTKSynchronizedPlotView";
export class VTKSynchronizedPlot extends AbstractVTKPlot {
    constructor(attrs) {
        super(attrs);
        this.outline = vtkns.OutlineFilter.newInstance(); //use to display bouding box of a selected actor
        const mapper = vtkns.Mapper.newInstance();
        mapper.setInputConnection(this.outline.getOutputPort());
        this.outline_actor = vtkns.Actor.newInstance();
        this.outline_actor.setMapper(mapper);
    }
    getActors(ptr_ref) {
        let actors = this.renderer_el.getRenderer().getActors();
        if (ptr_ref) {
            const context = this.renderer_el.getSynchronizerContext(CONTEXT_NAME);
            actors = actors.filter((actor) => {
                const id_actor = context.getInstanceId(actor);
                return id_actor ? id_actor.slice(-16) == ptr_ref.slice(1, 17) : false;
            });
        }
        return actors;
    }
    static init_VTKSynchronizedPlot() {
        this.prototype.default_view = VTKSynchronizedPlotView;
        this.define({
            arrays: [p.Any, {}],
            arrays_processed: [p.Array, []],
            enable_keybindings: [p.Boolean, false],
            one_time_reset: [p.Boolean],
            rebuild: [p.Boolean, false],
            scene: [p.Any, {}],
        });
        this.override({
            height: 300,
            width: 300,
        });
    }
}
VTKSynchronizedPlot.__name__ = "VTKSynchronizedPlot";
VTKSynchronizedPlot.__module__ = "panel.models.vtk";
VTKSynchronizedPlot.init_VTKSynchronizedPlot();
//# sourceMappingURL=vtksynchronized.js.map