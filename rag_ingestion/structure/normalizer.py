# normalizer.py

import hashlib
from collections import Counter
from pathlib import Path

from rag_ingestion.models import ElementType, NormalizedDocument, ParsedElement
from rag_ingestion.structure.hierarchy import (
    REPAIRABLE,
    current_parent,
    heading_parent,
    push_heading,
    validate_hierarchy,
)


class DocumentNormalizer:

    def normalize(
        self,
        file_path: str,
        document_type: str,
        parsed_elements: list[ParsedElement],
        title: str | None = None,
        metadata: dict | None = None
    ) -> NormalizedDocument:

        metadata = metadata if metadata is not None else {}
        issues = metadata.setdefault("issues", [])
        normalized_elements = []

        for index, element in enumerate(parsed_elements):

            # Ensure every element has an ID (tất định: cùng file ra cùng id)
            if not element.element_id:
                element.element_id = f"e{index + 1}"

            # Assign a stable document order
            element.order = index + 1

            normalized_elements.append(element)

        # 1. Dựng parent_id còn thiếu (PDF dựng ở đây, xuyên ranh giới trang)
        self._link_parents(normalized_elements)

        # 2. Kiểm tra bất biến; vi phạm thì gỡ liên kết hỏng và GHI issue, không lưu im lặng
        self._enforce_invariants(normalized_elements, issues)

        # 3. section_path, nguồn và độ tin cậy suy ra từ cây đã hợp lệ
        self._fill_paths_and_sources(normalized_elements)

        metadata["hierarchy_stats"] = self._stats(normalized_elements)

        return NormalizedDocument(
            document_id=self._content_id(file_path),
            source=file_path,
            document_type=document_type,
            title=title or self._guess_title(normalized_elements),
            elements=normalized_elements,
            metadata=metadata
        )

    @staticmethod
    def _content_id(file_path: str) -> str:
        # Băm theo NỘI DUNG file: ingest lại cùng file ra cùng document_id (idempotent)
        digest = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
        return digest[:16]

    @staticmethod
    def _link_parents(elements: list[ParsedElement]) -> None:
        """Điền parent_id còn thiếu theo cây heading, cho mọi định dạng.

        Parser nào đã tự đặt parent_id thì giữ nguyên.
        """
        stack: dict[int, str] = {}
        for el in elements:
            if el.element_type == ElementType.HEADING:
                level = el.heading_level or 1
                if el.parent_id is None:
                    el.parent_id = heading_parent(stack, level)
                push_heading(stack, level, el.element_id)
            elif el.element_type == ElementType.TITLE:
                continue
            elif el.parent_id is None:
                el.parent_id = current_parent(stack)

    @staticmethod
    def _enforce_invariants(elements: list[ParsedElement], issues: list[dict]) -> None:
        by_id = {e.element_id: e for e in elements}
        for v in validate_hierarchy(elements):
            if v.code in REPAIRABLE:
                el = by_id[v.element_id]
                el.parent_id = None
                el.hierarchy_source = "none"
                el.hierarchy_confidence = 0.0
            issues.append({
                "page": by_id[v.element_id].page_number,
                "page_type": None,
                "code": f"hierarchy_{v.code}",
                "detail": v.detail,
                "element_id": v.element_id,
            })

    @staticmethod
    def _fill_paths_and_sources(elements: list[ParsedElement]) -> None:
        by_id = {e.element_id: e for e in elements}
        for el in elements:
            parent = by_id.get(el.parent_id) if el.parent_id else None
            base = list(parent.section_path) if parent else []
            el.section_path = base + [el.content] if el.element_type == ElementType.HEADING else base

            if el.hierarchy_source is None:
                # Phần tử thường thừa hưởng độ tin cậy của heading cha
                if parent:
                    el.hierarchy_source = parent.hierarchy_source
                    el.hierarchy_confidence = parent.hierarchy_confidence
                else:
                    el.hierarchy_source = "none"
                    el.hierarchy_confidence = 0.0

    @staticmethod
    def _stats(elements: list[ParsedElement]) -> dict:
        headings = [e for e in elements if e.element_type == ElementType.HEADING]
        body = [e for e in elements if e.element_type not in (ElementType.HEADING, ElementType.TITLE)]
        return {
            "elements": len(elements),
            "headings": len(headings),
            "headings_by_source": dict(Counter(e.hierarchy_source for e in headings)),
            "body_with_parent": sum(1 for e in body if e.parent_id),
            "body_total": len(body),
        }

    @staticmethod
    def _guess_title(elements: list[ParsedElement]) -> str | None:
        for el in elements:
            if el.element_type == ElementType.TITLE:
                return el.content
        for el in elements:
            if el.element_type == ElementType.HEADING and el.heading_level == 1:
                return el.content
        return None
