"""P8 precreated agents — errors."""


class SeedLoadError(ValueError):
    """Raised when a seed cannot be loaded or validated."""


class SeedNotFoundError(ValueError):
    """Raised when a seed is requested but its directory is missing."""


class SeedShaConflict(ValueError):  # noqa: N818
    """Disk SHA changed between dashboard load and apply click."""

    def __init__(self, precreated_id: str, *, expected: str, actual: str) -> None:
        self.precreated_id = precreated_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"Seed {precreated_id!r} changed again; reload the page and try again")


class OrphanedPrecreatedError(ValueError):
    """A precreated agent's seed dir no longer exists on disk."""
