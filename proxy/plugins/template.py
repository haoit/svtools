from base import PluginBase


# Example plugin
# Must be inherited from PluginBase
# and implement all abstract methods
# proxy server will call ExamplePlugin
class ExamplePlugin(PluginBase):
    def new_connection(self, conn):
        '''Will be called when have a new connection'''
        pass

    def send_server(self, data, conn):
        '''Will be called when send data to server'''
        return data

    def send_client(self, data, conn):
        '''Will be called when send data to client'''
        return data

    def finish_connection(self, conn):
        '''Will be called when connection close'''
        pass
