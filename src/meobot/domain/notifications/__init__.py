"""Sending something to somebody who is not in the chat you are typing in.

Three modules, and the split matters:

* :mod:`~meobot.domain.notifications.models` names the things - what a group is
  for, how private a message is, where an outbound record has got to;
* :mod:`~meobot.domain.notifications.routing` decides whether a given message
  may reach a given destination, as pure rules with no I/O;
* :mod:`~meobot.domain.notifications.templates` turns validated structured data
  into Vietnamese, and refuses any field it was not told to expect.

Nothing here talks to Telegram or to a database. That is the application
layer's job, and keeping the rules out of it is what makes "could this leak a
leave reason into a group?" a question a test can answer.
"""
