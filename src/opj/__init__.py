"""opj — an independent, compatible reader for Origin project files.

Public API:
    read(path)            parse a file into a Document
    parse(data)           parse an in-memory buffer into a Document
    Header, Record, Document, OpjError
"""

from .opju import (
    Document,
    Header,
    OpjError,
    Record,
    parse,
    read,
)

__all__ = [
    "Document",
    "Header",
    "OpjError",
    "Record",
    "parse",
    "read",
]

__version__ = "0.0.1"
