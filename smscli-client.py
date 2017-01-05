#/usr/bin/python

# TODO: make strings generic

import json
import os
import struct
import signal
import socket
import datetime
import threading
import collections
import configparser

import urwid

VIEW_SWITCH_KEY = 'meta'    # meta/alt key used as prefix for switching views
MAX_MESSAGE_LENGTH = 5355

# Main data structure, all contacts and conversations with each are stored here
contact_views = {}


class ViewMessage(urwid.Padding):
    """ Represents a single message in a view """ 

    LOG_SENDER = 0
    OUTGOING_SENDER = 'OUTBOX'
    INCOMING_SENDER = 'INBOX'

    LOG_SENDER_ATTR = 'log'
    OUTGOING_SENDER_ATTR = 'outgoing'
    INCOMING_SENDER_ATTR = 'incoming'

    TIME_ATTR = 'time'
    BODY_ATTR = 'body'

    def __init__(self, time, body, related_view_id, sender_name, message_type):
        self.time = time
        self.body = body
        self.related_view_id = related_view_id
        self.sender_name = sender_name
        self.message_type = message_type

        self.alignment = 'left'
        self.width_type = 'relative'
        self.width_size = 70

        self.sender_attr = ''
        if self.message_type == ViewMessage.LOG_SENDER:
            self.sender_attr = ViewMessage.LOG_SENDER_ATTR
        elif self.message_type == ViewMessage.OUTGOING_SENDER:
            self.sender_attr = ViewMessage.OUTGOING_SENDER_ATTR
        elif self.message_type == ViewMessage.INCOMING_SENDER:
            self.sender_attr = ViewMessage.INCOMING_SENDER_ATTR

        super().__init__(urwid.Text([
                (ViewMessage.TIME_ATTR, self.time + ' - '),
                (self.sender_attr, self.sender_name + ': '),
                (ViewMessage.BODY_ATTR, self.body)
            ]),
            align=self.alignment,
            width=(self.width_type, self.width_size)
        )


class View(object):
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
            datetime.datetime.now().time().strftime('%H:%M:%S'),
            message,
            LogView.VIEW_NAME,
            LogView.VIEW_NAME,
            ViewMessage.LOG_SENDER
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

    TITLE_BAR_ATTR = 'titlebar'
    TITLE_BAR_TEXT = 'smscli'

    DIVIDER_ATTR = 'divider'

    EDIT_CAPTION = '> '
    MAX_VIEWS = 6

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

        self.set_focus('footer')

    def init_views(self, init_view):
        """
            initialise the views
            make this a method so we can call it when connecting to reset
        """
        self.shown_views = collections.OrderedDict({init_view.view_id: init_view})    # subset of views being shown
        self.max_views = MainWindow.MAX_VIEWS
        self.current_view = init_view

    def add_new_view(self, view):
        if len(self.shown_views) <= self.max_views:
            self.shown_views[view.view_id] = view

            self.refresh_divider()
        else:
            log_view.print_message('Maxed out views')

    def switch_view(self, view_id):
        """
            change views, as in switch the current listbox
            in the frame with a different one
            contents['body'][0] -> inner frame
            contents['body'][0].contents['body'] -> (listbox, attr)
        """

        self.contents['body'][0].contents['body'] = (self.shown_views[view_id].listbox, None)
        self.current_view = self.shown_views[view_id]
        self.refresh_divider()

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

        divider_text = '[connected]' if connection_handler.connected else '[disconnected]'

        # do it this way so we can get indices
        for i, keypair in enumerate(self.shown_views.items()):
            if keypair[1] is self.current_view:
                divider_text += ' -' + str(i) + ':' + keypair[1].view_name + '-'
            else:
                divider_text += ' [' + str(i) + ':' + keypair[1].view_name + ']'

        return divider_text


class ConnectionHandler(object):
    """
        Handles all connection with the server

        protocol with server:
            communicate in messages
            messages are JSON strings

            messages can be sent either to or from
            server/client at any time

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

    def __init__(self):
        self.connected = False

    def setup_connection(self, ip_address, port):
        """
            connects to server, reads initial data
            starts up read loop thread
        """

        self.connect(ip_address, port)

        if self.connected:
            main_window.refresh_divider()

            initial_data = self.read_server()
            JSONHelper.setup_contact_views(initial_data)

            # ensure all views except log are closed so we don't have out of date views on reconnects
            main_window.init_views(log_view)

            self.read_looper = threading.Thread(target=self.read_loop)
            self.read_looper.start()

            log_view.print_message('Connected to ' + str(self.ip_address) + ' on port: ' + str(self.port))

    def connect(self, ip_address, port):
        try:
            self.ip_address = socket.inet_aton(ip_address)
            self.port = port

            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((ip_address, int(port)))

            self.connected = True
        except socket.error as e:
            log_view.print_message(str(e))
            self.connected = False

    def read_server(self):
        """ reads a json string from the server """
        
        message = ''

        try:
            length = int.from_bytes(
                self.socket.recv(self.LEN_BYTE_SIZE, socket.MSG_WAITALL),
                'big'
            )

            message = str(self.socket.recv(length, socket.MSG_WAITALL), 'utf-8')

            # clean disconnect will not raise socket.error, so do it ourselves
            if message == '':
                raise socket.error
        except socket.error:
            log_view.print_message('Disconnected')
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
        except socket.error as e:
            log_view.print_message('Lost connection: ' + str(e))
            self.connected = False

    def read_loop(self):
        """ waits for messages from server """

        while self.connected:
            json_message = self.read_server()
            if json_message != '':
                handle_receive_message(json_message)

        # if this is a shutdown, urwid raises an assertion error when we do this
        try:
            main_window.refresh_divider()
            main_loop.draw_screen()
        except AssertionError:
            pass
            

class CommandHandler(object):
    """
        Parses and handles commands
    """
    COMMAND_PREFIX = '/'        # all commands start with /
    COMMAND_METHOD_PREFIX = 'do_'
    HELP_MESSAGE_PREFIX = 'help_'
    DEFAULT_HELP_MESSAGE = 'Usage: /<command> <args>'   # TODO: make this longer

    # help messages
    help_connect = 'Usage: /connect <ip> <port>'
    help_msg = 'Usage: /msg <contact_name/phone_number>'

    def parse_command(self, command):
        # break up command
        command_parts = command[1:].split()
        command_name = command_parts[0]
        command_args = command_parts[1:]

        # call method associated to command
        command_method_str = CommandHandler.COMMAND_METHOD_PREFIX + command_name;

        try:
            getattr(self, command_method_str)(command_args)
        except AttributeError:
            # command does not exist
            self.do_help([])

    def do_connect(self, args):
        """
            /connect <ip> <port>
            connects to a smscli-server on the given ip and port
        """

        num_args = 2

        if len(args) == num_args:
            if not connection_handler.connected:
                connection_handler.setup_connection(args[0], args[1])
            else:
                log_view.print_message("Already connected")
        else:
            self.do_help(['connect'])

    def do_msg(self, args):
        """
            /msg <contact_name/phone_number> - opens a new contact view
            if contact doesnt exist, will create one using given phone number

            invalid phone numbers and such are left for the server to deal with
        """

        num_args = 1

        if connection_handler.connected:
            if len(args) == num_args:
                name = args[0]
                matched_views = [view for view in contact_views.values() if view.display_name.lower() == name.lower()]

                if len(matched_views):
                    for view in matched_views:      # could be multiple contacts with same name -just open all
                        if view.view_id not in main_window.shown_views:
                            main_window.add_new_view(view)
                else:
                    contact_views[name] = ContactView(
                        name,
                        name,
                        name,
                        []
                    )

                    contact_view = contact_views[name]
                    main_window.add_new_view(contact_view)
            else:
                self.do_help(['msg'])
        else:
            log_view.print_message("Not connected")

    def do_quit(self, args):
        exit()

    def do_help(self, args):
        if len(args) == 0:
            log_view.print_message("Unknown command")
            log_view.print_message(CommandHandler.DEFAULT_HELP_MESSAGE)
        else:
            help_message = CommandHandler.HELP_MESSAGE_PREFIX + args[0]
            log_view.print_message(getattr(CommandHandler, help_message))


class JSONHelper(object):
    """
        Util methods for converting between
        JSON strings and objects used here

        Is aware of dict keys used in json
    """

    @staticmethod
    def view_message_to_json(view_message):
        view_message_dict = {
                'time': view_message.time,
                'body': view_message.body,
                'relatedContactId': view_message.related_view_id,
                'smsMessageType': view_message.message_type
        }

        return json.dumps(view_message_dict)

    @staticmethod
    def json_to_view_message(json_view_message):
        view_message_dict = json.loads(json_view_message)

        if view_message_dict['smsMessageType'] == ViewMessage.OUTGOING_SENDER:
            display_name = 'Me'
        else:
            if view_message_dict['relatedContactId'] in contact_views:
                display_name = contact_views[view_message_dict['relatedContactId']].display_name
            else:
                display_name = view_message_dict['relatedContactId']

        time = datetime.datetime.strptime(view_message_dict['time'], '%H:%M:%S %p').strftime('%H:%M:%S')

        return ViewMessage(
                time,
                view_message_dict['body'],
                view_message_dict['relatedContactId'],
                display_name,
                view_message_dict['smsMessageType']
        )

    @staticmethod
    def dict_to_contact_view(contact_view_dict):
        """ Convert a contact view dict to a ContactView object """

        return ContactView(
                contact_view_dict['id'],
                contact_view_dict['displayName'],
                contact_view_dict['phoneNumber'],
                []
        )

    @staticmethod
    def setup_contact_views(json_contacts):
        """ Convert json to a contact view list """

        contact_view_dicts = json.loads(json_contacts)

        for view_id, contact_view_dict in contact_view_dicts.items():
            contact_views[view_id] = JSONHelper.dict_to_contact_view(contact_view_dict)


class ThemeFormatter(object):
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
        'time': 'dark red, default',
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
        """"
        return [(attr_name, split[ThemeFormatter.FOREGROUND_INDICE], colors[ThemeFormatter.BACKGROUND_INDICE])
                for attr_name, colors_str in dict_theme.items()]
        """

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


class ConfigHandler(object):
    # config parser format

    CONFIG_DIR_NAME = 'smscli'
    CONFIG_DIR_PATH = os.path.expanduser('~') + '/' '.config/' + CONFIG_DIR_NAME + '/'

    CONFIG_FILE_NAME = 'smscli.conf'
    CONFIG_FILE_PATH = CONFIG_DIR_PATH + CONFIG_FILE_NAME

    SECTION_THEME = 'Theme'

    def init_config(self):
        self.config = configparser.ConfigParser()

        # check if file exists - create if no and write defaults
        if not os.path.isfile(ConfigHandler.CONFIG_FILE_PATH):
            if not os.path.isdir(ConfigHandler.CONFIG_DIR_PATH):
                try:
                    os.makedirs(ConfigHandler.CONFIG_DIR_PATH)
                except OSError as e:
                    print('Failed to create config: ' + str(e))
                    return False
            self.create_config()

        try:
            self.config.read(ConfigHandler.CONFIG_FILE_PATH)
        except configparser.Error as e:
            print('Failed to parse file: ' + str(e))

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


# TODO: make util class, find classes for these functions

def handle_view_switch(key):
    try:
        view_index = int(key.split()[1])
        view_id = list(main_window.shown_views.items())[view_index][0]

        if view_index < len(main_window.shown_views) and \
                main_window.shown_views[view_id] != main_window.current_view:
            main_window.switch_view(view_id)
    except:
        pass


def add_new_contact(contact_id):
    """ add number not in contacts to our contact list """
    contact_views[contact_id] = ContactView(
            contact_id,
            contact_id,
            contact_id,
            []
    )


def handle_receive_message(message):

    view_message = JSONHelper.json_to_view_message(message)

    if view_message.related_view_id not in contact_views:
        add_new_contact(view_message.related_view_id)

    contact_view = contact_views[view_message.related_view_id]
    contact_view.add_message(view_message)

    if view_message.related_view_id not in main_window.shown_views:
        main_window.add_new_view(contact_view)

    # were in another thread so we have to do this to tell urwid to render
    main_loop.draw_screen()

    # TODO: Raise notification here


def handle_send_message(message):
    """ create a ViewMessage given 
        message  body and current view
        then send and add to view
    """

    if len(message) <= MAX_MESSAGE_LENGTH:
        view_message = ViewMessage(
                datetime.datetime.now().time().strftime('%H:%M:%S'),
                message,
                main_window.current_view.view_id,
                'Me',
                ViewMessage.OUTGOING_SENDER
        )

        if main_window.current_view.view_id not in contact_views:
            add_new_contact(main_window.current_view.view_id)

        connection_handler.write_server(JSONHelper.view_message_to_json(view_message))
        main_window.current_view.add_message(view_message)

        main_window.clear_input()
    else:
        log_view.print_message('Message to long')


def ctrlc_quit(signum, frame):
    """ trap ctrl-c """
    shutdown()


def handle_input(key):
    if key == 'enter':
        user_input = main_window.get_input()
        if user_input[0] == CommandHandler.COMMAND_PREFIX:
            command_handler.parse_command(user_input)
            main_window.clear_input()
        elif connection_handler.connected and (main_window.current_view != log_view):
            handle_send_message(user_input)
    elif VIEW_SWITCH_KEY in key:
        handle_view_switch(key)


def shutdown():
    if connection_handler.connected:
        # stop read looper thread
        connection_handler.connected = False
        connection_handler.socket.shutdown(socket.SHUT_RDWR)
        connection_handler.socket.close()

    raise urwid.ExitMainLoop

if __name__ == '__main__':
    command_handler = CommandHandler()
    connection_handler = ConnectionHandler()
    config_handler = ConfigHandler()

    if not config_handler.init_config():
        print('Failed to load config file')
        exit(-1)

    theme = config_handler.get_theme()
    if theme is None:
        print('Config file syntax is incorrect')
        exit(-1)

    log_view = LogView([])
    log_view.print_message('Welcome to smscli')

    main_window = MainWindow(log_view)

    signal.signal(signal.SIGINT, ctrlc_quit)

    try:
        main_loop = urwid.MainLoop(main_window, theme, handle_mouse=False, unhandled_input=handle_input)
        main_loop.run()
    except urwid.AttrSpecError as e:
        print('Failed to initialize window: ' + str(e))

