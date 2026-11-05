"""opj — an independent, compatible reader for Origin project files.

Public API:
    read(path)            parse a file into a Document
    parse(data)           parse an in-memory buffer into a Document
    Header, Record, Document, OpjError
    GlobalHeader, AssetDirectory, AssetEntry, Column, Undecoded
"""

from .opju import (
    AssetDirectory,
    AssetEntry,
    Column,
    Document,
    GlobalHeader,
    Header,
    OpjError,
    Record,
    Undecoded,
    parse,
    read,
)

__all__ = [
    "AssetDirectory",
    "AssetEntry",
    "Column",
    "Document",
    "GlobalHeader",
    "Header",
    "OpjError",
    "Record",
    "Undecoded",
    "parse",
    "read",
]

__version__ = "0.0.1"
