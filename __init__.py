import ast
from itertools import chain
import json
from pathlib import Path
from random import choice
import re
from threading import Event, Thread
from time import sleep, time
from urllib import request

from pynicotine.pluginsystem import BasePlugin as NBasePlugin

BASE_PATH = Path(__file__).parent
config_file = BASE_PATH / 'PLUGININFO'
CONFIG = dict([(key, ast.literal_eval(value) if value.startswith('[') else value[1:-1].replace('\\n', '\n'))
               for key, value in map(lambda i: i.split('=', 1), filter(None, config_file.read_text().split('\n')))])

__version__ = CONFIG.get('Version', '0.0.1')


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


class BasePlugin(NBasePlugin):
    settings = metasettings = {}
    default_settings = {
        'check_update': True,
    }
    default_metasettings = {
        'check_update': {
            'description': '''Check for Updates
Check for updates on start and periodically''',
            'type': 'bool',
        },
    }

    __name__ = CONFIG.get('Name')
    update_version = None

    def init(self):
        settings = self.default_settings
        settings.update(self.settings)
        self.settings = settings
        metasettings = self.default_metasettings
        metasettings.update(self.metasettings)
        self.metasettings = metasettings

        self.auto_update = PeriodicJob(name='AutoUpdate',
                                       delay=3600,
                                       update=self.check_update)
        self.auto_update.start()

        self.settings_watcher = PeriodicJob(name='SettingsWatcher', update=self.detect_settings_change)
        self.settings_watcher.start()

        self.log(f'Running version {__version__}')

    def check_update(self):
        try:
            repo = CONFIG.get('Repository')
            if 'dev' in __version__ or not repo or not self.settings['check_update']:
                self.update_version = None
                return

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

    def detect_settings_change(self):
        if not hasattr(self, '_settings_before'):
            self._settings_before = set(self.settings.items())
            return

        after = set(self.settings.items())
        if changes := self._settings_before ^ after:
            change_dict = {
                'before': dict(filter(lambda i: i in self._settings_before, changes)),
                'after': dict(filter(lambda i: i in after, changes))
            }
            self.settings_changed(before=self._settings_before,
                                  after=self.settings,
                                  change=change_dict)
            self._settings_before = set(self.settings.items())

    def settings_changed(self, before, after, change):
        self.log(f'Settings change: {json.dumps(change)}')


class Plugin(BasePlugin):
    settings = {
        'public_replies': 'i/test=Test failed',
        'private_replies': '',
    }
    metasettings = {
        'public_replies': {
            'description': '''Public Replies
Each line represents "flags/incoming message pattern=reply". Only applies to public chat rooms.
- Placeholders: Senders name: {sender}, Own name: {self}, Room name: {room}
- Flags: i: ignore case, r: parse as regex
- Commands: only the "/me" commands can be used (limitation by the plugin system)
- Comments start with #
- If a line can't be parsed it will be logged to the console

Example:
i/test=test failed
hey=Hello {user}
# this is a comment
ir/(foo|bar)=FOOBAR WOHOO!!''',
            'type': 'textview',
        },
        'private_replies': {
            'description': '''Private Replies
Works the same as public replies but for private chats only.''',
            'type': 'textview',
        },
    }

    def init(self):
        super().init()
        self.parse_settings()

    def settings_changed(self, before, after, change):
        self.parse_settings()

    def parse_settings(self):
        def _parse(text):
            result = {}

            for line in filter(None, map(str.strip, text.split('\n'))):
                if line.startswith('#'):
                    continue
                try:
                    _in, out = re.split('(?<!\\\\)=', line, 1)
                except ValueError:
                    self.log(f'No reply found in {line}')
                    continue
                ignore_case = _in.startswith(('i/', 'ri/', 'ir/'))
                is_regex = _in.startswith(('r/', 'ir/', 'ri/'))

                if is_regex:
                    try:
                        _in = re.compile(_in.split('/', 1)[1],
                                         flags=re.IGNORECASE if ignore_case else 0)
                    except Exception as e:
                        self.log(f'Could not parse regex "{_in}": {e}')
                        continue
                result.setdefault(_in, [])
                result[_in].append(out)
            return result

        self.public_replies = _parse(self.settings['public_replies'])
        self.private_replies = _parse(self.settings['private_replies'])

    def auto_reply(self, is_public, user, line, room=''):
        possible_replies = self.public_replies if is_public else self.private_replies
        replies = list(chain(*[zip([_in] * len(outs), outs) for _in, outs in possible_replies.items()
                               if (isinstance(_in, re.Pattern) and _in.search(line))
                               or (isinstance(_in, str) and _in.startswith('i/') and line.lower() == _in[2:].lower())
                               or (line == _in)]))
        if not replies:
            return

        pattern, out = choice(replies)
        if isinstance(pattern, re.Pattern) and (groups := pattern.search(line).groups()):  # type: ignore
            for index, value in enumerate(groups, 1):
                out = out.replace(f'${index}', value)

        reply = out.format(self=self.config.sections['server']['login'],
                           sender=user,
                           room=room)
        if is_public:
            self.send_public(room, reply)
            self.log(f'Sent message "{reply}" to room #{room} as a reply to "{line}" from "{user}"')
        else:
            self.send_private(user, reply)
            self.log(f'Sent message "{reply}" to user "{user}" as a reply to "{line}"')

    def incoming_public_chat_notification(self, room, user, line):
        self.auto_reply(True, user, line, room)

    def incoming_private_chat_notification(self, user, line):
        self.auto_reply(False, user, line)
