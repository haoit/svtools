import socketserver
import argparse
import threading
import socket
import select
import logging
import importlib
import pkgutil
import traceback
import cmd
import inspect
from base import PluginBase


thread_lock = threading.Lock()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s]: %(message)s', datefmt='%H:%M:%S')


class Connection:
    __conn_id = 0

    def __init__(self, app_name, sock_client, sock_server, chunk_size=1024, buffer_size=5 * 1024):
        self.__client = sock_client
        self.__server = sock_server
        self.__app_name = app_name
        self._max_chunk_size = chunk_size
        self._max_buffer_size = buffer_size
        # total data server has sent
        self.buffer_server = b''
        # total data client has sent
        self.buffer_client = b''
        thread_lock.acquire(True)
        Connection.__conn_id += 1
        self.__id = Connection.__conn_id
        thread_lock.release()

    @property
    def client(self):
        return self.__client

    @property
    def app_name(self):
        return self.__app_name

    @property
    def server(self):
        return self.__server

    @property
    def max_chunk_size(self):
        return self._max_chunk_size

    @property
    def max_buffer_size(self):
        return self._max_buffer_size

    @property
    def id(self):
        return self.__id

    def clean_buffer(self):
        if len(self.buffer_server) >= self.max_buffer_size:
            self.buffer_server = b''
        if len(self.buffer_client) >= self.max_buffer_size:
            self.buffer_client = b''


class PluginManager:
    def __init__(self, path='plugins'):
        self._enable = True
        self.__modules = []
        self.__instances = []
        self.path = path
        self.loaded_modules = []

    def reload(self):
        self.__modules = []
        self.__instances = []
        self.load(True)

    def load(self, _reload=False):
        '''Dynamic module loading'''
        # https://github.com/cuckoosandbox/cuckoo/blob/master/cuckoo/core/plugins.py#L29
        self.loaded_modules = []
        for _, module_name, _ in pkgutil.iter_modules([self.path], self.path + '.'):
            try:
                if _reload:
                    module = importlib.import_module(module_name)
                    self.__modules.append(importlib.reload(module))
                else:
                    self.__modules.append(importlib.import_module(module_name))
            except ImportError as e:
                logging.error('Unable to load %s: %s' % (module_name, e))

        for module in self.__modules:
            class_members = inspect.getmembers(module, inspect.isclass)
            for name, _class in class_members:
                if not issubclass(_class, PluginBase) or name == 'PluginBase':
                    continue
                try:
                    module = _class()
                    self.__instances.append(module)
                    self.loaded_modules.append(name)
                except Exception as e:
                    logging.error(e)
                    traceback.print_exc()
        logging.info('Loaded modules: ' + str(self.loaded_modules))

    def enable(self):
        self._enable = True

    def disable(self):
        self._enable = False

    def do_new_connection(self, conn: Connection):
        if self._enable:
            for inst in self.__instances:
                try:
                    inst.new_connection(conn)
                except Exception as e:
                    logging.error(inst.__class__.__name__ + '.new_connection ' + str(e))

    def do_send_server(self, data: bytes, conn: Connection) -> bytes:
        if self._enable:
            for inst in self.__instances:
                try:
                    data = inst.send_server(data, conn)
                except Exception as e:
                    logging.error(inst.__class__.__name__ + '.send_server ' + str(e))
        return data

    def do_send_client(self, data: bytes, conn: Connection) -> bytes:
        if self._enable:
            for inst in self.__instances:
                try:
                    data = inst.send_client(data, conn)
                except Exception as e:
                    logging.error(inst.__class__.__name__ + '.send_client ' + str(e))
        return data

    def do_finish_connection(self, conn: Connection):
        if self._enable:
            for inst in self.__instances:
                try:
                    inst.finish_connection(conn)
                except Exception as e:
                    logging.error(inst.__class__.__name__ + '.finish_connection ' + str(e))


class ProxyHandler(socketserver.BaseRequestHandler):
    def setup(self):
        global TARGET_IP
        global TARGET_PORT

        sock_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_server.connect((TARGET_IP, TARGET_PORT))
        # self.server is instance of ProxyServer
        self.conn = Connection(self.server.app_name, self.request, sock_server)
        logging.info('Open connection %d' % self.conn.id)
        # call plugin
        self.server.plugin.do_new_connection(self.conn)

    def handle(self):
        while True:
            try:
                readable, writable, exceptions = select.select((self.conn.client, self.conn.server), [], [])
                for s in readable:
                    chunk = s.recv(self.conn.max_chunk_size)
                    if len(chunk) == 0:
                        return
                    if s == self.conn.client:
                        chunk = self.server.plugin.do_send_server(chunk, self.conn)
                        self.conn.buffer_client += chunk
                        self.conn.server.send(chunk)
                    elif s == self.conn.server:
                        chunk = self.server.plugin.do_send_client(chunk, self.conn)
                        self.conn.buffer_server += chunk
                        self.conn.client.send(chunk)
                self.conn.clean_buffer()
            except socket.error:
                break
            except Exception as e:
                logging.error(e)
                traceback.print_exc()
                break

    def finish(self):
        logging.info('Close connection %d' % self.conn.id)
        self.server.plugin.do_finish_connection(self.conn)
        self.conn.client.close()


class ProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, app_name, server_address, handler, plugin: PluginManager):
        socketserver.TCPServer.__init__(self, server_address, handler)
        self.plugin = plugin
        self.app_name = app_name


class Console(cmd.Cmd):
    prompt = '> '

    def preloop(self):
        global TARGET_IP
        global TARGET_PORT
        global HOST_IP
        global HOST_PORT
        global APP_NAME

        self.plugin = PluginManager()
        self.plugin.load()
        self.proxyserver = ProxyServer(APP_NAME, (HOST_IP, HOST_PORT), ProxyHandler, self.plugin)

        thread = threading.Thread(target=self.proxyserver.serve_forever)
        thread.start()

    def do_reload(self, arg):
        '''Reload rules'''
        self.plugin.reload()

    def do_enable(self, arg):
        '''Enable plugins'''
        self.plugin.enable()

    def do_disable(self, arg):
        '''Disable plugins'''
        self.plugin.disable()

    def do_exit(self, arg):
        '''Exit program'''
        self.proxyserver.server_close()
        return True

    def postloop(self):
        self.do_exit(None)

    def keyboard_interrupt(self):
        self.do_exit(None)


def main():
    global TARGET_IP
    global TARGET_PORT
    global HOST_IP
    global HOST_PORT
    global APP_NAME

    parser = argparse.ArgumentParser(description='Proxy server')
    parser.add_argument('app_name', type=str, help='Application name')
    parser.add_argument('app_server', type=str, help='Target IP')
    parser.add_argument('app_port', type=int, help='Target port')
    parser.add_argument('listen_ip', type=str, help='Host IP')
    parser.add_argument('listen_port', type=int, help='Host port')
    args = parser.parse_args()
    TARGET_IP = args.app_server
    TARGET_PORT = args.app_port
    APP_NAME = args.app_name
    HOST_IP = args.listen_ip
    HOST_PORT = args.listen_port

    cmd = Console()
    try:
        cmd.cmdloop()
    except KeyboardInterrupt:
        cmd.do_exit(None)

    # plugin = PluginManager()
    # plugin.load()
    # server = ProxyServer(args.app_name, (args.listen_ip, args.listen_port), ProxyHandler, plugin)
    # try:
    #     server.serve_forever()
    # except KeyboardInterrupt:
    #     server.server_close()
    # except Exception as e:
    #     server.server_close()
    #     logging.error(e)


if __name__ == '__main__':
    main()
