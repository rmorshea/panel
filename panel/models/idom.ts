import {mountLayout} from 'idom-client-react';

import * as p from "@bokehjs/core/properties"
import {HTMLBox} from "@bokehjs/models/layouts/html_box"
import {PanelHTMLBoxView, set_size} from "./layout"

export class IDOMView extends PanelHTMLBoxView {
  model: IDOM
  _update: any

  connect_signals(): void {
    super.connect_signals()
    this.connect(this.model.properties.event.change, () => {
      this._update(...this.model.event)
      setTimeout(() => { requestAnimationFrame(() => this.fix_layout()) })
   })
  }

  fix_layout(): void {
    this.update_layout()
    this.compute_layout()
    this.invalidate_layout()
    set_size(this.el, this.model)
  }

  initialize(): void {
    super.initialize()
    mountLayout(
      this.el,
      {
        saveUpdateHook: (update: any) => this._save_update(update),
        sendEvent: (event: any) => this._send(event),
        loadImportSource: (source: any, sourceType: any) => import(
          sourceType == "NAME" ? this.model.importSourceUrl + "/" + source : source),
      },
    )
  }

  async lazy_initialize(): Promise<void> {
    await super.lazy_initialize()
    await new Promise((resolve) => {
      const check_update = () => {
	if (this._update)
	  resolve(null)
	else
	  setTimeout(check_update, 100);
      }
      check_update()
    })
  }

  _save_update(update: any): any {
    this._update = update
  }

  async render(): Promise<void> {
    super.render()
    this._update(...this.model.event)
    await new Promise((resolve) => {
      const check_update = () => {
	if (this.el.children.length) {
	  this.fix_layout()
	  resolve(null)
	} else
	  setTimeout(check_update, 50)
      }
      check_update()
    })
  }

  _send(event: any): any {
    this.model.msg = event
  }
}

export namespace IDOM {
  export type Attrs = p.AttrsOf<Props>

  export type Props = HTMLBox.Props & {
    event: p.Property<any>
    importSourceUrl: p.Property<string>
    msg: p.Property<any>
  }
}

export interface IDOM extends IDOM.Attrs {}

export class IDOM extends HTMLBox {
  properties: IDOM.Props

  constructor(attrs?: Partial<IDOM.Attrs>) {
    super(attrs)
  }

  static __module__ = "panel.models.idom"

  static init_IDOM(): void {
    this.prototype.default_view = IDOMView
    this.define<IDOM.Props>(({Any, String}) => ({
      event:           [ Any,    [] ],
      importSourceUrl: [ String, '' ],
      msg:             [ Any,    {} ],
    }))
  }
}
