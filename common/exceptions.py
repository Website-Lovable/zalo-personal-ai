class FeatureDisabledError(Exception):
    """Raised when code tries to use a feature that is switched off by a flag."""


class NotImplementedInPhase(NotImplementedError):
    """Raised by stubs that are scheduled for a later phase."""

    def __init__(self, feature: str, phase: str):
        super().__init__(f"{feature} is implemented in Phase {phase}")
        self.feature = feature
        self.phase = phase
