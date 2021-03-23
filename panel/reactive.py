"""
Declares Syncable and Reactive classes which provides baseclasses
for Panel components which sync their state with one or more bokeh
models rendered on the frontend.
"""

import difflib
import sys
import threading

from collections import Counter, defaultdict, namedtuple
from functools import partial

import bleach
import numpy as np
import param

from bokeh.models import LayoutDOM
from param.parameterized import ParameterizedMetaclass
from tornado import gen

from .config import config
from .io.callbacks import PeriodicCallback
from .io.model import hold
from .io.notebook import push, push_on_root
from .io.server import unlocked
from .io.state import state
from .models.reactive_html import (
    ReactiveHTML as _BkReactiveHTML, ReactiveHTMLParser, construct_data_model
)
from .util import edit_readonly, escape, updating
from .viewable import Layoutable, Renderable, Viewable

LinkWatcher = namedtuple("Watcher","inst cls fn mode onlychanged parameter_names what queued target links transformed bidirectional_watcher")


class Syncable(Renderable):
    """
    Syncable is an extension of the Renderable object which can not
    only render to a bokeh model but also sync the parameters on the
    object with the properties on the model.

    In order to bi-directionally link parameters with bokeh model
    instances the _link_params and _link_props methods define
    callbacks triggered when either the parameter or bokeh property
    values change. Since there may not be a 1-to-1 mapping between
    parameter and the model property the _process_property_change and
    _process_param_change may be overridden to apply any necessary
    transformations.
    """

    # Timeout if a notebook comm message is swallowed
    _timeout = 20000

    # Timeout before the first event is processed
    _debounce = 50

    # Any parameters that require manual updates handling for the models
    # e.g. parameters which affect some sub-model
    _manual_params = []

    # Mapping from parameter name to bokeh model property name
    _rename = {}

    # Allows defining a mapping from model property name to a JS code
    # snippet that transforms the object before serialization
    _js_transforms = {}

    # Transforms from input value to bokeh property value
    _source_transforms = {}
    _target_transforms = {}

    __abstract = True

    def __init__(self, **params):
        super().__init__(**params)

        # Useful when updating model properties which trigger potentially
        # recursive events
        self._updating = False

        # A dictionary of current property change events
        self._events = {}

        # Any watchers associated with links between two objects
        self._links = []
        self._link_params()

        # A dictionary of bokeh property changes being processed
        self._changing = {}

        # Sets up watchers to process manual updates to models
        if self._manual_params:
            self.param.watch(self._update_manual, self._manual_params)

    #----------------------------------------------------------------
    # Model API
    #----------------------------------------------------------------

    def _process_property_change(self, msg):
        """
        Transform bokeh model property changes into parameter updates.
        Should be overridden to provide appropriate mapping between
        parameter value and bokeh model change. By default uses the
        _rename class level attribute to map between parameter and
        property names.
        """
        inverted = {v: k for k, v in self._rename.items()}
        return {inverted.get(k, k): v for k, v in msg.items()}

    def _process_param_change(self, msg):
        """
        Transform parameter changes into bokeh model property updates.
        Should be overridden to provide appropriate mapping between
        parameter value and bokeh model change. By default uses the
        _rename class level attribute to map between parameter and
        property names.
        """
        properties = {self._rename.get(k, k): v for k, v in msg.items()
                      if self._rename.get(k, False) is not None}
        if 'width' in properties and self.sizing_mode is None:
            properties['min_width'] = properties['width']
        if 'height' in properties and self.sizing_mode is None:
            properties['min_height'] = properties['height']
        return properties

    @property
    def _linkable_params(self):
        """
        Parameters that can be linked in JavaScript via source
        transforms.
        """
        return [p for p in self._synced_params if self._rename.get(p, False) is not None
                and self._source_transforms.get(p, False) is not None] + ['loading']

    @property
    def _synced_params(self):
        """
        Parameters which are synced with properties using transforms
        applied in the _process_param_change method.
        """
        ignored = ['default_layout', 'loading']
        return [p for p in self.param if p not in self._manual_params+ignored]

    def _init_params(self):
        return {k: v for k, v in self.param.get_param_values()
                if k in self._synced_params and v is not None}

    def _link_params(self):
        params = self._synced_params
        if params:
            watcher = self.param.watch(self._param_change, params)
            self._callbacks.append(watcher)

    def _link_props(self, model, properties, doc, root, comm=None):
        ref = root.ref['id']
        if config.embed:
            return

        for p in properties:
            if isinstance(p, tuple):
                _, p = p
            if comm:
                model.on_change(p, partial(self._comm_change, doc, ref, comm))
            else:
                model.on_change(p, partial(self._server_change, doc, ref))

    def _manual_update(self, events, model, doc, root, parent, comm):
        """
        Method for handling any manual update events, i.e. events triggered
        by changes in the manual params.
        """

    def _update_manual(self, *events):
        for ref, (model, parent) in self._models.items():
            if ref not in state._views or ref in state._fake_roots:
                continue
            viewable, root, doc, comm = state._views[ref]
            if comm or state._unblocked(doc):
                with unlocked():
                    self._manual_update(events, model, doc, root, parent, comm)
                if comm and 'embedded' not in root.tags:
                    push(doc, comm)
            else:
                cb = partial(self._manual_update, events, model, doc, root, parent, comm)
                if doc.session_context:
                    doc.add_next_tick_callback(cb)
                else:
                    cb()

    def _update_model(self, events, msg, root, model, doc, comm):
        self._changing[root.ref['id']] = [
            attr for attr, value in msg.items()
            if not model.lookup(attr).property.matches(getattr(model, attr), value)
        ]
        try:
            model.update(**msg)
        finally:
            del self._changing[root.ref['id']]

    def _cleanup(self, root):
        super()._cleanup(root)
        ref = root.ref['id']
        self._models.pop(ref, None)
        comm, client_comm = self._comms.pop(ref, (None, None))
        if comm:
            try:
                comm.close()
            except Exception:
                pass
        if client_comm:
            try:
                client_comm.close()
            except Exception:
                pass

    def _param_change(self, *events):
        msgs = []
        for event in events:
            msg = self._process_param_change({event.name: event.new})
            if msg:
                msgs.append(msg)

        events = {event.name: event for event in events}
        msg = {k: v for msg in msgs for k, v in msg.items()}
        if not msg:
            return

        for ref, (model, parent) in self._models.items():
            if ref not in state._views or ref in state._fake_roots:
                continue
            viewable, root, doc, comm = state._views[ref]
            if comm or not doc.session_context or state._unblocked(doc):
                with unlocked():
                    self._update_model(events, msg, root, model, doc, comm)
                if comm and 'embedded' not in root.tags:
                    push(doc, comm)
            else:
                cb = partial(self._update_model, events, msg, root, model, doc, comm)
                doc.add_next_tick_callback(cb)

    def _process_events(self, events):
        with edit_readonly(state):
            state.busy = True
        try:
            with edit_readonly(self):
                self.param.set_param(**self._process_property_change(events))
        finally:
            with edit_readonly(state):
                state.busy = False

    @gen.coroutine
    def _change_coroutine(self, doc=None):
        self._change_event(doc)

    def _change_event(self, doc=None):
        try:
            state.curdoc = doc
            thread = threading.current_thread()
            thread_id = thread.ident if thread else None
            state._thread_id = thread_id
            events = self._events
            self._events = {}
            self._process_events(events)
        finally:
            state.curdoc = None
            state._thread_id = None

    def _comm_change(self, doc, ref, comm, attr, old, new):
        if attr in self._changing.get(ref, []):
            self._changing[ref].remove(attr)
            return

        with hold(doc, comm=comm):
            self._process_events({attr: new})

    def _server_change(self, doc, ref, attr, old, new):
        if attr in self._changing.get(ref, []):
            self._changing[ref].remove(attr)
            return

        state._locks.clear()
        processing = bool(self._events)
        self._events.update({attr: new})
        if not processing:
            if doc.session_context:
                doc.add_timeout_callback(
                    partial(self._change_coroutine, doc),
                    self._debounce
                )
            else:
                self._change_event(doc)


class Reactive(Syncable, Viewable):
    """
    Reactive is a Viewable object that also supports syncing between
    the objects parameters and the underlying bokeh model either via
    the defined pyviz_comms.Comm type or using bokeh server.

    In addition it defines various methods which make it easy to link
    the parameters to other objects.
    """

    #----------------------------------------------------------------
    # Public API
    #----------------------------------------------------------------

    def add_periodic_callback(self, callback, period=500, count=None,
                              timeout=None, start=True):
        """
        Schedules a periodic callback to be run at an interval set by
        the period. Returns a PeriodicCallback object with the option
        to stop and start the callback.

        Arguments
        ---------
        callback: callable
          Callable function to be executed at periodic interval.
        period: int
          Interval in milliseconds at which callback will be executed.
        count: int
          Maximum number of times callback will be invoked.
        timeout: int
          Timeout in seconds when the callback should be stopped.
        start: boolean (default=True)
          Whether to start callback immediately.

        Returns
        -------
        Return a PeriodicCallback object with start and stop methods.
        """
        self.param.warning(
            "Calling add_periodic_callback on a Panel component is "
            "deprecated and will be removed in the next minor release. "
            "Use the pn.state.add_periodic_callback API instead."
        )
        cb = PeriodicCallback(callback=callback, period=period,
                              count=count, timeout=timeout)
        if start:
            cb.start()
        return cb

    def link(self, target, callbacks=None, bidirectional=False,  **links):
        """
        Links the parameters on this object to attributes on another
        object in Python. Supports two modes, either specify a mapping
        between the source and target object parameters as keywords or
        provide a dictionary of callbacks which maps from the source
        parameter to a callback which is triggered when the parameter
        changes.

        Arguments
        ---------
        target: object
          The target object of the link.
        callbacks: dict
          Maps from a parameter in the source object to a callback.
        bidirectional: boolean
          Whether to link source and target bi-directionally
        **links: dict
          Maps between parameters on this object to the parameters
          on the supplied object.
        """
        if links and callbacks:
            raise ValueError('Either supply a set of parameters to '
                             'link as keywords or a set of callbacks, '
                             'not both.')
        elif not links and not callbacks:
            raise ValueError('Declare parameters to link or a set of '
                             'callbacks, neither was defined.')
        elif callbacks and bidirectional:
            raise ValueError('Bidirectional linking not supported for '
                             'explicit callbacks. You must define '
                             'separate callbacks for each direction.')

        _updating = []
        def link(*events):
            for event in events:
                if event.name in _updating: continue
                _updating.append(event.name)
                try:
                    if callbacks:
                        callbacks[event.name](target, event)
                    else:
                        setattr(target, links[event.name], event.new)
                finally:
                    _updating.pop(_updating.index(event.name))
        params = list(callbacks) if callbacks else list(links)
        cb = self.param.watch(link, params)

        bidirectional_watcher = None
        if bidirectional:
            _reverse_updating = []
            reverse_links = {v: k for k, v in links.items()}
            def reverse_link(*events):
                for event in events:
                    if event.name in _reverse_updating: continue
                    _reverse_updating.append(event.name)
                    try:
                        setattr(self, reverse_links[event.name], event.new)
                    finally:
                        _reverse_updating.remove(event.name)
            bidirectional_watcher = target.param.watch(reverse_link, list(reverse_links))

        link = LinkWatcher(*tuple(cb)+(target, links, callbacks is not None, bidirectional_watcher))
        self._links.append(link)
        return cb

    def controls(self, parameters=[], jslink=True):
        """
        Creates a set of widgets which allow manipulating the parameters
        on this instance. By default all parameters which support
        linking are exposed, but an explicit list of parameters can
        be provided.

        Arguments
        ---------
        parameters: list(str)
           An explicit list of parameters to return controls for.
        jslink: bool
           Whether to use jslinks instead of Python based links.
           This does not allow using all types of parameters.

        Returns
        -------
        A layout of the controls
        """
        from .param import Param
        from .layout import Tabs, WidgetBox
        from .widgets import LiteralInput

        if parameters:
            linkable = parameters
        elif jslink:
            linkable = self._linkable_params + ['loading']
        else:
            linkable = list(self.param)

        params = [p for p in linkable if p not in Viewable.param]
        controls = Param(self.param, parameters=params, default_layout=WidgetBox,
                         name='Controls')
        layout_params = [p for p in linkable if p in Viewable.param]
        if 'name' not in layout_params and self._rename.get('name', False) is not None and not parameters:
            layout_params.insert(0, 'name')
        style = Param(self.param, parameters=layout_params, default_layout=WidgetBox,
                      name='Layout')
        if jslink:
            for p in params:
                widget = controls._widgets[p]
                widget.jslink(self, value=p, bidirectional=True)
                if isinstance(widget, LiteralInput):
                    widget.serializer = 'json'
            for p in layout_params:
                widget = style._widgets[p]
                widget.jslink(self, value=p, bidirectional=p != 'loading')
                if isinstance(widget, LiteralInput):
                    widget.serializer = 'json'

        if params and layout_params:
            return Tabs(controls.layout[0], style.layout[0])
        elif params:
            return controls.layout[0]
        return style.layout[0]

    def jscallback(self, args={}, **callbacks):
        """
        Allows defining a JS callback to be triggered when a property
        changes on the source object. The keyword arguments define the
        properties that trigger a callback and the JS code that gets
        executed.

        Arguments
        ----------
        args: dict
          A mapping of objects to make available to the JS callback
        **callbacks: dict
          A mapping between properties on the source model and the code
          to execute when that property changes

        Returns
        -------
        callback: Callback
          The Callback which can be used to disable the callback.
        """

        from .links import Callback
        for k, v in list(callbacks.items()):
            callbacks[k] = self._rename.get(v, v)
        return Callback(self, code=callbacks, args=args)

    def jslink(self, target, code=None, args=None, bidirectional=False, **links):
        """
        Links properties on the source object to those on the target
        object in JS code. Supports two modes, either specify a
        mapping between the source and target model properties as
        keywords or provide a dictionary of JS code snippets which
        maps from the source parameter to a JS code snippet which is
        executed when the property changes.

        Arguments
        ----------
        target: HoloViews object or bokeh Model or panel Viewable
          The target to link the value to.
        code: dict
          Custom code which will be executed when the widget value
          changes.
        bidirectional: boolean
          Whether to link source and target bi-directionally
        **links: dict
          A mapping between properties on the source model and the
          target model property to link it to.

        Returns
        -------
        link: GenericLink
          The GenericLink which can be used unlink the widget and
          the target model.
        """
        if links and code:
            raise ValueError('Either supply a set of properties to '
                             'link as keywords or a set of JS code '
                             'callbacks, not both.')
        elif not links and not code:
            raise ValueError('Declare parameters to link or a set of '
                             'callbacks, neither was defined.')
        if args is None:
            args = {}

        mapping = code or links
        for k in mapping:
            if k.startswith('event:'):
                continue
            elif hasattr(self, 'object') and isinstance(self.object, LayoutDOM):
                current = self.object
                for attr in k.split('.'):
                    if not hasattr(current, attr):
                        raise ValueError(f"Could not resolve {k} on "
                                         f"{self.object} model. Ensure "
                                         "you jslink an attribute that "
                                         "exists on the bokeh model.")
                    current = getattr(current, attr)
            elif (k not in self.param and k not in list(self._rename.values())):
                matches = difflib.get_close_matches(k, list(self.param))
                if matches:
                    matches = ' Similar parameters include: %r' % matches
                else:
                    matches = ''
                raise ValueError("Could not jslink %r parameter (or property) "
                                 "on %s object because it was not found.%s"
                                 % (k, type(self).__name__, matches))
            elif (self._source_transforms.get(k, False) is None or
                  self._rename.get(k, False) is None):
                raise ValueError("Cannot jslink %r parameter on %s object, "
                                 "the parameter requires a live Python kernel "
                                 "to have an effect." % (k, type(self).__name__))

        if isinstance(target, Syncable) and code is None:
            for k, p in mapping.items():
                if k.startswith('event:'):
                    continue
                elif p not in target.param and p not in list(target._rename.values()):
                    matches = difflib.get_close_matches(p, list(target.param))
                    if matches:
                        matches = ' Similar parameters include: %r' % matches
                    else:
                        matches = ''
                    raise ValueError("Could not jslink %r parameter (or property) "
                                     "on %s object because it was not found.%s"
                                    % (p, type(self).__name__, matches))
                elif (target._source_transforms.get(p, False) is None or
                      target._rename.get(p, False) is None):
                    raise ValueError("Cannot jslink %r parameter on %s object "
                                     "to %r parameter on %s object. It requires "
                                     "a live Python kernel to have an effect."
                                     % (k, type(self).__name__, p, type(target).__name__))

        from .links import Link
        return Link(self, target, properties=links, code=code, args=args,
                    bidirectional=bidirectional)


class SyncableData(Reactive):
    """
    A baseclass for components which sync one or more data parameters
    with the frontend via a ColumnDataSource.
    """

    selection = param.List(default=[], doc="""
        The currently selected rows in the data.""")

    # Parameters which when changed require an update of the data
    _data_params = []

    _rename = {'selection': None}

    __abstract = True

    def __init__(self, **params):
        super().__init__(**params)
        self._data = None
        self._processed = None
        self.param.watch(self._validate, self._data_params)
        if self._data_params:
            self.param.watch(self._update_cds, self._data_params)
        self.param.watch(self._update_selected, 'selection')
        self._validate(None)
        self._update_cds()

    def _validate(self, event):
        """
        Allows implementing validation for the data parameters.
        """

    def _get_data(self):
        """
        Implemented by subclasses converting data parameter(s) into
        a ColumnDataSource compatible data dictionary.

        Returns
        -------
        processed: object
          Raw data after pre-processing (e.g. after filtering)
        data: dict
          Dictionary of columns used to instantiate and update the
          ColumnDataSource
        """

    def _update_column(self, column, array):
        """
        Implemented by subclasses converting changes in columns to
        changes in the data parameter.

        Parameters
        ----------
        column: str
          The name of the column to update.
        array: numpy.ndarray
          The array data to update the column with.
        """
        data = getattr(self, self._data_params[0])
        data[column] = array

    def _update_data(self, data):
        self.param.set_param(**{self._data_params[0]: data})

    def _manual_update(self, events, model, doc, root, parent, comm):
        for event in events:
            if event.type == 'triggered' and self._updating:
                continue
            elif hasattr(self, '_update_' + event.name):
                getattr(self, '_update_' + event.name)(model)

    def _update_cds(self, *events):
        if self._updating:
            return
        self._processed, self._data = self._get_data()
        for ref, (m, _) in self._models.items():
            m.source.data = self._data
            push_on_root(ref)

    def _update_selected(self, *events, indices=None):
        if self._updating:
            return
        indices = self.selection if indices is None else indices
        for ref, (m, _) in self._models.items():
            m.source.selected.indices = indices
            push_on_root(ref)

    @updating
    def _stream(self, stream, rollover=None):
        for ref, (m, _) in self._models.items():
            m.source.stream(stream, rollover)
            push_on_root(ref)

    @updating
    def _patch(self, patch):
        for ref, (m, _) in self._models.items():
            m.source.patch(patch)
            push_on_root(ref)

    def stream(self, stream_value, rollover=None, reset_index=True):
        """
        Streams (appends) the `stream_value` provided to the existing
        value in an efficient manner.

        Arguments
        ---------
        stream_value: (Union[pd.DataFrame, pd.Series, Dict])
          The new value(s) to append to the existing value.
        rollover: int
           A maximum column size, above which data from the start of
           the column begins to be discarded. If None, then columns
           will continue to grow unbounded.
        reset_index (bool, default=True):
          If True and the stream_value is a DataFrame, then its index
          is reset. Helps to keep the index unique and named `index`.

        Raises
        ------
        ValueError: Raised if the stream_value is not a supported type.

        Examples
        --------

        Stream a Series to a DataFrame
        >>> value = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        >>> obj = DataComponent(value)
        >>> stream_value = pd.Series({"x": 4, "y": "d"})
        >>> obj.stream(stream_value)
        >>> obj.value.to_dict("list")
        {'x': [1, 2, 4], 'y': ['a', 'b', 'd']}

        Stream a Dataframe to a Dataframe
        >>> value = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        >>> obj = DataComponent(value)
        >>> stream_value = pd.DataFrame({"x": [3, 4], "y": ["c", "d"]})
        >>> obj.stream(stream_value)
        >>> obj.value.to_dict("list")
        {'x': [1, 2, 3, 4], 'y': ['a', 'b', 'c', 'd']}

        Stream a Dictionary row to a DataFrame
        >>> value = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        >>> tabulator = DataComponent(value)
        >>> stream_value = {"x": 4, "y": "d"}
        >>> obj.stream(stream_value)
        >>> obj.value.to_dict("list")
        {'x': [1, 2, 4], 'y': ['a', 'b', 'd']}

        Stream a Dictionary of Columns to a Dataframe
        >>> value = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        >>> obj = DataComponent(value)
        >>> stream_value = {"x": [3, 4], "y": ["c", "d"]}
        >>> obj.stream(stream_value)
        >>> obj.value.to_dict("list")
        {'x': [1, 2, 3, 4], 'y': ['a', 'b', 'c', 'd']}
        """
        if 'pandas' in sys.modules:
            import pandas as pd
        else:
            pd = None
        if pd and isinstance(stream_value, pd.DataFrame):
            if isinstance(self._processed, dict):
                self.stream(stream_value.to_dict(), rollover)
                return
            if reset_index:
                value_index_start = self._processed.index.max() + 1
                stream_value = stream_value.reset_index(drop=True)
                stream_value.index += value_index_start
            combined = pd.concat([self._processed, stream_value])
            if rollover is not None:
                combined = combined.iloc[-rollover:]
            with param.discard_events(self):
                self._update_data(combined)
            try:
                self._updating = True
                self.param.trigger(self._data_params[0])
            finally:
                self._updating = False
            try:
                self._updating = True
                self._stream(stream_value, rollover)
            finally:
                self._updating = False
        elif pd and isinstance(stream_value, pd.Series):
            if isinstance(self._processed, dict):
                self.stream({k: [v] for k, v in stream_value.to_dict().items()}, rollover)
                return
            value_index_start = self._processed.index.max() + 1
            self._processed.loc[value_index_start] = stream_value
            with param.discard_events(self):
                self._update_data(self._processed)
            self._updating = True
            try:
                self._stream(self._processed.iloc[-1:], rollover)
            finally:
                self._updating = False
        elif isinstance(stream_value, dict):
            if isinstance(self._processed, dict):
                if not all(col in stream_value for col in self._data):
                    raise ValueError("Stream update must append to all columns.")
                for col, array in stream_value.items():
                    combined = np.concatenate([self._data[col], array])
                    if rollover is not None:
                        combined = combined[-rollover:]
                    self._update_column(col, combined)
                self._updating = True
                try:
                    self._stream(stream_value, rollover)
                finally:
                    self._updating = False
            else:
                try:
                    stream_value = pd.DataFrame(stream_value)
                except ValueError:
                    stream_value = pd.Series(stream_value)
                self.stream(stream_value)
        else:
            raise ValueError("The stream value provided is not a DataFrame, Series or Dict!")

    def patch(self, patch_value):
        """
        Efficiently patches (updates) the existing value with the `patch_value`.

        Arguments
        ---------
        patch_value: (Union[pd.DataFrame, pd.Series, Dict])
          The value(s) to patch the existing value with.

        Raises
        ------
        ValueError: Raised if the patch_value is not a supported type.

        Examples
        --------

        Patch a DataFrame with a Dictionary row.
        >>> value = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        >>> obj = DataComponent(value)
        >>> patch_value = {"x": [(0, 3)]}
        >>> obj.patch(patch_value)
        >>> obj.value.to_dict("list")
        {'x': [3, 2], 'y': ['a', 'b']}

        Patch a Dataframe with a Dictionary of Columns.
        >>> value = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        >>> obj = DataComponent(value)
        >>> patch_value = {"x": [(slice(2), (3,4))], "y": [(1,'d')]}
        >>> obj.patch(patch_value)
        >>> obj.value.to_dict("list")
        {'x': [3, 4], 'y': ['a', 'd']}

        Patch a DataFrame with a Series. Please note the index is used in the update.
        >>> value = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        >>> obj = DataComponent(value)
        >>> patch_value = pd.Series({"index": 1, "x": 4, "y": "d"})
        >>> obj.patch(patch_value)
        >>> obj.value.to_dict("list")
        {'x': [1, 4], 'y': ['a', 'd']}

        Patch a Dataframe with a Dataframe. Please note the index is used in the update.
        >>> value = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        >>> obj = DataComponent(value)
        >>> patch_value = pd.DataFrame({"x": [3, 4], "y": ["c", "d"]})
        >>> obj.patch(patch_value)
        >>> obj.value.to_dict("list")
        {'x': [3, 4], 'y': ['c', 'd']}
        """
        if self._processed is None or isinstance(patch_value, dict):
            self._patch(patch_value)
            return

        if 'pandas' in sys.modules:
            import pandas as pd
        else:
            pd = None
        data = getattr(self, self._data_params[0])
        if pd and isinstance(patch_value, pd.DataFrame):
            patch_value_dict = {}
            for column in patch_value.columns:
                patch_value_dict[column] = []
                for index in patch_value.index:
                    patch_value_dict[column].append((index, patch_value.loc[index, column]))
            self.patch(patch_value_dict)
        elif pd and isinstance(patch_value, pd.Series):
            if "index" in patch_value:  # Series orient is row
                patch_value_dict = {
                    k: [(patch_value["index"], v)] for k, v in patch_value.items()
                }
                patch_value_dict.pop("index")
            else:  # Series orient is column
                patch_value_dict = {
                    patch_value.name: [(index, value) for index, value in patch_value.items()]
                }
            self.patch(patch_value_dict)
        elif isinstance(patch_value, dict):
            for k, v in patch_value.items():
                for index, patch  in v:
                    if pd and isinstance(self._processed, pd.DataFrame):
                        data.loc[index, k] = patch
                    else:
                        data[k][index] = patch
            self._updating = True
            try:
                self._patch(patch_value)
            finally:
                self._updating = False
        else:
            raise ValueError(
                f"Patching with a patch_value of type {type(patch_value).__name__} "
                "is not supported. Please provide a DataFrame, Series or Dict."
            )


class ReactiveData(SyncableData):
    """
    An extension of SyncableData which bi-directionally syncs a data
    parameter between frontend and backend using a ColumnDataSource.
    """

    def _update_selection(self, indices):
        self.selection = indices

    def _process_events(self, events):
        if 'data' in events:
            data = events.pop('data')
            if self._updating:
                data = {}
            _, old_data = self._get_data()
            updated = False
            for k, v in data.items():
                if k in self.indexes:
                    continue
                k = self._renamed_cols.get(k, k)
                if isinstance(v, dict):
                    v = [v for _, v in sorted(v.items(), key=lambda it: int(it[0]))]
                try:
                    isequal = (old_data[k] == np.asarray(v)).all()
                except Exception:
                    isequal = False
                if not isequal:
                    self._update_column(k, v)
                    updated = True
            if updated:
                self._updating = True
                try:
                    self.param.trigger('value')
                finally:
                    self._updating = False
        if 'indices' in events:
            self._updating = True
            try:
                self._update_selection(events.pop('indices'))
            finally:
                self._updating = False
        super(ReactiveData, self)._process_events(events)



class ReactiveHTMLMetaclass(ParameterizedMetaclass):
    """
    Parses the ReactiveHTML._template of the class and initializes
    variables, callbacks and the data model to sync the parameters and
    HTML attributes.
    """

    _name_counter = Counter()

    def __init__(mcs, name, bases, dict_):
        ParameterizedMetaclass.__init__(mcs, name, bases, dict_)
        for name, child_config in mcs._child_config.items():
            child_type = child_config.get('type', 'model')
            if name not in mcs.param:
                raise ValueError(f"Config for '{name}' does not match any parameters.")
            elif child_type not in ('model', 'template', 'literal'):
                raise ValueError(f"Config for {name} child parameter declares "
                                 f"unknown type '{child_type}'. Children must "
                                 "declare either 'model', 'template' or 'literal'"
                                 "type.")

        mcs._parser = ReactiveHTMLParser(mcs)
        mcs._parser.feed(mcs._template)
        mcs._attrs, mcs._node_callbacks = {}, {}
        mcs._inline_callbacks = []
        for node, attrs in mcs._parser.attrs.items():
            for (attr, parameters, template) in attrs:
                param_attrs = []
                for p in parameters:
                    if p in mcs.param:
                        param_attrs.append(p)
                    elif hasattr(mcs, p):
                        if node not in mcs._node_callbacks:
                            mcs._node_callbacks[node] = []
                        mcs._node_callbacks[node].append((attr, p))
                        mcs._inline_callbacks.append((node, attr, p))
                    else:
                        matches = difflib.get_close_matches(p, dir(mcs))
                        raise ValueError("HTML template references unknown "
                                         f"parameter or method '{p}', "
                                         "similar parameters and methods "
                                         f"include {matches}.")
                if node not in mcs._attrs:
                    mcs._attrs[node] = []
                mcs._attrs[node].append((attr, param_attrs, template))
        ignored = list(Reactive.param)+list(mcs._parser.children.values())
        ignored.remove('name')

        # Create model with unique name
        ReactiveHTMLMetaclass._name_counter[name] += 1
        model_name = f'{name}{ReactiveHTMLMetaclass._name_counter[name]}'
        mcs._data_model = construct_data_model(mcs, name=model_name, ignore=ignored)



class ReactiveHTML(Reactive, metaclass=ReactiveHTMLMetaclass):
    """
    ReactiveHTML provides bi-directional syncing of arbitrary HTML
    attributes and DOM properties with parameters on the subclass.

    HTML templates
    ~~~~~~~~~~~~~~

    A ReactiveHTML component is declared by providing an HTML template
    on the `_template` attribute on the class. Parameters are synced by
    inserting them as template variables of the form `${parameter}`,
    e.g.:

        <div class="${div_class}">${children}</div>

    will interpolate the div_class parameter on the class. In addition
    to providing attributes we can also provide children to an HTML
    tag. By default any parameter referenced as a child will be
    treated as a Panel components to be rendered into the containing
    HTML. This makes it possible to use ReactiveHTML to lay out other
    components.

    Children
    ~~~~~~~~

    As mentioned above parameters may be referenced as children of a
    DOM node and will, by default, be treated as Panel components to
    insert on the DOM node. However by declaring a `_child_config` we
    can control how the DOM nodes are treated. The `_child_config` is
    indexed by parameter name and the values must themselves be
    dictionaries containing `type` and `template` keys, e.g.:

        {'children': {'type': 'model', 'template': '<b></b>'}}

    The `type` key can choose whether the parameter values should be
    treated as a 'model' (i.e. a Panel component), a 'literal' which
    will be inserted as is, or a 'template'. If the children are of
    type 'model' or 'literal' a child template may be supplied, which
    will be used to wrap the contents. If the type is 'template' the
    parameter will be inserted as is and the DOM node's innerHTML will
    be synced with the child parameter.

    DOM Events
    ~~~~~~~~~~

    In certain cases it is necessary to explicitly declare event
    listeners on the DOM node to ensure that changes in their
    properties are synced when an event is fired. To make this possible
    the HTML element in question must be given a unique id, e.g.:

        <input id="input"></input>

    Now we can use this name to declare set of `_dom_events` to
    subscribe to. The following will subscribe to change DOM events
    on the input element:

       {'input': ['change']}

    Once subscribed the class may also define a method following the
    `_{node}_{event}` naming convention which will fire when the DOM
    event triggers, e.g. we could define a `_input_change` method.
    Any such callback will be given a DOMEvent object as the first and
    only argument. The DOMEvent contains information about the event
    on the .data attribute and declares the type of event on the .type
    attribute.

    Inline callbacks
    ~~~~~~~~~~~~~~~~

    Instead of declaring explicit DOM events Python callbacks can also
    be declared inline, e.g.:

        <input id="input" onchange="${_input_change}"></input>

    will look for an `_input_change` method on the ReactiveHTML
    component and call it when the event is fired.

    Scripts
    ~~~~~~~

    In addition to declaring callbacks in Python it is also possible
    to declare Javascript callbacks to execute when any synced
    attribute changes. Let us say we have declared an input element
    with a synced value parameter:

        <input id="input" value="${value}"></input>

    We can now declare a set of `_scripts`, which will fire whenever
    the value updates:

        _scripts = {
            'value': ['console.log(model, data, input)']
       }

    The Javascript is provided multiple objects in its namespace
    including:

      * data :  The data model holds the current values of the synced
                parameters, e.g. data.value will reflect the current
                value of the input node.
      * model:  The ReactiveHTML model which holds layout information
                and information about the children and events.
      * state:  An empty state dictionary which scripts can use to
                store state for the lifetime of the view.
      * <node>: All named DOM nodes in the HTML template, e.g. the
                `input` node in the example above.
    """

    _child_config = {}

    _dom_events = {}

    _template = ""

    _scripts = {}

    __abstract = True

    def __init__(self, **params):
        from .pane import panel
        for children in self._parser.children.values():
            config = self._child_config.get(children, {})
            if children not in params or config.get('type', 'model') != 'model':
                continue
            params[children] = [panel(pane) for pane in params[children]]
        super().__init__(**params)
        self._event_callbacks = defaultdict(lambda: defaultdict(list))

    def _cleanup(self, root):
        for children_param in self._parser.children.values():
            for child in getattr(self, children_param):
                child._cleanup(root)
        super()._cleanup(root)

    @property
    def _linkable_params(self):
        return [p for p in super()._linkable_params if p not in self._parser.children.values()]

    @property
    def _child_names(self):
        return {}

    def _process_children(self, children):
        return children

    def _init_params(self):
        ignored = list(Reactive.param)+list(self._parser.children.values())
        params = {
            p : getattr(self, p) for p in list(Layoutable.param)
            if getattr(self, p) is not None and p != 'name'
        }
        data_params = {}
        for k, v in self.param.get_param_values():
            if k in ignored and k != 'name':
                continue
            if isinstance(v, str):
                v = bleach.clean(v)
            data_params[k] = v
        reverse = {v: k for k, v in self._parser.children.items()}
        params['attrs'] = self._attrs
        params['callbacks'] = self._node_callbacks
        params['child_templates'] = {
            reverse.get(k): v.get('template') for k, v in self._child_config.items()
            if k in reverse
        }
        params['child_names'] = self._child_names
        params['data'] = self._data_model(**self._process_param_change(data_params))
        params['events'] = self._get_events()
        params['html'] = escape(self._get_template())
        params['nodes'] = self._parser.nodes
        params['scripts'] = {
            trigger: [escape(script) for script in scripts]
            for trigger, scripts in self._scripts.items()
        }
        return params

    def _get_events(self):
        events = {}
        for node, node_events in self._dom_events.items():
            if isinstance(node_events, list):
                events[node] = {e: True for e in node_events}
            else:
                events[node] = node_events
        for node, evs in self._event_callbacks.items():
            events[node] = node_events = events.get(node, {})
            for e in evs:
                if e not in node_events:
                    node_events[e] = False
        return events

    def _get_children(self, doc, root, model, comm, old_children=None):
        from .pane import panel
        old_children = old_children or {}
        old_models = model.children
        new_models = {parent: [] for parent in self._parser.children}

        for parent, children_param in self._parser.children.items():
            config = self._child_config.get(children_param, {})
            if config.get('type') == 'literal':
                continue
            panes = getattr(self, children_param)
            if not isinstance(panes, (list, dict)):
                panes = [panes]
            if isinstance(panes, dict):
                for key, value in panes.items():
                    panes[key] = panel(value)
            else:
                for i, pane in enumerate(panes):
                    panes[i] = panel(pane)

        for children_param, old_panes in old_children.items():
            config = self._child_config.get(children_param, {})
            if config.get('type') == 'literal':
                continue
            new_panes = getattr(self, children_param)
            if not isinstance(new_panes, (list, dict)):
                new_panes = [new_panes]
            elif isinstance(new_panes, dict):
                new_panes = new_panes.values()
            for old_pane in old_panes:
                if old_pane not in new_panes:
                    old_pane._cleanup(root)

        for parent, children_param in self._parser.children.items():
            new_panes = getattr(self, children_param)
            if not isinstance(new_panes, (list, dict)):
                new_panes = [new_panes]
            if isinstance(new_panes, dict):
                new_panes = (new_panes.values())
            config = self._child_config.get(children_param, {})
            if config.get('type') == 'literal':
                new_models[parent] = new_panes
            elif children_param in old_children:
                # Find existing models
                old_panes = old_children[children_param]
                for i, pane in enumerate(new_panes):
                    if pane in old_panes:
                        child, _ = pane._models[root.ref['id']]
                    else:
                        child = pane._get_model(doc, root, model, comm)
                    new_models[parent].append(child)
            elif parent in old_models:
                # Children parameter unchanged
                new_models[parent] = old_models[parent]
            else:
                new_models[parent] = [
                    pane._get_model(doc, root, model, comm)
                    for pane in new_panes
                ]
        return self._process_children(new_models)

    def _get_template(self):
        html = self._template
        for name in list(self._parser.nodes):
            html = (
                html
                .replace(f"id='{name}'", f"id='{name}-${{id}}'")
                .replace(f'id="{name}"', f'id="{name}-${{id}}"')
            )
        for parent, child_name in self._parser.children.items():
            html = html.replace('${%s}' % child_name, '')
        return html

    def _get_model(self, doc, root=None, parent=None, comm=None):
        properties = self._process_param_change(self._init_params())
        model = _BkReactiveHTML(**properties)
        if not root:
            root = model
        model.children = self._get_children(doc, root, model, comm)
        model.on_event('dom_event', self._process_event)

        linked_properties = [p for pss in self._attrs.values() for _, ps, _ in pss for p in ps]
        self._link_props(model.data, linked_properties, doc, root, comm)

        self._models[root.ref['id']] = (model, parent)
        return model

    def _process_event(self, event):
        cb = getattr(self, f"_{event.node}_{event.data['type']}", None)
        if cb is not None:
            cb(event)
        event_type = event.data['type']
        star_cbs = self._event_callbacks.get('*', {})
        node_cbs = self._event_callbacks.get(event.node, {})
        inline_cbs = {attr: [getattr(self, p)] for node, attr, p in self._inline_callbacks
                      if node == event.node}
        event_cbs = (
            node_cbs.get(event_type, []) + node_cbs.get('*', []) +
            star_cbs.get(event_type, []) + star_cbs.get('*', []) +
            inline_cbs.get(event_type, [])
        )
        for cb in event_cbs:
            cb(event)

    def _set_on_model(self, msg, root, model):
        if not msg:
            return
        self._changing[root.ref['id']] = [
            attr for attr, value in msg.items()
            if not model.lookup(attr).property.matches(getattr(model, attr), value)
        ]
        try:
            model.update(**msg)
        finally:
            del self._changing[root.ref['id']]

    def _update_model(self, events, msg, root, model, doc, comm):
        child_params = self._parser.children.values()
        new_children, model_msg, data_msg  = {}, {}, {}
        for prop, v in list(msg.items()):
            if prop in child_params:
                new_children[prop] = prop
            elif prop in Reactive.param:
                model_msg[prop] = prop
            elif isinstance(v, str):
                data_msg[prop] = bleach.clean(v)
            else:
                data_msg[prop] = v
        if new_children:
            old_children = {key: events[key].old for key in new_children}
            model_msg['child_names'] = self._child_names
            model_msg['children'] = self._get_children(doc, root, model, comm, old_children)
        self._set_on_model(data_msg, root, model.data)
        self._set_on_model(model_msg, root, model)

    def on_event(self, node, event, callback):
        """
        Registers a callback to be executed when the specified DOM
        event is triggered on the named node. Note that the named node
        must be declared in the HTML. To create a named node you must
        give it an id of the form `id="name"`, where `name` will
        be the node identifier.

        Arguments
        ---------
        node: str
          Named node in the HTML identifiable via id of the form `id="name"`.
        event: str
          Name of the DOM event to add an event listener to.
        callback: callable
          A callable which will be given the DOMEvent object.
        """
        if node not in self._parser.nodes and node != '*':
            raise ValueError(f"Named node '{node}' not found. Available "
                             f"nodes include: {self._parser.nodes}.")
        self._event_callbacks[node][event].append(callback)
        events = self._get_events()
        for ref, (m, _) in self._models.items():
            m.events = events
            push_on_root(ref)
