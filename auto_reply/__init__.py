from itertools import chain
from random import choice
import re

from .base import BasePlugin


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
- Supports regex capture groups. Use "$n" where n is 1 or higher.

Example:
i/test=test failed
hey=Hello {user}
# this is a comment
ir/^(foo|bar)=I SAW "$1", WOHOO!!''',
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

    def settings_changed(self, **_):
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
                               if (isinstance(_in, re.Pattern) and _in.search(line)) or
                               (isinstance(_in, str) and _in.startswith('i/') and line.lower() == _in[2:].lower()) or
                               (line == _in)]))
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
