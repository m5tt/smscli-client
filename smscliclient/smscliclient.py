#/usr/bin/python

from builtins import AssertionError, str

import json
import os
import re
import struct
import signal
import socket
import datetime
import threading
import collections
import configparser
import time

import gi
gi.require_version('Notify', '0.7')
from gi.repository import Notify
import urwid

MAX_MESSAGE_LEN = 300

class ViewMessage(urwid.Padding):
    """ Represents a single message in a view """ 

    TYPE_LOG = 0
    TYPE_OUTGOING = 'OUTBOX'
    TYPE_INCOMING = 'INBOX'

    TIME_FORMAT_STR = '%H:%M:%S'
    USER_DISPLAY_NAME = 'Me'

    # attributes for theming
    TYPE_LOG_ATTR = 'log'
    TYPE_OUTGOING_ATTR = 'outgoing'
    TYPE_INCOMING_ATTR = 'incoming'

    TIME_ATTR = 'message_time'
    BODY_ATTR = 'body'

    def __init__(self, message_time, body, related_view_id, sender_name, message_type):
        self.message_time = message_time        # expects properly formatted time
        self.body = body
        self.related_view_id = related_view_id
        self.sender_name = sender_name
        self.message_type = message_type

        self.alignment = MainWindow.MESSAGE_ALIGNMENT
        self.width_type = MainWindow.MESSAGE_WIDTH_TYPE
        self.width_size = MainWindow.MESSAGE_WIDTH_PERCENT

        if self.message_type == ViewMessage.TYPE_LOG:
            self.sender_attr = ViewMessage.TYPE_LOG_ATTR
        elif self.message_type == ViewMessage.TYPE_OUTGOING:
            self.sender_attr = ViewMessage.TYPE_OUTGOING_ATTR
        elif self.message_type == ViewMessage.TYPE_INCOMING:
            self.sender_attr = ViewMessage.TYPE_INCOMING_ATTR

        super().__init__(urwid.Text([
                (ViewMessage.TIME_ATTR, self.message_time + ' - '),
                (self.sender_attr, self.sender_name + ': '),
                (ViewMessage.BODY_ATTR, self.body)
            ]),
            align=self.alignment,
            width=(self.width_type, self.width_size)
        )


class View:
    """
        Represents a single View.

        Encapsulates both the ListBox and the ListWalker,
        so we can easily switch views and add to them

        content is a list of ViewMessages
    """

    def __init__(self, view_id, view_name, content):
        self.view_id = view_id
        self.view_name = view_name
        self.listwalker = urwid.SimpleFocusListWalker(content)
        self.listbox = urwid.ListBox(self.listwalker)

    def scroll_to_bottom(self):
        self.listbox.set_focus(len(self.listwalker) - 1)

    def add_message(self, view_message):
        self.listwalker.append(view_message)
        self.scroll_to_bottom()

    def add_messages(self, view_messages):
        self.listwalker += view_messages
        self.scroll_to_bottom()


class LogView(View):
    """
        The main central view, that smscli logs output too
    """

    VIEW_ID = '0'
    VIEW_NAME = 'smscli'

    def __init__(self, content):
        super().__init__(self.VIEW_ID, self.VIEW_NAME, content)

    def print_message(self, message):
        self.add_message(ViewMessage(
            datetime.datetime.now().time().strftime(ViewMessage.TIME_FORMAT_STR),
            message,
            LogView.VIEW_NAME,
            LogView.VIEW_NAME,
            ViewMessage.TYPE_LOG
        ))


class ContactView(View):
    """
        The view for the conversation with a
        contact

        Holds contact data along with urwid widgets
        to represent it as a view
    """

    def __init__(self, view_id, name, address, content):
        self.address = address
        self.display_name = name
        
        super().__init__(view_id, name, content)


class MainWindow(urwid.Frame):
    """
        Represents the main window that holds
        the different views

        Manages setting up whole interface,
        adding new views, switching between them...

        key events will be handled externally
    """

    MESSAGE_ALIGNMENT = 'left'
    MESSAGE_WIDTH_TYPE = 'relative'
    MESSAGE_WIDTH_PERCENT = 80        # how much of the screen a message takes up before wrapping

    TITLE_BAR_ATTR = 'titlebar'
    TITLE_BAR_TEXT = 'smscli'
    DIVIDER_ATTR = 'divider'
    FOCUS_ATTR = 'footer'

    EDIT_CAPTION = '> '
    MAX_VIEWS = 6                    # TODO: deal with this properly
    ERROR_MAX_VIEWS = 'Cant open anymore views'

    def __init__(self, init_view):
        self.init_views(init_view)

        self.title_bar = urwid.AttrMap(urwid.Text(MainWindow.TITLE_BAR_TEXT), MainWindow.TITLE_BAR_ATTR)
        self.divider = urwid.AttrMap(urwid.Text(''), MainWindow.DIVIDER_ATTR)
        self.refresh_divider()

        self.input_line = urwid.Edit(MainWindow.EDIT_CAPTION)

        inner_frame = urwid.Frame(
                list(self.shown_views.items())[0][1].listbox,
                header=self.title_bar,
                footer=self.divider
        )

        super().__init__(inner_frame, footer=self.input_line)

        self.set_focus(MainWindow.FOCUS_ATTR)

    def init_views(self, init_view):
        """
            initialise the views
            make this a method so we can call it when connecting to reset
        """
        self.shown_views = collections.OrderedDict({init_view.view_id: init_view})    # subset of views being shown
        self.max_views = MainWindow.MAX_VIEWS
        self.current_view = init_view.view_id

    def add_new_view(self, view):
        if len(self.shown_views) <= self.max_views:
            self.shown_views[view.view_id] = view

            self.refresh_divider()
        else:
            state.log_view.print_message(MainWindow.ERROR_MAX_VIEWS)

    def switch_view(self, view_id):
        """
            change views, as in switch the current listbox in the frame with a different one
            contents['body'][0] -> inner frame
            contents['body'][0].contents['body'] -> (listbox, attr)
        """

        self.contents['body'][0].contents['body'] = (self.shown_views[view_id].listbox, None)
        self.current_view = view_id
        self.refresh_divider()

    def close_view(self, view_id):

        if not list(self.shown_views.values())[0] == self.shown_views[view_id]:  # can never close first view (log view)
            # shift current view down, delete old current view then switch
            keys = list(self.shown_views.keys())
            new_shown = keys[keys.index(view_id) - 1]

            del(self.shown_views[view_id])
            self.switch_view(new_shown)

    def get_input(self):
        return self.input_line.get_edit_text()

    def clear_input(self):
        self.input_line.set_edit_text('')

    def refresh_divider(self):
        """ change divider text """

        self.divider.original_widget.set_text(self.gen_divider_text())

    def gen_divider_text(self):
        """
            generate divider text using current show views
            looks like: '[connected]    [0:contact_name1] [1:contact_name2]'
        """

        if state.connection_handler.connected:
            divider_text = '[{status}]'.format(status=ConnectionHandler.STATUS_CONNECTED)
        else:
            divider_text = '[{status}]'.format(status=ConnectionHandler.STATUS_DISCONNECTED)

        # do it this way so we can get indices
        for i, keypair in enumerate(self.shown_views.items()):
            if keypair[0] == self.current_view:
                divider_text += ' -' + str(i) + ':' + keypair[1].view_name + '-'
            else:
                divider_text += ' [' + str(i) + ':' + keypair[1].view_name + ']'

        return divider_text


class ConnectionHandler:
    """
        Handles all connection with the server

        protocol with server:
            communicate in messages
            messages are JSON strings

            messages can be sent either to or from
            server/client at any message_time

            initial data: large contact list with sms conversations
                          encapsulated

            messages after: sms messages belonging to a 
                            conversation

            on connection: client reads initial data
            write: send message length in bytes - size 4 bytes
                   send data of size s
            read:  read 4 bytes to get length len
                   read len bytes
    """

    LEN_BYTE_SIZE = 4               # byte size of message length
    LEN_STRUCT_FORMAT = '! i'       # format chars for struct holding message length
    LEN_STRUCT_INT_TYPE = 'big'
    TIMEOUT = 15

    MIN_PORT = 1
    MAX_PORT = 65535

    WRITE_PAUSE_TIME = 0.2

    ERROR_MESSAGE_TIMEOUT = 'Connection timed out'
    ERROR_MESSAGE_REFUSED = 'Connection was refused'
    ERROR_MESSAGE_INVALID = 'Invalid command argument'
    ERROR_MESSAGE_GENERIC = 'Connection failed'
    ERROR_LOST_CONNECTION = 'Lost connection'

    MESSAGE_CONNECTING = 'Connecting to {ip}...'
    MESSAGE_ONCONNECT = 'Connected to {ip} on {port}'

    STATUS_CONNECTED = 'connected'
    STATUS_DISCONNECTED = 'disconnected'

    def __init__(self):
        self.connected = False

    def setup_connection(self, ip_address, port):
        """
            connects to server, reads initial data
            starts up read loop thread
        """

        self.connect(ip_address, port)

        if self.connected:
            state.log_view.print_message(ConnectionHandler.MESSAGE_CONNECTING.format(ip=self.ip_address))
            state.main_loop.draw_screen()     # gets blocked by connection stuff otherwise

            initial_data = self.read_server()
            JSONHelper.setup_contact_views(initial_data)        # TODO: have this return

            self.read_looper = threading.Thread(target=self.read_loop)
            self.read_looper.start()

            # ensure all views except log are closed so we don't have out of date views on reconnects
            state.main_window.init_views(state.log_view)
            state.main_window.refresh_divider()

            state.log_view.print_message(ConnectionHandler.MESSAGE_ONCONNECT.format(ip=self.ip_address, port=self.port))

    def connect(self, ip_address, port):
        if ConnectionHandler.is_valid_ipv4_address(ip_address) and ConnectionHandler.is_valid_port(port):
            self.ip_address = ip_address
            self.port = port

            try:

                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(ConnectionHandler.TIMEOUT)

                self.socket.connect((ip_address, int(port)))
                self.connected = True

                self.socket.settimeout(socket.getdefaulttimeout())
            except socket.error as e:
                if e.errno == socket.errno.ETIMEDOUT:
                    error_message = ConnectionHandler.ERROR_MESSAGE_TIMEOUT
                elif e.errno == socket.errno.ECONNREFUSED:
                    error_message = ConnectionHandler.ERROR_MESSAGE_REFUSED
                else:
                    # error_message = ConnectionHandler.ERROR_MESSAGE_GENERIC
                    # error_message = str(e)
                    raise

                state.log_view.print_message(error_message)
                self.connected = False
        else:
            state.log_view.print_message(ConnectionHandler.ERROR_MESSAGE_INVALID)
            self.connected = False

    def read_server(self):
        """ reads a json string from the server """
        
        message = ''

        try:
            length = int.from_bytes(
                self.socket.recv(self.LEN_BYTE_SIZE, socket.MSG_WAITALL),
                ConnectionHandler.LEN_STRUCT_INT_TYPE
            )

            message = str(self.socket.recv(length, socket.MSG_WAITALL), 'utf-8')

            # clean disconnect will not raise socket.error but will return empty message
            if not message:
                raise socket.error
        except socket.error:
            state.log_view.print_message(ConnectionHandler.ERROR_LOST_CONNECTION)
            self.connected = False

        return message

    def write_server(self, message):
        """
            write a json string to the server

            we use the struct module to correctly send
           the integer size, using network byte order
        """

        try: 
            self.socket.sendall(struct.pack(ConnectionHandler.LEN_STRUCT_FORMAT, len(message)))
            self.socket.sendall(message.encode('utf-8'))
        except socket.error:
            state.log_view.print_message(ConnectionHandler.ERROR_LOST_CONNECTION)
            self.connected = False

    def read_loop(self):
        """ waits for messages from server """

        while self.connected:
            json_message = self.read_server()
            if json_message:
                self.receive_message(json_message)

        # if this is a shutdown, urwid raises an assertion error when we do this
        try:
            state.main_window.refresh_divider()
            state.main_loop.draw_screen()
        except AssertionError:
            pass

    def receive_message(self, message):

        view_message = JSONHelper.json_to_view_message(message)

        # make new contact if contact not known
        if view_message.related_view_id not in state.contact_views:
            state.contact_views[view_message.related_view_id] = ContactView(
                view_message.related_view_id,
                view_message.related_view_id,
                view_message.related_view_id,
                []
            )

        contact_view = state.contact_views[view_message.related_view_id]
        contact_view.add_message(view_message)

        if view_message.related_view_id not in state.main_window.shown_views:
            state.main_window.add_new_view(contact_view)

        if view_message.message_type == ViewMessage.TYPE_INCOMING:
            ConnectionHandler.notify(contact_view.display_name, view_message.body)

        # were in another thread so explicitly tell urwid to render
        state.main_loop.draw_screen()

    def send_message(self, message):
        """
            create a ViewMessage given message  body and current view then send and add to view
        """

        if len(message) > MAX_MESSAGE_LEN:
            # break message into MAX_MESSAGE_LEN chunks
            message_chunk = [message[i:i+MAX_MESSAGE_LEN] for i in range(0, len(message), MAX_MESSAGE_LEN)]
        else:
            message_chunk = [message]

        # convert each message string chunk into a view message
        view_message_chunk = [ViewMessage(datetime.datetime.now().time().strftime(ViewMessage.TIME_FORMAT_STR),
                                          message, state.main_window.current_view, ViewMessage.USER_DISPLAY_NAME,
                                          ViewMessage.TYPE_OUTGOING)
                              for message in message_chunk]

        # write each chunk to server
        for view_message in view_message_chunk:
            self.write_server(JSONHelper.view_message_to_json(view_message))
            time.sleep(ConnectionHandler.WRITE_PAUSE_TIME)

        # finally add them to the current view
        state.main_window.shown_views[state.main_window.current_view].add_messages(view_message_chunk)
        state.main_window.clear_input()

    @staticmethod
    def notify(title, body):
        # TODO: make this optional
        if Notify.init(title):
            notification = Notify.Notification.new(title, body)
            notification.show()

    @staticmethod
    def is_valid_ipv4_address(ip_address):
        try:
            socket.inet_aton(ip_address)
        except socket.error:
            return False

        # though this is not technically needed for a valid ip we will impose it anyway
        return ip_address.count('.') == 3

    @staticmethod
    def is_valid_port(port):
        try:
            port = int(port)

            if ConnectionHandler.MIN_PORT <= port <= ConnectionHandler.MAX_PORT:
                return True
            else:
                return False
        except ValueError:
            return False


class CommandHandler:
    """
        Parses and handles commands
    """
    COMMAND_PREFIX = '/'        # all commands start with /
    COMMAND_METHOD_PREFIX = 'do_'
    HELP_MESSAGE_PREFIX = 'help_'
    DEFAULT_HELP_MESSAGE = 'Usage: /<command> <args>'

    # help messages
    HELP_CONNECT = 'Usage: /connect <ip> <port>'
    HELP_MSG = 'Usage: /msg <contact_name/phone_number>'
    HELP_DISCONNECT = 'Usage: /disconnect'
    HELP_LIST = 'Usage: /list'

    # command specific constants

    # connect command
    CONNECT_COMMAND_NAME = 'connect'
    CONNECT_CONNECTION_EXIST = 'Already connected'

    # msg command
    MSG_COMMAND_NAME = 'msg'
    MSG_DISCONNECTED = 'Not connected'
    MSG_INVALID_CONTACT = 'Invalid phone number or contact doesnt exist'

    # list command
    LIST_COMMAND_LIST_TITLE = 'Commands:'
    LIST_COMMAND_LIST_INDENT = 2

    def parse_command(self, command):
        # break up command
        try:
            command_parts = command[1:].split()     # grab everything after the slash and split on space
            command_name, command_args = command_parts[0], command_parts[1:]

            # get method associated to command
            command_method_str = CommandHandler.COMMAND_METHOD_PREFIX + command_name
            command_method = getattr(self, command_method_str)
        except IndexError:
            # command was to short, something like /
            self.do_help([])
            return False
        except AttributeError:
            # command does not exist
            self.do_help([])
            return False
        else:
            # now call it, do this here so we don't catch other attr errors inside command_method
            command_method(command_args)

        return True

    def do_connect(self, args):
        """
            /connect <ip> <port>
            connects to a smscli-server on the given ip and port
        """

        if not state.connection_handler.connected:
            if len(args) > 0:
                if len(args) == 1:
                    conn_set = state.config_handler.get_alias(args[0])
                else:
                    conn_set = args

                if conn_set is not None and len(conn_set) == 2:
                    state.connection_handler.setup_connection(conn_set[0], conn_set[1])
                else:
                    self.do_help([CommandHandler.CONNECT_COMMAND_NAME])
            else:
                self.do_help([CommandHandler.CONNECT_COMMAND_NAME])
        else:
            state.log_view.print_message(CommandHandler.CONNECT_CONNECTION_EXIST)

    def do_msg(self, args):
        """
            /msg <contact_name/phone_number>
            opens a new contact view if contact doesnt exist, will create one using given phone number

            invalid phone numbers and such are left for the server to deal with
        """
        num_args = 1

        if state.connection_handler.connected:
            if len(args) == num_args:
                name = args[0]
                matched_views = [view for view in state.contact_views.values() if view.display_name.lower() == name.lower()]

                if len(matched_views):
                    for view in matched_views:      # could be multiple contacts with same name -just open all
                        if view.view_id not in state.main_window.shown_views:
                            state.main_window.add_new_view(view)
                else:
                    # unmatched contact, assume name is a phone number, first ensure it has no letters
                    if re.search('[a-zA-Z]', name) is None:
                        state.contact_views[name] = ContactView(
                            name,
                            name,
                            name,
                            []
                        )

                        # strip space and special characters
                        name.strip()
                        re.sub('[^0-9]', '', name)

                        contact_view = state.contact_views[name]
                        state.main_window.add_new_view(contact_view)
                        state.main_window.switch_view(contact_view.view_id)
                    else:
                        state.log_view.print_message(CommandHandler.MSG_INVALID_CONTACT)
            else:
                self.do_help([CommandHandler.MSG_COMMAND_NAME])
        else:
            state.log_view.print_message(CommandHandler.MSG_DISCONNECTED)

    def do_disconnect(self, args):
        if state.connection_handler.connected:
            state.connection_handler.connected = False
            state.connection_handler.socket.shutdown(socket.SHUT_RDWR)
            state.connection_handler.socket.close()

    def do_quit(self, args):
        exit()      # TODO: bugged out for some reason

    def do_list(self, args):
        state.log_view.print_message(CommandHandler.LIST_COMMAND_LIST_TITLE)
        for command in CommandHandler.get_commands():
            state.log_view.print_message(' ' * CommandHandler.LIST_COMMAND_LIST_INDENT + command)

    def do_help(self, args):
        if len(args) == 0:
            state.log_view.print_message(CommandHandler.DEFAULT_HELP_MESSAGE)
        else:
            help_message = (CommandHandler.HELP_MESSAGE_PREFIX + args[0]).upper()
            state.log_view.print_message(getattr(CommandHandler, help_message))

    @staticmethod
    def get_commands():
        pattern = r'^' + re.escape(CommandHandler.COMMAND_METHOD_PREFIX) + r'.*'
        return [method.split('_')[1]
                for method in dir(CommandHandler)
                if callable(getattr(CommandHandler, method)) and re.search(pattern, method, re.IGNORECASE)]


class JSONHelper:
    """ Util methods for converting between JSON strings and objects used here """

    REMOTE_TIME_FORMAT_STR = '%I:%M:%S %p'

    JSON_MESSAGE_TIME_KEY = 'time'
    JSON_MESSAGE_BODY_KEY = 'body'
    JSON_MESSAGE_ID_KEY = 'relatedContactId'
    JSON_MESSAGE_TYPE_KEY = 'smsMessageType'

    JSON_CONTACT_ID_KEY = 'id'
    JSON_CONTACT_DISPLAY_KEY = 'displayName'
    JSON_CONTACT_PHONE_KEY = 'phoneNumber'

    @staticmethod
    def format_time(remote_time):
        """ java client formats time all weird out, fix it here """
        return datetime.datetime\
            .strptime(remote_time, JSONHelper.REMOTE_TIME_FORMAT_STR)\
            .strftime(ViewMessage.TIME_FORMAT_STR)

    @staticmethod
    def view_message_to_json(view_message):
        view_message_dict = {
                JSONHelper.JSON_MESSAGE_TIME_KEY: view_message.message_time,
                JSONHelper.JSON_MESSAGE_BODY_KEY: view_message.body,
                JSONHelper.JSON_MESSAGE_ID_KEY: view_message.related_view_id,
                JSONHelper.JSON_MESSAGE_TYPE_KEY: view_message.message_type
        }

        return json.dumps(view_message_dict)

    @staticmethod
    def json_to_view_message(json_view_message):
        view_message_dict = json.loads(json_view_message)

        if view_message_dict[JSONHelper.JSON_MESSAGE_TYPE_KEY] == ViewMessage.TYPE_OUTGOING:
            display_name = ViewMessage.USER_DISPLAY_NAME
        else:
            if view_message_dict[JSONHelper.JSON_MESSAGE_ID_KEY] in state.contact_views:
                display_name = state.contact_views[view_message_dict[JSONHelper.JSON_MESSAGE_ID_KEY]].display_name
            else:
                display_name = view_message_dict[JSONHelper.JSON_MESSAGE_ID_KEY]

        return ViewMessage(
                JSONHelper.format_time(view_message_dict[JSONHelper.JSON_MESSAGE_TIME_KEY]),
                view_message_dict[JSONHelper.JSON_MESSAGE_BODY_KEY],
                view_message_dict[JSONHelper.JSON_MESSAGE_ID_KEY],
                display_name,
                view_message_dict[JSONHelper.JSON_MESSAGE_TYPE_KEY]
        )

    @staticmethod
    def dict_to_contact_view(contact_view_dict):
        """ Convert a contact view dict to a ContactView object """

        return ContactView(
                contact_view_dict[JSONHelper.JSON_CONTACT_ID_KEY],
                contact_view_dict[JSONHelper.JSON_CONTACT_DISPLAY_KEY],
                contact_view_dict[JSONHelper.JSON_CONTACT_PHONE_KEY],
                []
        )

    @staticmethod
    def setup_contact_views(json_contacts):
        """ Convert json to a contact view list """

        contact_view_dicts = json.loads(json_contacts)

        for view_id, contact_view_dict in contact_view_dicts.items():
            state.contact_views[view_id] = JSONHelper.dict_to_contact_view(contact_view_dict)


class ThemeFormatter:
    """
        small static class to handle theme
        stores default theme and has helpers
        for formatting it

        stored in configparser format

        two formats:
            dict format: theme: { attr_name: 'foreground, background' }
            tuple format: [ ('attr_name', 'foreground', 'background') ]


        dict format is for configparser, so it can be written to a config file
        list format is for urwid, which calls this a "palette"
    """

    DEFAULT_THEME = {
        'message_time': 'dark red, default',
        'log': 'dark blue, default',
        'incoming': 'dark blue, default',
        'outgoing': 'dark green, default',
        'titlebar': 'black, dark blue',
        'divider': 'black, dark blue'
    }

    FOREGROUND_INDICE = 0
    BACKGROUND_INDICE = 1
    ATTR_SET_LEN = 2

    @staticmethod
    def dict_to_list_format(dict_theme):
        """"""

        list_theme = []
        for attr_name, colors_str in dict_theme.items():
            colors = [color_str.strip() for color_str in colors_str.split(',')]
            if len(colors) == ThemeFormatter.ATTR_SET_LEN:
                list_theme.append(
                    (attr_name, colors[ThemeFormatter.FOREGROUND_INDICE], colors[ThemeFormatter.BACKGROUND_INDICE])
                )
            else:
                return None

        return list_theme


class ConfigHandler:
    """ Deals with all things config file related"""

    # TODO: needs way more testing

    USER_CONFIG_DIR = '.config'
    CONFIG_DIR_NAME = 'smscli'
    CONFIG_DIR_PATH = os.path.join(os.path.expanduser('~'), USER_CONFIG_DIR, CONFIG_DIR_NAME)

    CONFIG_FILE_NAME = 'smscli.conf'
    CONFIG_FILE_PATH = os.path.join(CONFIG_DIR_PATH, CONFIG_FILE_NAME)

    SECTION_THEME = 'Theme'
    SECTION_ALIASES = 'Aliases'

    ERROR_CREATE = 'Failed to create config file'
    ERROR_PARSE = 'Failed to parse config file'

    def init_config(self):
        self.config = configparser.ConfigParser()

        # check if file exists - create if no and write defaults
        if not os.path.isfile(ConfigHandler.CONFIG_FILE_PATH):
            if not os.path.isdir(ConfigHandler.CONFIG_DIR_PATH):
                try:
                    os.makedirs(ConfigHandler.CONFIG_DIR_PATH)
                except OSError as e:
                    print(ConfigHandler.ERROR_CREATE + str(e))
                    return False
            self.create_config()

        try:
            self.config.read(ConfigHandler.CONFIG_FILE_PATH)
        except configparser.Error as e:
            print(ConfigHandler.ERROR_PARSE + str(e))
            return False

        return True

    def create_config(self):
        self.set_defaults()

        with open(ConfigHandler.CONFIG_FILE_PATH, 'w') as config_file:
            self.config.write(config_file)

    def set_defaults(self):
        """ set the default values for the config object that will be written to initial config file """

        self.config[ConfigHandler.SECTION_THEME] = ThemeFormatter.DEFAULT_THEME

        # other default settings will go here

    def get_theme(self):
        if self.config.has_section(ConfigHandler.SECTION_THEME):
            return ThemeFormatter.dict_to_list_format(self.config[ConfigHandler.SECTION_THEME])

    def get_alias(self, alias_name):
        if self.config.has_section(ConfigHandler.SECTION_ALIASES):
            conn_set = [self.config[ConfigHandler.SECTION_ALIASES][name]
                        for name in self.config.options(ConfigHandler.SECTION_ALIASES)
                        if name == alias_name]
            try:
                conn_set = [part.strip(',') for part in conn_set[0].split(',')]     # choose first matched alias
                conn_set[1] = int(conn_set[1])
            except IndexError:
                return None
            else:
                return conn_set
        else:
            return None


class InputHandler:
    """ handles and delegates any kind of input from the user """

    VIEW_KEY = 'meta'       # meta/alt key used as prefix for view commands
    VIEW_COMBO_LEN = 2      # view commands will always be a combo of 2
    VIEW_CLOSE_KEY = 'c'    # key to close the current view

    HISTORY_BACK_KEY = 'up'
    HISTORY_FORWARD_KEY = 'down'

    INPUT_LINE_KEY = 'enter'

    def __init__(self):
        self.history = []       # maintain a history list
        self.current_hist_item = len(self.history)

    def handle_input(self, key):
        """ callback method called by urwid when any kind input happens """

        if key == InputHandler.INPUT_LINE_KEY:
            user_input = state.main_window.get_input()

            if len(user_input):
                # decide if a message or a command
                if user_input[0] == CommandHandler.COMMAND_PREFIX:
                    if state.command_handler.parse_command(user_input):
                        self.history.append(user_input)

                    state.main_window.clear_input()
                elif state.connection_handler.connected and (state.main_window.shown_views[state.main_window.current_view] != state.log_view):
                    state.connection_handler.send_message(user_input)

            # reset current history item
            self.current_hist_item = len(self.history)
        elif key == InputHandler.HISTORY_BACK_KEY or key == InputHandler.HISTORY_FORWARD_KEY:
            self.handle_history(key)
        elif InputHandler.VIEW_KEY in key:
            self.handle_view_command(key)

    def handle_view_command(self, key):
        if len(key.split()) == InputHandler.VIEW_COMBO_LEN:
            action = key.split()[1]
        else:
            return

        if action == InputHandler.VIEW_CLOSE_KEY:
            state.main_window.close_view(state.main_window.current_view)
        else:   # switch view
            self.handle_view_switch(action)

    def handle_view_switch(self, view_index):
        """ uses main window object to switch a view given a index """

        try:
            view_index = int(view_index)
        except ValueError:
            return

        if view_index < len(state.main_window.shown_views):
            # convert view_index to a view_id, this works cause shown_views is a OrderedDict
            view_id = list(state.main_window.shown_views.keys())[view_index]

            if state.main_window.shown_views[view_id] != state.main_window.current_view:
                state.main_window.switch_view(view_id)

    def handle_history(self, hist_dir):
        if hist_dir == InputHandler.HISTORY_BACK_KEY:
            if (self.current_hist_item - 1) >= 0:
                self.current_hist_item -= 1
                state.main_window.input_line.set_edit_text(self.history[self.current_hist_item])
                state.main_window.input_line.set_edit_pos(len(self.history[self.current_hist_item]))
        elif hist_dir == InputHandler.HISTORY_FORWARD_KEY:
            if (self.current_hist_item + 1) < len(self.history):
                self.current_hist_item += 1
                state.main_window.input_line.set_edit_text(self.history[self.current_hist_item])
                state.main_window.input_line.set_edit_pos(len(self.history[self.current_hist_item]))
            elif (self.current_hist_item + 1) == len(self.history):
                self.current_hist_item += 1
                state.main_window.input_line.set_edit_text('')

    @staticmethod
    def ctrl_c_quit(signum, frame):
        """ method to trap ctrl-c """
        shutdown()


class State(object):
    """ 
        state class to hold instances of some objects
        kind of shit but i dont know what else to do, maybe modules...
    """

    def __init__(self):
        self.contact_views = {} # Main data structure, all contacts and conversations are stored here

        self.command_handler = CommandHandler()
        self.connection_handler = ConnectionHandler()
        self.config_handler = ConfigHandler()



def shutdown():
    if state.connection_handler.connected:
        # stop read looper thread
        state.connection_handler.connected = False
        state.connection_handler.socket.shutdown(socket.SHUT_RDWR)
        state.connection_handler.socket.close()

    raise urwid.ExitMainLoop


def main():
    # TODO: implement command line options like config file specification and help
    # TODO: get rid of logview object, can do it through main_window

    global state

    state.log_view = LogView([])         
    state.log_view.print_message('Welcome to smscli')

    state.main_window = MainWindow(state.log_view)

    if not state.config_handler.init_config():
        print('Failed to load config file')
        exit(-1)

    theme = state.config_handler.get_theme()
    if theme is None:
        print('Config file syntax is invalid')
        exit(-1)

    signal.signal(signal.SIGINT, InputHandler.ctrl_c_quit)

    try:
        state.main_loop = urwid.MainLoop(state.main_window, theme, handle_mouse=False, unhandled_input=InputHandler().handle_input)
        state.main_loop.run()
    except urwid.AttrSpecError as e:
        print('Failed to initialize window: ' + str(e))


state = State()

if __name__ == '__main__':
    main()
