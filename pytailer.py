#!/usr/bin/env python

"""
Watch a list of files, passing on the filename and callback with optional data.

It also supports files rotation.

Example:

>>> def callback(filename, lines, type):
...     print "my metric is %s" % (type['metric'])
...     print filename, lines
...
>>> filelist = [['/var/log/httpd/logs/access.log.{%Y-%m-%d}',{'metric':'whatever'}],['/var/log/httpd/logs/error_log',{'metric':'something'}]]
>>> lw = LogWatcher(filelist, callback)
>>> lw.loop()

"""

import sys
import os
import time
import errno
import stat
import re

class LogWatcher(object):
    """
    """

    def __init__(self,filelist,callback,tail_lines=0,sizehint=1048576):
        """
        filelist = a list of files to watch

        callback = a function which is called every time one of the file being
            watched is updated;
            this is called with "filename" and "lines" arguments.

        tail_lines = read last N lines from files being watched before starting

        sizehint = passed to file.readlines(), represents an
            approximation of the maximum number of bytes to read from
            a file on every ieration (as opposed to load the entire
            file in memory until EOF is reached). Defaults to 1MB.
        """
        
        self.filelist = filelist
        self._files_map = {}
        self._callback = callback
        self._sizehint = sizehint
        assert callable(callback), repr(callback)
        self.update_files()
        
        for id, _info in self._files_map.items():
            file, _type = _info
            file.seek(os.path.getsize(file.name))  # EOF
            if tail_lines:
                try:
                    lines = self.tail(file.name, tail_lines)
                except IOError as err:
                    if err.errno != errno.ENOENT:
                        raise
                else:
                    if lines:
                        self._callback(file.name, lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    def loop(self, interval=0.05, blocking=True):
        """Start a busy loop checking for file changes every *interval*
        seconds. If *blocking* is False make one loop then return.
        """
        # May be overridden in order to use pyinotify lib and block
        # until the directory being watched is updated.
        # Note that directly calling readlines() as we do is faster
        # than first checking file's last modification times.
        while True:
            self.update_files()
            for fid, _info in list(self._files_map.items()):
                file, _type = _info
                self.readlines(file,_type)
            if not blocking:
                return
            time.sleep(interval)

    def log(self, line):
        """Log when a file is un/watched"""
        print line

    def listdir(self):
        """List directory and filter files by extension.
        You may want to override this to add extra logic or globbing
        support.
        """
        ls = os.listdir(self.folder)
        if self.extensions:
            return [x for x in ls if os.path.splitext(x)[1][1:] \
                                           in self.extensions]
        else:
            return ls

    @classmethod
    def open(cls, file):
        return open(file, 'rb')

    @classmethod
    def tail(cls, fname, window):
        """Read last N lines from file fname."""
        if window <= 0:
            raise ValueError('invalid window value %r' % window)
        with cls.open(fname) as f:
            BUFSIZ = 1024
            # True if open() was overridden and file was opened in text
            # mode. In that case readlines() will return unicode strings
            # instead of bytes.
            encoded = getattr(f, 'encoding', False)
            CR = '\n' if encoded else b'\n'
            data = '' if encoded else b''
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            block = -1
            exit = False
            while not exit:
                step = (block * BUFSIZ)
                if abs(step) >= fsize:
                    f.seek(0)
                    newdata = f.read(BUFSIZ - (abs(step) - fsize))
                    exit = True
                else:
                    f.seek(step, os.SEEK_END)
                    newdata = f.read(BUFSIZ)
                data = newdata + data
                if data.count(CR) >= window:
                    break
                else:
                    block -= 1
            return data.splitlines()[-window:]
        
    def update_files(self):
        ls = []
        for name,_type in self.filelist:
            if "{" in name:
                _time = time.strftime(name[name.find("{")+1:name.find("}")],time.localtime(time.time()))
                r = re.match("^(.*)\{.*\}(.*)$",name)
                name = r.group(1) + _time + r.group(2)
            absname = os.path.realpath(name)
            try:
                st = os.stat(absname)
            except EnvironmentError as err:
                if err.errno != errno.ENOENT:
                    raise
            else:
                if not stat.S_ISREG(st.st_mode):
                    continue
                fid = self.get_file_id(st)
                ls.append((fid, absname,_type))

        # check existent files
        for fid, _info in list(self._files_map.items()):
            file, _type = _info
            try:
                st = os.stat(file.name)
            except EnvironmentError as err:
                if err.errno == errno.ENOENT:
                    self.unwatch(file, fid)
                else:
                    raise
            else:
                if fid != self.get_file_id(st):
                    # same name but different file (rotation); reload it.
                    self.unwatch(file, fid)
                    self.watch(file.name)

        # add new ones
        for fid, fname,_type in ls:
            if fid not in self._files_map:
                self.watch(fname,_type)

    def readlines(self, file,_type):
        """Read file lines since last access until EOF is reached and
        invoke callback.
        """
        while True:
            lines = file.readlines(self._sizehint)
            if not lines:
                break
            self._callback(file.name, lines,_type)

    def watch(self, fname, _type):
        try:
            file = self.open(fname)
            fid = self.get_file_id(os.stat(fname))
        except EnvironmentError as err:
            if err.errno != errno.ENOENT:
                raise
        else:
            self.log("watching logfile %s" % fname)
            self._files_map[fid] = [file,_type]

    def unwatch(self, file, fid):
        # File no longer exists. If it has been renamed try to read it
        # for the last time in case we're dealing with a rotating log
        # file.
        self.log("un-watching logfile %s" % file.name)
        with file:
            lines = self.readlines(file,self._files_map[fid][1])
            del self._files_map[fid]
            if lines:
                self._callback(file.name, lines)

    @staticmethod
    def get_file_id(st):
        if os.name == 'posix':
            return "%xg%x" % (st.st_dev, st.st_ino)
        else:
            return "%f" % st.st_ctime

    def close(self):
        for id, _info in self._files_map.items():
            file, _type = _info
            file.close()
        self._files_map.clear()
