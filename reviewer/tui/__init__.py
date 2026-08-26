"""The dashboard, built on Textual.

Textual is the one dependency in this project and it is imported only from this
package. The reviewer itself — ``./run.sh``, ``--check``, ``--once`` — stays
standard library only and works on a machine that has never run ``pip``.

The layering, outermost last:

``theme``/``formatting``/``prose``  the vocabulary — colours, glyphs, strings
``models``/``session``/``status``   values and the pure rules over them
``data``                            the only place the store is read
``widgets``/``views``               Textual widgets that render those values
``app``                             wiring, keys and timers
"""

from .app import Dashboard, Runtime, run
from .logs import LogRelay

__all__ = ["Dashboard", "LogRelay", "Runtime", "run"]
