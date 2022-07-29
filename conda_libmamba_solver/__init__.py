__version__ = "22.6.0"

from warnings import warn as _warn
from .solver import LibMambaSolver


def get_solver_class(key="libmamba"):
    if key == "libmamba":
        return LibMambaSolver
    if key == "libmamba-draft":
        _warn(
            "The 'libmamba-draft' solver has been deprecated. "
            "The 'libmamba' solver will be used instead. "
            "Please consider updating your code to remove this warning. "
            "Using 'libmamba-draft' will result in an error in a future release.",
        )
        return LibMambaSolver
    raise ValueError("Key must be 'libmamba'")
