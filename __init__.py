import logging
import json

from flatland import Form, String
from logging_helpers import _L
from microdrop.app_context import get_app
from microdrop.plugin_helpers import StepOptionsController, get_plugin_info
from microdrop.plugin_manager import (PluginGlobals, Plugin, IPlugin,
                                      implements, emit_signal)
from path_helpers import path
from noconflict import classmaker
from pygtkhelpers.gthreads import gtk_threadsafe
import pygtkhelpers as pg
import pygtkhelpers.schema
import pygtkhelpers.ui.form_view_dialog
import pygtkhelpers.utils
import gobject
import gtk

from ._version import get_versions

__version__ = get_versions()['version']
del get_versions

logger = logging.getLogger(__name__)

PluginGlobals.push_env('microdrop.managed')


class UserPromptPlugin(Plugin, gobject.GObject, StepOptionsController):
    """
    This class is automatically registered with the PluginManager.

    .. versionchanged:: 2.2.1
        Use :data:`__version__` for plugin version.
    """
    implements(IPlugin)
    # Without the follow line, cannot inherit from both `Plugin` and
    # `gobject.GObject`.  See [here][1] for more details.
    #
    # [1]: http://code.activestate.com/recipes/204197-solving-the-metaclass-conflict/
    __metaclass__ = classmaker()
    pg.utils.gsignal('step-prompt-accepted', object)
    version = __version__
    plugin_name = get_plugin_info(path(__file__).parent).plugin_name

    StepFields = Form.of(
        String.named('message').using(optional=True, default=''),
        String.named('schema').using(optional=True, default='',
                                     properties={'show_in_gui': False}))

    def __init__(self):
        gobject.GObject.__init__(self)
        super(UserPromptPlugin, self).__init__()
        self.name = self.plugin_name
        self.timeout_id = None
        self.start_time = None
        self.step_options_menu = None
        self.connect('step-prompt-accepted', lambda obj, values:
                     _L().info('Step prompt accepted (`%s`)', values))

    @gtk_threadsafe
    def create_ui(self):
        '''
        .. versionchanged:: 2.1.2
            Wrap with :func:`gtk_threadsafe` decorator to ensure the code runs
            in the main GTK thread.

        .. versionchanged:: 2.3.2
            Move ``Set step prompt...`` menu item directly under main ``Tools``
            menu.
        '''
        self.step_options_menu = gtk.MenuItem('_Set step prompt...')
        self.step_options_menu.props.use_underline = True
        self.step_options_menu.set_tooltip_text(self.name)
        self.step_options_menu.connect('activate',
                                       self.on_step_options_menu__activate)
        self.step_options_menu.show()
        app = get_app()
        app.main_window_controller.menu_tools.append(self.step_options_menu)

    @gtk_threadsafe
    def destroy_ui(self):
        '''
        .. versionchanged:: 2.1.2
            Wrap with :func:`gtk_threadsafe` decorator to ensure the code runs
            in the main GTK thread.
        '''
        app = get_app()
        app.main_window_controller.menu_tools.remove(self.step_options_menu)

    ###########################################################################
    # Callback methods
    def on_plugin_disable(self):
        self.destroy_ui()

    def on_plugin_enable(self):
        self.create_ui()

    @gtk_threadsafe
    def on_step_options_menu__activate(self, widget):
        '''
        .. versionchanged:: 2.1.2
            Wrap with :func:`gtk_threadsafe` decorator to ensure the code runs
            in the main GTK thread.

        .. versionchanged:: 2.3.2
            Validate schema before saving step options.
        '''
        step_options_dialog = (pg.ui.form_view_dialog
                               .FormViewDialog(self.StepFields))
        ok, values = step_options_dialog.run(values=self.get_step_options())
        if ok:
            if values['schema'].strip():
                try:
                    schema = json.loads(values['schema'])
                    pg.schema.get_fields_frame(schema)
                except Exception as exception:
                    _L().error('Invalid schema `%s`: `%s`', values['schema'],
                               exception, exc_info=True)
                    values['schema'] = ''
            self.set_step_values(values)

    @gtk_threadsafe
    def on_step_run(self):
        '''
        Handler called whenever a step is executed. Note that this signal
        is only emitted in realtime mode or if a protocol is running.

        Plugins that handle this signal must emit the on_step_complete
        signal once they have completed the step. The protocol controller
        will wait until all plugins have completed the current step before
        proceeding.

        return_value can be one of:
            None
            'Repeat' - repeat the step
            or 'Fail' - unrecoverable error (stop the protocol)

        .. versionchanged:: 2.1.2
            Wrap with :func:`gtk_threadsafe` decorator to ensure the code runs
            in the main GTK thread.
        '''
        app = get_app()
        logger.info('[UserPromptPlugin] on_step_run(): step #%d',
                    app.protocol.current_step_number)
        step_options = self.get_step_options()
        if not step_options['message'] and not step_options['schema']:
            # No message or schema.  Nothing to do.
            emit_signal('on_step_complete', [self.name, None])
        else:
            title = 'Step {}'.format(app.protocol.current_step_number + 1)
            try:
                parent_window = app.main_window_controller.view
                if not step_options['schema']:
                    # No schema specified.  Display simple message box.
                    dialog = gtk.Dialog(title=title,
                                        parent=parent_window,
                                        flags=(gtk.DIALOG_MODAL |
                                               gtk.DIALOG_DESTROY_WITH_PARENT),
                                        buttons=(gtk.STOCK_OK,
                                                 gtk.RESPONSE_ACCEPT,
                                                 gtk.STOCK_CANCEL,
                                                 gtk.RESPONSE_REJECT))
                    # Add specified message to dialog content area.
                    message_label = gtk.Label(step_options['message'])
                    dialog.get_content_area().pack_start(message_label)
                    message_label.show()
                    # Increase default dialog size.
                    dialog.set_size_request(250, 100)

                    response_code = dialog.run()
                    dialog.destroy()
                    if response_code != gtk.RESPONSE_ACCEPT:
                        raise ValueError('Protocol stop requested.')
                    else:
                        # No schema was specified.  No values entered by user.
                        values = {}
                else:
                    if step_options['message']:
                        title = '[{}] {}'.format(title,
                                                 step_options['message'])
                    schema = json.loads(step_options['schema'])
                    # Prompt user to enter values according to schema.
                    values = pg.schema.schema_dialog(schema, title=title,
                                                     parent=parent_window)
                # Emit notification that step prompt was accepted.
                self.emit('step-prompt-accepted', values)

                # Signal that step processing has completed successfully.
                emit_signal('on_step_complete', [self.name, None])
            except ValueError:
                logger.warning('Protocol stopped.')
                # An error occurred while initializing Analyst remote control.
                emit_signal('on_step_complete', [self.name, 'Fail'])
            except Exception:
                logger.error('Protocol stopped.', exc_info=True)
                # An error occurred while initializing Analyst remote control.
                emit_signal('on_step_complete', [self.name, 'Fail'])


PluginGlobals.pop_env()
