"""Rules markdown parsing and database/vector ingestion helpers."""

__all__ = ["Chapter", "Rule", "Section", "parse_rules_md"]


def __getattr__(name: str):
    if name in __all__:
        from . import parser
        return getattr(parser, name)
    raise AttributeError(name)
