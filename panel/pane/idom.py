import sys
import shutil
import asyncio

from functools import partial
from threading import Thread
from queue import Queue as SyncQueue
from packaging.version import Version

from ..io.notebook import push_on_root
from ..io.resources import DIST_DIR, LOCAL_DIST
from ..io.state import state
from ..models import IDOM as _BkIDOM
from .base import PaneBase


_IDOM_MIN_VER = "0.35"
_IDOM_MAX_VER = "0.36"


try:
    # all IDOM imports belong here so we can gracefully respond if it's not installed
    import idom
    from idom.config import IDOM_WED_MODULES_DIR
    from idom.core.component import ComponentType
    from idom.core.layout import Layout, LayoutUpdate, LayoutEvent
    from idom.core.dispatcher import VdomJsonPatch
except ImportError:
    # we check these imports succeeded in IDOM.__init__
    pass


def _spawn_threaded_event_loop(coro):
    loop_q = SyncQueue()

    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_q.put(loop)
        loop.run_until_complete(coro)

    thread = Thread(target=run_in_thread, daemon=True)
    thread.start()

    return thread, loop_q.get()


class IDOM(PaneBase):

    priority = None

    _updates = True

    _unpack = True

    _bokeh_model = _BkIDOM

    def __init__(self, object=None, **params):
        try:
            idom
        except NameError:
            raise RuntimeError(
                "'idom' is not installed - run "
                """'pip install "idom>={_IDOM_MIN_VER},<{_IDOM_MAX_VER}"' """
                "and try again"
            )

        if Version(_IDOM_MIN_VER) > Version(idom.__version__) >= Version(_IDOM_MAX_VER):
            raise RuntimeError(
                f"Expected idom>={_IDOM_MIN_VER},<{_IDOM_MAX_VER}, but found {idom.__version__}"
            )

        new_web_modules_dir = DIST_DIR / "idom"
        if new_web_modules_dir.exists():
            shutil.rmtree(str(new_web_modules_dir))
        shutil.copytree(str(IDOM_WED_MODULES_DIR.current), str(new_web_modules_dir))
        IDOM_WED_MODULES_DIR.current = new_web_modules_dir

        super().__init__(object, **params)
        self._idom_thread: Thread = None
        self._idom_loop: asyncio.AbstractEventLoop = None
        self._idom_model = {}
        self.param.watch(self._update_layout, 'object')

    def _update_layout(self, *args):
        self._idom_model = {}
        if self._idom_loop is None:
            return
        self._setup()

    def _setup(self):
        if self.object is None:
            return
        if isinstance(self.object, Layout):
            self._idom_layout = self.object
        elif isinstance(self.object, ComponentType):
            self._idom_layout = Layout(self.object)
        else:
            self._idom_layout = Layout(self.object())
        self._idom_thread, self._idom_loop = _spawn_threaded_event_loop(
            self._idom_layout_render_loop()
        )

    def _get_model(self, doc, root=None, parent=None, comm=None):
        if comm:
            url = '/panel_dist/idom'
        else:
            url = '/'+LOCAL_DIST+'idom'

        if self._idom_loop is None:
            self._setup()

        update = VdomJsonPatch.create_from(LayoutUpdate("", {}, self._idom_model))
        props = self._init_params()
        model = self._bokeh_model(
            event=[update.path, update.changes], importSourceUrl=url, **props
        )
        if root is None:
            root = model
        self._link_props(model, ['msg'], doc, root, comm)

        if root is None:
            root = model
        self._models[root.ref['id']] = (model, parent)
        return model

    def _cleanup(self, root):
        super()._cleanup(root)
        if not self._models:
            try:
                # try to gracefully wait for tasks to complete
                for task in asyncio.all_tasks(self._idom_loop):
                    task.cancel()
                self._idom_thread.join(3)

                if self._idom_loop.is_closed():
                    # if tasks failed to cancel, forcefully close the loop
                    self._idom_loop.call_soon_threadsafe(
                        lambda *funcs: [f() for f in funcs],
                        self._idom_loop.stop,
                        self._idom_loop.close,
                    )

                # check one more time to see if the thread closed
                self._idom_thread.join(3)
                if self._idom_thread.is_alive():
                    # something unexpected is keeping the thread alive
                    raise RuntimeError("Failed to stop thread.")
            finally:
                self._idom_thread = None
                self._idom_loop = None
                self._idom_layout = None

    def _process_property_change(self, msg):
        if msg['msg'] is None:
            return {}
        dispatch = self._idom_layout.dispatch(LayoutEvent(**msg['msg']))
        asyncio.run_coroutine_threadsafe(dispatch, loop=self._idom_loop)
        for ref, (m, _) in self._models.items():
            m.msg = None
            push_on_root(ref)
        return {}

    async def _idom_layout_render_loop(self):
        with self._idom_layout:
            while True:
                update = VdomJsonPatch.create_from(await self._idom_layout.render())
                self._idom_model = update.apply_to(self._idom_model)
                for ref, (model, _) in self._models.items():
                    doc = state._views[ref][2]
                    if doc.session_context:
                        doc.add_next_tick_callback(partial(model.update, event=update))
                    else:
                        model.event = update
                        push_on_root(ref)

    @classmethod
    def applies(self, object):
        if 'idom' in sys.modules:
            if isinstance(object, (ComponentType, Layout)):
                return 0.8
            elif callable(object):
                return None
        return False

    @classmethod
    def use_param(cls, parameter):
        """
        Links parameter to some IDOM state value and returns the linked
        value.

        Arguments
        ---------
        parameter: param.Parameter
          The parameter to link to a idom state value.

        Returns
        -------
        An idom state value which is updated when the parameter changes.
        """
        import idom
        from ..depends import param_value_if_widget
        parameter = param_value_if_widget(parameter)
        initial = getattr(parameter.owner, parameter.name)
        value, set_value = idom.hooks.use_state(initial)
        def update(event):
            set_value(event.new)
        parameter.owner.param.watch(update, parameter.name)
        return value
