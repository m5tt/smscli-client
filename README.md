## smscli-client
A sms client in your console

## Features
Send and receive sms messages all from your console

Uses a irssi-like curses interface using the **urwid** library

## Examples

![](http://i.imgur.com/z5ZzOLY.png)

![](http://i.imgur.com/8Bs508x.png)

## Required

* The corresponding Android app - **smscli-server**, [here](https://github.com/m5tt/smscli-server)
* The urwid python module
* Optionally the gobject python module for notifications

## Usage

Once the app is running, just run smscli-client and enter the command:

`/connect <ip> <port>`

After that you can view and message any contact. Any incoming sms will be opened up in new windows.
Any sms you send on your phone will also be synced in the respective view.

## Notes

Everything is more or less stable and working, but a few features are still missing. May be a little buggy too.


