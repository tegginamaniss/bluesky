# -*- coding: utf-8 -*-
"""
Created on Tue Apr 18 09:44:50 2017

@author: repa
"""

# interface
import Tkinter as tk
import ttk

import tkFileDialog
import os
import subprocess
import socket
import datetime
from netifaces import interfaces, ifaddresses, AF_INET
from time import sleep

# find current file location
basepath = os.sep.join(os.path.abspath(__file__).split(os.sep)[:-3])

# interface to try
iftry = -1


def getInterface():
    global iftry
    addresses = []
    for ifname in interfaces():
        for i in ifaddresses(ifname).setdefault(AF_INET, []):
            if i.has_key('addr') and i['addr'][:3] != '127':
                addresses.append(i['addr'])
    if len(addresses) > 1:
        # update iftry
        iftry = iftry + 1
        if iftry >= len(addresses):
            iftry = 0
        return addresses[iftry]
    elif len(addresses):
        return addresses[0]
    return None


class CreateToolTip(object):
    """
    create a tooltip for a given widget
    """

    def __init__(self, widget, text='widget info'):
        self.waittime = 500  # miliseconds
        self.wraplength = 180  # pixels
        self.widget = widget
        self.text = text
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.widget.bind("<ButtonPress>", self.leave)
        self.id = None
        self.tw = None

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(self.waittime, self.showtip)

    def unschedule(self):
        id = self.id
        self.id = None
        if id:
            self.widget.after_cancel(id)

    def showtip(self, event=None):
        x = y = 0
        x, y, cx, cy = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 20
        # creates a toplevel window
        self.tw = tk.Toplevel(self.widget)
        # Leaves only the label and removes the app window
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry("+%d+%d" % (x, y))
        label = ttk.Label(self.tw, text=self.text, justify='left',
                          background="#ffffff", relief='solid', borderwidth=1,
                          wraplength=self.wraplength)
        label.pack(ipadx=1)

    def hidetip(self):
        tw = self.tw
        self.tw = None
        if tw:
            tw.destroy()


def modifyFile(fname, fname2, subst):
    fdata = ''.join(open(fname, 'r').readlines())
    for k, val in subst.items():
        key = '@' + k + '@'
        fdata = fdata.replace(key, val)
    f2 = open(fname2, 'w')
    f2.write(fdata)
    f2.close()


class StartSimulation(ttk.Frame):
    def __init__(self, root):
        global basepath

        ttk.Frame.__init__(self, root)
        self.parent = root
        self.parent.title("CoPlanar ASAS simulation")
        self.pack(fill=tk.BOTH, expand=1)
        self.single_player = ttk.Button(self, text='single player',
                                        command=self.singlePlayer)
        self.single_player.pack(fill=tk.X, expand=1)
        self.host_multi = ttk.Button(self, text='host multiplayer',
                                     command=self.multiHost)
        self.host_multi.pack(fill=tk.X, expand=1)
        canReach = self.register(self.canReach)
        self.iphost = ttk.Entry(self, width=50, validate='focusout',
                                validatecommand=(canReach, '%P'))
        self.iphost.pack(fill=tk.X, expand=1)
        self.iphosttt = CreateToolTip(
            self.iphost, """Hostname or IP address of host""")
        self.join_multi = ttk.Button(
            self, text="join multiplayer", command=self.joinMulti,
            state=tk.DISABLED)
        self.join_multi.pack(fill=tk.X, expand=1)
        self.cancel = ttk.Button(self, text='cancel running simulation',
                                 command=self.cancelRun, state=tk.DISABLED)
        self.cancel.pack(fill=tk.X, expand=1)
        self.infolabel = ttk.Label(self, text="")
        self.infolabel.pack(fill=tk.X, expand=1)

        self.multihost = ''
        self.simbase = basepath + '/run/ISAP2017'
        self.process = None

    def feedback(self, txt):
        self.infolabel.config(text=txt)

    def canReach(self, hostip=None):
        if hostip is not None:
            self.multihost = hostip
        if self.multihost:
            running = not os.system(
                'ping -i 0.5 -c 1 ' + self.multihost +
                ' >/dev/null 2>/dev/null')
        else:
            running = False
        if running:
            self.join_multi.config(state=tk.NORMAL)
        else:
            self.join_multi.config(state=tk.DISABLED)
        return True

    def buttonsRunning(self):
        print "disabling buttons"
        self.single_player.config(state=tk.DISABLED)
        self.join_multi.config(state=tk.DISABLED)
        self.host_multi.config(state=tk.DISABLED)
        self.iphost.config(state=tk.DISABLED)
        self.cancel.config(state=tk.NORMAL)

    def buttonsClean(self):
        print "normalizing buttons"
        self.single_player.config(state=tk.NORMAL)
        self.canReach()
        self.host_multi.config(state=tk.NORMAL)
        self.iphost.config(state=tk.NORMAL)
        self.cancel.config(state=tk.DISABLED)

    def singlePlayer(self):
        print "starting single player"
        self.feedback("single-player simulation")
        self.buttonsRunning()
        suffix = datetime.datetime.now().strftime('%y%m%d-%H.%M')
        subprocess.Popen(('/bin/bash', 'links.script'),
                         cwd=self.simbase + '/single').wait()
        self.process = subprocess.Popen(
            '../../../dueca_run.x', shell=True,
            cwd=self.simbase + '/single',
            stdout=open(self.simbase + '/single/normal.log' + suffix, 'w'),
            stderr=open(self.simbase + '/single/error.log' + suffix, 'w'))
        self.after_idle(self.checkProcess)

    def cancelRun(self):
        if self.process:
            print "terminating"
            self.feedback("cancelling simulation")
            self.process.send_signal(15)
        else:
            print "nothing to cancel"

    def checkProcess(self, *args):
        # print "idle"
        if self.process:
            res = self.process.poll()
            if res is None:
                sleep(0.1)  # throttle a bit
                self.after_idle(self.checkProcess)
                return
            if res:
                self.feedback("simulation ended with error code %s" % res)
            else:
                self.feedback("simulation ended")
            self.process = None
            self.buttonsClean()

    def multiHost(self):
        print "hosting multiplayer"
        hname = socket.gethostname()
        ipaddress = getInterface()
        if not ipaddress:
            self.feedback("No ip address found")
            return
        self.feedback("hosting multi-player simulation, hostname %s (%s)" %
                      (hname, ipaddress))
        self.buttonsRunning()
        suffix = datetime.datetime.now().strftime('%y%m%d-%H.%M')
        modifyFile(self.simbase + '/master/dueca.mod.in',
                   self.simbase + '/master/dueca.mod',
                   {'ifaddress': ipaddress})
        subprocess.Popen(('/bin/bash', 'links.script'),
                         cwd=self.simbase + '/master').wait()
        self.process = subprocess.Popen(
            '../../../dueca_run.x', cwd=self.simbase + '/master',
            stdout=open(self.simbase + '/master/normal.log' + suffix, 'w'),
            stderr=open(self.simbase + '/master/error.log' + suffix, 'w'))
        self.after_idle(self.checkProcess)

    def joinMulti(self):
        print "joining multi player"
        self.feedback("multi-player simulation")
        self.buttonsRunning()
        suffix = datetime.datetime.now().strftime('%y%m%d-%H.%M')
        modifyFile(self.simbase + '/peer/dueca.mod.in',
                   self.simbase + '/peer/dueca.mod',
                   {'hostname': self.multihost,
                    'ifaddress': getInterface()})
        subprocess.Popen(('/bin/bash', 'links.script'),
                         cwd=self.simbase + '/peer').wait()
        self.process = subprocess.Popen(
            '../../../dueca_run.x', cwd=self.simbase + '/peer',
            stdout=open(self.simbase + '/peer/normal.log' + suffix, 'w'),
            stderr=open(self.simbase + '/peer/error.log' + suffix, 'w'))
        self.after_idle(self.checkProcess)


def main():
    root = tk.Tk()
    app = StartSimulation(root)
    root.mainloop()
    del app


if __name__ == '__main__':
    main()