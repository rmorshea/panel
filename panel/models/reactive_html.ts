import {render} from 'preact';
import {useCallback} from 'preact/hooks';
import {html} from 'htm/preact';

import {build_views} from "@bokehjs/core/build_views"
import {isArray} from "@bokehjs/core/util/types"
import * as p from "@bokehjs/core/properties"
import {HTMLBox} from "@bokehjs/models/layouts/html_box"
import {LayoutDOM} from "@bokehjs/models/layouts/layout_dom"
import {empty, classes} from "@bokehjs/core/dom"
import {color2css} from "@bokehjs/core/util/color"

import {serializeEvent} from "./event-to-object";
import {DOMEvent, htmlDecode} from "./html"
import {PanelHTMLBoxView, set_size} from "./layout"


function serialize_attrs(attrs: any): any {
  const serialized: any = {}
  for (const attr in attrs) {
    let value = attrs[attr]
    if (typeof value !== "string")
      value = value
    else if (value !== "" && (value === "NaN" || !isNaN(Number(value))))
      value = Number(value)
    else if (value === 'false' || value === 'true')
      value = value === 'true' ? true : false
    serialized[attr] = value
  }
  return serialized
}

function escapeRegex(string: string) {
    return string.replace(/[-\/\\^$*+?.()|[\]]/g, '\\$&');
}

function extractToken(template: string, str: string, tokens: string[]) {
  const tokenMapping: any = {}
  for (const match of tokens)
    tokenMapping[`{${match}}`] = "(.*)"

  const tokenList = []
  let regexpTemplate = "^" + escapeRegex(template) + "$";

  // Find the order of the tokens
  let i, tokenIndex, tokenEntry;
  for (const m in tokenMapping) {
    tokenIndex = template.indexOf(m);

    // Token found
    if (tokenIndex > -1) {
      regexpTemplate = regexpTemplate.replace(m, tokenMapping[m]);
      tokenEntry = {
	index: tokenIndex,
	token: m
      };

      for (i = 0; i < tokenList.length && tokenList[i].index < tokenIndex; i++);

      // Insert it at index i
      if (i < tokenList.length)
	tokenList.splice(i, 0, tokenEntry)
      else
	tokenList.push(tokenEntry)
    }
  }

  regexpTemplate = regexpTemplate.replace(/\{[^{}]+\}/g, '.*');

  var match = new RegExp(regexpTemplate).exec(str)
  let result: any = null;

  if (match) {
    result = {};
    // Find your token entry
    for (i = 0; i < tokenList.length; i++)
      result[tokenList[i].token.slice(1, -1)] = match[i + 1]
  }

  return result;
}


export class ReactiveHTMLView extends PanelHTMLBoxView {
  model: ReactiveHTML
  html: string
  _changing: boolean = false
  _event_listeners: any = {}
  _mutation_observers: MutationObserver[] = []
  _script_fns: any = {}
  _state: any = {}

  initialize(): void {
    super.initialize()
    this.html = htmlDecode(this.model.html) || this.model.html
  }

  connect_signals(): void {
    super.connect_signals()
    this.connect(this.model.properties.children.change, () => this.rebuild())
    for (const prop in this.model.data.properties) {
      this.connect(this.model.data.properties[prop].change, () => {
        if (!this._changing) {
          this._update(prop)
	  this.invalidate_layout()
	}
      })
    }
    this.connect(this.model.properties.events.change, () => {
      this._remove_event_listeners()
      this._setup_event_listeners()
    })
    this.connect_scripts()
  }

  connect_scripts(): void {
    const id = this.model.data.id
    for (const prop in this.model.scripts) {
      const scripts = this.model.scripts[prop]
      for (const script of scripts) {
        const decoded_script = htmlDecode(script) || script
	const script_fn = this._render_script(decoded_script, id)
	const property = this.model.data.properties[prop]
	if (property == null) {
	  this._script_fns[prop] = script_fn
	  continue
	}
	this.connect(property.change, () => {
          if (!this._changing)
            script_fn(this.model, this.model.data, this._state)
	})
      }
    }
  }

  disconnect_signals(): void {
    super.disconnect_signals()
    this._remove_event_listeners()
    this._remove_mutation_observers()
  }

  get child_models(): LayoutDOM[] {
    const models = []
    for (const parent in this.model.children) {
      for (const model of this.model.children[parent])
        if (typeof model !== 'string')
          models.push(model)
    }
    return models
  }

  async build_child_views(): Promise<void> {
    await build_views(this._child_views, this.child_models, {parent: (null as any)})
  }

  update_layout(): void {
    this._update_layout()
  }

  render(): void {
    empty(this.el)

    const {background} = this.model
    this.el.style.backgroundColor = background != null ? color2css(background) : ""
    classes(this.el).clear().add(...this.css_classes())

    this._update()
    this._render_children()
    this._setup_mutation_observers()
    this._setup_event_listeners()
    const render_script = this._script_fns.render
    if (render_script != null)
      render_script(this.model, this.model.data, this._state)
  }

  private _send_event(elname: string, attr: string, event: any) {
    let serialized = serializeEvent(event)
    serialized.type = attr
    this.model.trigger_event(new DOMEvent(elname, serialized))
  }

  private _render_children(): void {
    const id = this.model.data.id
    for (const node in this.model.children) {
      const children = this.model.children[node]
      if (this.model.looped.indexOf(node) > -1) {
	for (let i = 0; i < children.length; i++) {
	  let el: any = document.getElementById(`${node}-${i}-${id}`)
	  if (el == null) {
            console.warn(`DOM node '${node}-${i}-${id}' could not be found. Cannot render children.`)
            continue
	  }
	  const cm = children[i]
          const view: any = this._child_views.get(cm)
          if (view == null)
            el.innerHTML = cm
	  else
            view.renderTo(el)
	}
      } else {
	let el: any = document.getElementById(`${node}-${id}`)
	if (el == null) {
          console.warn(`DOM node '${node}-${id}' could not be found. Cannot render children.`)
          continue
	}
	for (let i = 0; i < children.length; i++) {
          const cm = children[i]
          const view: any = this._child_views.get(cm)
          if (view == null)
            el.innerHTML = cm
	  else
            view.renderTo(el)
	}
      }
    }
  }

  after_layout(): void {
    super.after_layout()
    for (const child_view of this.child_views) {
      child_view.resize_layout()
      this._align_view(child_view)
    }
  }

  private _align_view(view: any): void {
    const {align} = view.model
    let halign, valign: string
    if (isArray(align))
      [halign, valign] = (align as any)
    else
      halign = valign = align
    if (halign === 'center') {
      view.el.style.marginLeft = 'auto';
      view.el.style.marginRight = 'auto';
    } else if (halign === 'end')
      view.el.style.marginLeft = 'auto';
    if (valign === 'center') {
      view.el.style.marginTop = 'auto';
      view.el.style.marginBottom = 'auto';
    } else if (valign === 'end')
      view.el.style.marginTop = 'auto';
  }


  private _render_html(literal: any, state: any={}): any {
    let htm = literal
    let callbacks = ''
    const methods: string[] = []
    for (const elname in this.model.callbacks) {
      for (const callback of this.model.callbacks[elname]) {
        const [cb, method] = callback;
        if (methods.indexOf(method) > -1)
          continue
        methods.push(method)
        callbacks = callbacks + `
        const ${method} = (event) => {
          view._send_event("${elname}", "${cb}", event)
        }
        `
        htm = htm.replace('${'+method, '$--{'+method)
      }
    }
    htm = (
      htm
        .replaceAll('${model.', '$-{model.')
        .replaceAll('${', '${data.')
        .replaceAll('$-{model.', '${model.')
        .replaceAll('$--{', '${')
    )
    return new Function("view, model, data, state, html, useCallback", callbacks+"return html`"+htm+"`;")(
      this, this.model, this.model.data, state, html, useCallback
    )
  }

  private _render_script(literal: any, id: string) {
    const scripts = []
    for (const elname of this.model.nodes) {
      const script = `
      const ${elname} = document.getElementById('${elname}-${id}')
      if (${elname} == null) {
        console.warn("DOM node '${elname}' could not be found. Cannot execute callback.")
        return
      }
      `
      scripts.push(script)
    }
    scripts.push(literal)
    return new Function("model, data, state", scripts.join('\n'))
  }

  private _remove_mutation_observers(): void {
    for (const observer of this._mutation_observers)
      observer.disconnect()
    this._mutation_observers = []
  }

  private _setup_mutation_observers(): void {
    const id = this.model.data.id
    for (const name in this.model.attrs) {
      const el: any = document.getElementById(`${name}-${id}`)
      if (el == null) {
        console.warn(`DOM node '${name}-${id}' could not be found. Cannot set up MutationObserver.`)
        continue
      }
      const observer = new MutationObserver(() => {
        this._update_model(el, name)
      })
      observer.observe(el, {attributes: true});
      this._mutation_observers.push(observer)
    }
  }

  private _remove_event_listeners(): void {
    const id = this.model.data.id
    for (const node in this._event_listeners) {
      const el: any = document.getElementById(`${node}-${id}`)
      if (el == null)
        continue
      for (const event_name in this._event_listeners[node]) {
        const event_callback = this._event_listeners[node][event_name]
        el.removeEventListener(event_name, event_callback)
      }
    }
    this._event_listeners = {}
  }

  private _setup_event_listeners(): void {
    const id = this.model.data.id
    for (const node in this.model.events) {
      const el: any = document.getElementById(`${node}-${id}`)
      if (el == null) {
        console.warn(`DOM node '${node}-${id}' could not be found. Cannot subscribe to DOM events.`)
        continue
      }
      const node_events = this.model.events[node]
      for (const event_name in node_events) {
        const event_callback = (event: any) => {
          this._send_event(node, event_name, event)
          if (node in this.model.attrs && node_events[event_name])
            this._update_model(el, node)
        }
        el.addEventListener(event_name, event_callback)
        if (!(node in this._event_listeners))
          this._event_listeners[node] = {}
        this._event_listeners[node][event_name] = event_callback
      }
    }
  }

  private _update(property: string | null = null): void {
    if (property == null || (this.html.indexOf(`\${${property}}`) > -1)) {
      const rendered = this._render_html(this.html)
      try {
        this._changing = true
        render(rendered, this.el)
      } finally {
        this._changing = false
      }
      if (this.el.children.length)
        set_size((this.el.children[0] as any), this.model)
    }
  }

  private _update_model(el: any, name: string): void {
    if (this._changing)
      return
    const attrs: any = {}
    for (const attr_info of this.model.attrs[name]) {
      const [attr, tokens, template] = attr_info
      let value = attr === 'children' ? el.innerHTML : el[attr]
      if (tokens.length === 1 && (`{${tokens[0]}}` === template))
        attrs[tokens[0]] = value
      else if (typeof value === 'string') {
        value = extractToken(template, value, tokens)
        if (value == null)
          console.warn(`Could not resolve parameters in ${name} element ${attr} attribute value ${value}.`)
        else {
          for (const param in value) {
            if (value[param] === undefined)
              console.warn(`Could not resolve ${param} in ${name} element ${attr} attribute value ${value}.`)
            else
              attrs[param] = value[param]
          }
        }
      }
    }
    try {
      this._changing = true
      this.model.data.setv(serialize_attrs(attrs))
    } finally {
      this._changing = false
    }
  }
}

export namespace ReactiveHTML {
  export type Attrs = p.AttrsOf<Props>

  export type Props = HTMLBox.Props & {
    attrs: p.Property<any>
    callbacks: p.Property<any>
    children: p.Property<any>
    data: p.Property<any>
    events: p.Property<any>
    html: p.Property<string>
    looped: p.Property<string[]>
    nodes: p.Property<string[]>
    scripts: p.Property<any>
  }
}

export interface ReactiveHTML extends ReactiveHTML.Attrs {}

export class ReactiveHTML extends HTMLBox {
  properties: ReactiveHTML.Props

  constructor(attrs?: Partial<ReactiveHTML.Attrs>) {
    super(attrs)
  }

  static __module__ = "panel.models.reactive_html"

  static init_ReactiveHTML(): void {
    this.prototype.default_view = ReactiveHTMLView
    this.define<ReactiveHTML.Props>(({Array, Any, String}) => ({
      attrs:     [ Any,    {} ],
      callbacks: [ Any,    {} ],
      children:  [ Any,    {} ],
      data:      [ Any,       ],
      events:    [ Any,    {} ],
      html:      [ String, "" ],
      looped:    [ Array(String), [] ],
      nodes:     [ Array(String), [] ],
      scripts:   [ Any,    {} ],
    }))
  }
}
