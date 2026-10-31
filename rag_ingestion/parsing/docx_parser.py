import re

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from rag_ingestion.models import ElementType, ParsedElement
from rag_ingestion.structure.hierarchy import current_parent, heading_parent, push_heading


class DOCXParser:

    def parse(self, file_path: str) -> list[ParsedElement]:

        doc = Document(file_path)

        elements = []
        order = 0
        heading_stack = {}

        for block in doc.iter_inner_content():

            # Paragraph (kiểm tra theo KIỂU: Table cũng có thuộc tính .style)
            if isinstance(block, Paragraph):

                text = block.text.strip()

                if not text:
                    continue

                order += 1
                style_name = block.style.name

                if style_name.startswith("Heading"):
                    match = re.search(r"\d+", style_name)
                    level = int(match.group()) if match else 1

                    element = ParsedElement(
                        element_id=f"e{order}",
                        element_type=ElementType.HEADING,
                        content=text,
                        order=order,
                        parent_id=heading_parent(heading_stack, level),
                        heading_level=level,
                        hierarchy_source="style",
                        hierarchy_confidence=1.0,
                        metadata={"style": style_name}
                    )

                    push_heading(heading_stack, level, element.element_id)

                elif style_name == "Title":
                    element = ParsedElement(
                        element_id=f"e{order}",
                        element_type=ElementType.TITLE,
                        content=text,
                        order=order
                    )

                else:
                    # Paragraph belongs to the nearest preceding heading
                    element = ParsedElement(
                        element_id=f"e{order}",
                        element_type=ElementType.PARAGRAPH,
                        content=text,
                        order=order,
                        parent_id=current_parent(heading_stack)
                    )

                elements.append(element)

            # Table
            elif isinstance(block, Table):

                rows = [
                    [cell.text.strip() for cell in row.cells]
                    for row in block.rows
                ]

                order += 1

                elements.append(
                    ParsedElement(
                        element_id=f"e{order}",
                        element_type=ElementType.TABLE,
                        content={
                            "headers": rows[0] if rows else [],
                            "rows": rows[1:] if rows else []
                        },
                        order=order,
                        parent_id=current_parent(heading_stack)
                    )
                )

        return elements


