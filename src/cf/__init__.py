"""Top-level package for cf."""

__author__ = """1Token"""
__email__ = "tyz@1token.trade"
__version__ = "0.1.1"

from cf.log import logger, logger_add_path, set_log_level

__all__ = ["logger", "logger_add_path", "set_log_level"]


def init():
    init_pandas()


def init_pandas():
    import pandas  # noqa: F401

    # TODO set pandas default options
    # pandas.set_option("")
