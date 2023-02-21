import os


VERSION = "0.1"
IP = "localhost"
PORT = 8080

TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'


def get_base_path():
    """
    returns package base dir
    """
    return os.path.dirname(__file__)
