"""
base.py

Every source type (CSV today; JSON, Parquet, COBOL copybook, XML later)
implements this interface. The codegen engine only ever talks to
SchemaInferrer — it doesn't know or care how a given source type figures
out its columns. This is what makes adding a new source type an addition
(new inferrer + new template) rather than a change to existing code.
"""

from abc import ABC, abstractmethod

from schema_types import ColumnSchema


class SchemaInferrer(ABC):
    source_type: str  # e.g. "csv" — used for CLI dispatch and template naming

    @abstractmethod
    def infer(self, config: dict) -> list[ColumnSchema]:
        """
        Given a source-specific config dict (file path, delimiter, etc.),
        return the discovered column schema. Should read only a sample of
        the source, never the full dataset, to keep discovery fast and
        keep the control plane out of the data path (see PRD Section 2 —
        this principle holds even for local prototyping).
        """
        raise NotImplementedError
