class RuntimeStopped(RuntimeError):
    reason = 'stopped'


class RuntimeLimitReached(RuntimeError):
    reason = 'iteration_limit'


class RuntimeInputUnavailable(RuntimeError):
    reason = 'input_unavailable'


class RuntimeEmptyResponse(RuntimeError):
    reason = 'empty_response'
