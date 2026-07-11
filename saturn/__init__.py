"""Saturn: dataset dissector."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("saturn-dissect")
except PackageNotFoundError:  # source tree imported before installation
    __version__ = "0+unknown"
__author__ = "Luke Steuber"
