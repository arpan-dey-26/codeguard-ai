class _Ns:
    def __getattr__(self, name): return _Ns()
    def __call__(self, *a, **k): return _Ns()
    def bind(self, **k): return self
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def exception(self, *a, **k): pass
contextvars = _Ns(); processors = _Ns(); dev = _Ns(); stdlib = _Ns()
def configure(*a, **k): pass
def make_filtering_bound_logger(*a, **k): return object
def get_logger(*a, **k): return _Ns()
