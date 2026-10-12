"""Tiny event bus. Modules talk to the base (and to each other) only through hooks and services,
so any module can be missing without breaking the others.

  hooks.on("train.window", fn)      subscribe (fn(*args, **kwargs));  returns fn, so it works as a decorator
  hooks.emit("train.window", info)  call every subscriber; one that raises is reported and skipped
  hooks.collect("context.sample")   like emit, but returns the non-None results (providers / filters)
"""
import sys
import traceback


class Hooks:
    def __init__(self, debug=False):
        self._subs = {}
        self.debug = debug

    def on(self, event, fn=None, priority=0):
        if fn is None:                                    # @hooks.on("event") decorator form
            return lambda f: self.on(event, f, priority)
        lst = self._subs.setdefault(event, [])
        lst.append((priority, len(lst), fn))
        lst.sort(key=lambda t: (-t[0], t[1]))             # higher priority first, then registration order
        return fn

    def off(self, event, fn):
        self._subs[event] = [t for t in self._subs.get(event, []) if t[2] is not fn]

    def emit(self, event, *args, **kwargs):
        """Run all subscribers. Returns their return values (failures are logged, never raised)."""
        out = []
        for _p, _i, fn in list(self._subs.get(event, ())):
            try:
                out.append(fn(*args, **kwargs))
            except Exception as e:                        # a broken module must never break recording/UI loops
                name = getattr(fn, "__qualname__", repr(fn))
                print(f"hook error [{event}] {name}: {type(e).__name__}: {e}", file=sys.stderr)
                if self.debug:
                    traceback.print_exc()
        return out

    def collect(self, event, *args, **kwargs):
        return [r for r in self.emit(event, *args, **kwargs) if r is not None]

    def has(self, event):
        return bool(self._subs.get(event))
