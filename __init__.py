import ast
import json
from pathlib import Path
from threading import Event, Thread
from time import sleep, time
from urllib import request

from pynicotine.pluginsystem import BasePlugin

BASE_PATH = Path(__file__).parent
config_file = BASE_PATH / 'PLUGININFO'
CONFIG = dict([(key, ast.literal_eval(value) if value.startswith('[') else value[1:-1].replace('\\n', '\n'))
               for key, value in map(lambda i: i.split('=', 1), filter(None, config_file.read_text().split('\n')))])

__version__ = CONFIG.get('Version', '0.0.1')
__version__ = __version__.replace('dev', '')


class Plugin(BasePlugin):

    settings = {
        'check_update': True,
    }
    metasettings = {
        'check_update': {
            'description': '''Check for Updates
Check for updates on start and periodically''',
            'type': 'bool',
        },
    }
    __name__ = CONFIG.get('Name')
    update_version = None

    def init(self):
        self.auto_update = PeriodicJob(name='AutoUpdate',
                                       delay=3600,
                                       update=self.check_update)
        self.auto_update.start()
        self.log(f'Running version {__version__}')

    def check_update(self):
        try:
            repo = CONFIG.get('Repository')
            if 'dev' in __version__ or not repo or not self.settings['check_update']:
                self.update_version = None
                return

            self.log('Checking for updates')
            with request.urlopen(f'https://api.github.com/repos/{repo}/tags') as response:
                latest_info = next(iter(json.load(response)), {})
                latest_version = latest_info.get('name', '')
                if latest_version.replace('v', '') != __version__:
                    self.update_version = latest_version
                    msg = f'# A new version of "{self.__name__}" is available: {latest_version} https://github.com/{repo}/releases/tag/{latest_version}'  # noqa
                    self.log('\n{border}\n{msg}\n{border}'.format(msg=msg, border='#' * len(msg)))
        except Exception as e:
            self.log(f'ERROR: Could not fetch update: {e}')

    def stop(self):
        self.auto_update.stop(False)

    disable = shutdown_notification = stop


class PeriodicJob(Thread):
    __stopped = False
    last_run = None

    name = ''
    delay = 1
    _min_delay = 1

    def __init__(self, delay=None, update=None, name=None, before_start=None):
        super().__init__(name=name or self.name)
        self.delay = delay or self.delay
        self.before_start = before_start
        self.first_round = Event()

        self.__pause = Event()
        self.__pause.set()

        if update:
            self.update = update

    def time_to_work(self):
        delay = self.delay() if callable(self.delay) else self.delay
        return self.__pause.wait() and delay and (not self.last_run or time() - self.last_run > delay)

    def run(self):
        if self.before_start:
            self.before_start()
        while not self.__stopped:
            if self.time_to_work():
                self.update()
                self.last_run = time()
            if not self.first_round.is_set():
                self.first_round.set()
            sleep(self._min_delay)

    def stop(self, wait=True):
        self.__stopped = True
        if wait and self.is_alive():
            self.join()

    def pause(self):
        self.__pause.clear()

    def resume(self):
        self.__pause.set()


