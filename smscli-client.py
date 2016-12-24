#/usr/bin/python

import sys
import json
import struct
import signal
import socket
import datetime
import threading
import collections

import urwid

COMMAND_PREFIX = '/'        ## all commands start with /
VIEW_SWITCH_KEY = 'meta'    ## meta/alt key used as prefix for swtiching views
MAX_MESSAGE_LENGTH = 5355

## Main data structure, all contacts and conversations with each are stored here
contact_views = {}

class ViewMessage(urwid.Padding):
    """ Represents a single message in a view """ 

    TIMESTAMP_ATTR = 'timestamp'
    SENDER_ATTR = 'sender'
    BODY_ATTR = 'body'

    def __init__(self, timestamp, related_view_id, body):
        ## TODO: format timestamp and body in a generic manner
        self.timestamp = timestamp
        self.related_view_id = related_view_id
        self.body = body

        self.alignment = 'left'
        self.width_type = 'relative'
        self.width_size = 60

        super().__init__(urwid.Text([
                (ViewMessage.TIMESTAMP_ATTR, self.timestamp),
                (ViewMessage.SENDER_ATTR, self.related_view_id),
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

    def add_message(self, view_message):
        self.listwalker.append(view_message)

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
            LogView.VIEW_NAME,
            message
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
        self.shown_views = collections.OrderedDict({init_view.view_id: init_view })
        self.view_count = 1;
        self.max_views = 6
        self.current_view = init_view

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

    def add_new_view(self, view):
        if self.view_count <= self.max_views:
            self.shown_views[view.view_id] = view

            self.view_count += 1
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

        self.divider.original_widget.set_text(self.build_divider_text())


    def build_divider_text(self):
        """
            builds the divider text

            looks like: '[connected]    [0:name0] [1:name1]'
        """

        divider_text = '[connected]' if connection_handler.connected else '[disconnected]'

        ## do it this way so we can get indices
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

    LEN_SIZE = 4
    LEN_STRUCT_FORMAT = '! i'

    def __init__(self):
        self.connected = False

    def setup_connection(self, ip_address, port):
        """
            connects to server, reads initial data
            starts up read loop thread
        """

        self.connect(ip_address, port)
        main_window.refresh_divider()

        initial_data = self.read_server()
        self.read_looper = threading.Thread(target=self.read_loop)
        self.read_looper.start()

        JSONHelper.setup_contact_views(initial_data)

    def connect(self, ip_address, port):
        try:
            self.ip_address = socket.inet_aton(ip_address)
            self.port = port

            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((ip_address, int(port)))
            self.connected = True
            log_view.print_message('Connected to ' + str(self.ip_address) + ' on port: ' + str(self.port))
        except socket.error as e:
            log_view.print_message(str(e))

    def read_server(self):
        """ reads a json string from the server """
        
        message = ''

        try:
            length = int.from_bytes(
                    self.socket.recv(self.LEN_SIZE, socket.MSG_WAITALL),
                    'big'
            )
            message = str(self.socket.recv(length, socket.MSG_WAITALL), 'utf-8')
        except socket.error as e:
            log_view.print_message(str(e))
            main_window.refresh_divider()

        return message

    def write_server(self, message):
        """
            write a json string to the server

            we use the struct module to correctly send
            the integer size, using network byte order/endianess
        """

        try: 
            self.socket.sendall(struct.pack(ConnectionHandler.LEN_STRUCT_FORMAT, len(message)))
            self.socket.sendall(message.encode('utf-8'))
        except socket.error as e:
            log_view.print_message(str(e))
            main_window.refresh_divider()

    def read_loop(self):
        """ waits for messages from server """

        while 1:
            json_message = self.read_server()
            handle_receive_message(json_message)
            

class CommandHandler(object):
    """
        Parses and handles all commands

        Handles some, delegates others to
        external objects 
    """

    def parse_command(self, command):
        command_parts = command[1:].split()
        command_name = command_parts[0]
        command_args = command_parts[1:]

        ## TODO: handle args properly

        if (command_name == 'connect'):
            connection_handler.setup_connection(
                    command_args[0], 
                    command_args[1]
            )

    def print_help(self, command):
        pass


class JSONHelper(object):
    """
        Util methods for converting between 
        JSON strings and objects used here

        Is aware of dict keys used in json
    """

    def view_message_to_json(view_message):
        view_message_dict = {
                'timestamp': view_message.timestamp,
                'body': view_message.body,
                'relatedContactId': view_message.related_view_id,
                'smsMessageType': 'OUTGOING'
        }

        return json.dumps(view_message_dict)

    def json_to_view_message(json_view_message):
        view_message_dict = json.loads(json_view_message)
        return ViewMessage(
                '17:54:33', ## TODO: parse timestamp
                view_message_dict['relatedContactId'],
                view_message_dict['body']
        )

    def dict_to_contact_view(contact_view_dict):
        """ Convert a contact view dict to a ContactView object """

        return ContactView(
                contact_view_dict['id'],
                contact_view_dict['displayName'],
                contact_view_dict['phoneNumber'],
                []
        )
                
    def setup_contact_views(json_contacts):
        """ Convert json to a contact view list """

        log_view.print_message(json_contacts)
        contact_view_dicts = json.loads(json_contacts)

        for view_id, contact_view_dict in contact_view_dicts.items():
            contact_views[view_id] = JSONHelper.dict_to_contact_view(contact_view_dict)

        for key in contact_views.keys():
            log_view.print_message(key)


def handle_view_switch(key):
    try:
        view_index = int(key.split()[1])

        if (view_index < len(main_window.shown_views)):
            main_window.switch_view(
                    list(main_window.shown_views.items())[view_index][0]
            )
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

    if (view_message.related_view_id not in contact_views):
        add_new_contact(view_message.related_view_id)

    contact_view = contact_views[view_message.related_view_id]
    contact_view.add_message(view_message)

    if view_message.related_view_id not in main_window.shown_views:
        log_view.print_message('adding new view')
        main_window.add_new_view(contact_view)

    main_loop.draw_screen()

    ## TODO: Raise notification here

def handle_send_message(message):
    """ create a ViewMessage given 
        message  body and current view
        then send and add to view
    """

    if len(message) <= MAX_MESSAGE_LENGTH:
        view_message = ViewMessage(
                datetime.datetime.now().time().strftime('%H:%M:%S'),
                main_window.current_view.view_id,
                message
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

    if (connection_handler.connected):
        self.read_looper.stop()
        connection_handler.socket.close()

    raise urwid.ExitMainLoop


def handle_input(key):
    if key == 'enter':
        user_input = main_window.get_input()
        if user_input[0] == COMMAND_PREFIX:
            command_handler.parse_command(user_input)
            main_window.clear_input()
        elif (connection_handler.connected) and (main_window.current_view != log_view):
            handle_send_message(user_input)
    elif VIEW_SWITCH_KEY in key:
        handle_view_switch(key)



## TODO: get this from a file
palette = [
    ('timestamp', 'dark blue', 'default'),
    ('sender', 'dark blue', 'default'),
    ('titlebar', 'black', 'dark blue'),
    ('divider', 'black', 'dark blue')
]


if __name__ == '__main__':
    command_handler = CommandHandler()
    connection_handler = ConnectionHandler()

    log_view = LogView([])
    main_window = MainWindow(log_view)

    signal.signal(signal.SIGINT, ctrlc_quit)

    log_view.print_message('Welcome to smscli')

    main_loop = urwid.MainLoop(main_window, palette, handle_mouse=False, unhandled_input=handle_input)
    main_loop.run()
