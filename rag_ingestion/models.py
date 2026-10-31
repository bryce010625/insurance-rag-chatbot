# models.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    HTML = "html"


class PageType(str, Enum):
    TEXT_SIMPLE = "text_simple"
    SCANNED = "scanned"
    MIXED_OR_COMPLEX = "mixed_or_complex"


class ElementType(str, Enum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    LIST = "list"


@dataclass
class DocumentProfile:
    file_path: str
    document_type: DocumentType
    page_count: int = 0


@dataclass
class PageProfile:
    page_number: int
    page_type: PageType
    text_chars: int
    image_count: int
    table_count: int = 0


@dataclass
class ParsedElement:
    element_id: str
    element_type: ElementType
    content: Any

    order: int
    parent_id: Optional[str] = None
    page_number: Optional[int] = None

    # Hierarchy: heading_level chỉ có ở HEADING; section_path do normalizer dựng từ cây;
    # hierarchy_source/confidence cho biết cây này được dựng từ đâu và đáng tin đến đâu
    heading_level: Optional[int] = None
    section_path: list[str] = field(default_factory=list)
    hierarchy_source: Optional[str] = None  # style | html_tag | pdf_toc | pdf_pattern | pdf_font | none
    hierarchy_confidence: Optional[float] = None

    metadata: dict = field(default_factory=dict)


@dataclass
class NormalizedDocument:
    document_id: str
    source: str
    document_type: str
    title: Optional[str] = None

    elements: list[ParsedElement] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
