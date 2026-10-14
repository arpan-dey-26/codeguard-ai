"""Minimal pytest stand-in for the dependency-light suites."""
import re as _re


class _Raises:
    def __init__(self, exc, match=None):
        self.exc, self.match = exc, match
        self.value = None
    def __enter__(self): return self
    def __exit__(self, t, v, tb):
        if t is None:
            raise AssertionError("DID NOT RAISE %s" % self.exc)
        if not issubclass(t, self.exc): return False
        if self.match and not _re.search(self.match, str(v)):
            raise AssertionError("pattern %r not in %r" % (self.match, str(v)))
        self.value = v
        return True


def raises(exc, match=None): return _Raises(exc, match)


def fixture(*a, **k):
    if a and callable(a[0]):
        a[0].__is_fixture__ = True
        return a[0]
    def deco(fn):
        fn.__is_fixture__ = True
        fn.__fixture_scope__ = k.get("scope", "function")
        return fn
    return deco


class _Mark:
    def parametrize(self, argnames, argvalues):
        names = [n.strip() for n in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
        def deco(fn):
            existing = getattr(fn, "__params__", [])
            fn.__params__ = existing + [(names, list(argvalues))]
            return fn
        return deco
    def __getattr__(self, name):
        def deco(*a, **k):
            if a and callable(a[0]): return a[0]
            return lambda fn: fn
        return deco
mark = _Mark()


def skipif(*a, **k): return lambda fn: fn


class MonkeyPatch:
    def __init__(self): self._undo = []
    def setattr(self, target, name, value=None, raising=True):
        if value is None and not isinstance(name, str):
            raise TypeError
        old = getattr(target, name, None)
        self._undo.append((target, name, old))
        setattr(target, name, value)
    def undo(self):
        for t, n, o in reversed(self._undo):
            setattr(t, n, o)
        self._undo.clear()
