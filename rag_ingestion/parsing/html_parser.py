from bs4 import BeautifulSoup

from rag_ingestion.models import ElementType, ParsedElement
from rag_ingestion.structure.hierarchy import current_parent, heading_parent, push_heading


class HTMLParser:

    NOISE = ["nav", "footer", "aside", "script", "style", "form"]
    CONTAINERS = ["table", "ul", "ol"]

    def parse(self, file_path: str) -> list[ParsedElement]:
        with open(file_path, encoding="utf-8") as file:
            html = file.read()

        soup = BeautifulSoup(html, "html.parser")

        elements = []
        order = 0
        heading_stack = {}

        for tag in soup.find_all(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p", "table", "ul", "ol", "img"]
        ):
            # Bỏ phần không phải nội dung, và phần tử lồng trong bảng/danh sách
            # (đã được đọc cùng container cha của chúng)
            if tag.find_parent(self.NOISE) or tag.find_parent(self.CONTAINERS):
                continue

            metadata = {"html_tag": tag.name, "parser": "beautifulsoup"}

            if tag.name == "img":
                content, kind = tag.get("alt", ""), ElementType.IMAGE
                metadata["src"] = tag.get("src")

            elif tag.name == "table":
                rows = [
                    [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
                    for tr in tag.find_all("tr")
                ]
                if not rows:
                    continue
                content = {"headers": rows[0], "rows": rows[1:]}
                kind = ElementType.TABLE

            elif tag.name in ("ul", "ol"):
                items = [li.get_text(" ", strip=True) for li in tag.find_all("li")]
                if not items:
                    continue
                content, kind = {"items": items}, ElementType.LIST

            else:
                content = tag.get_text(" ", strip=True)
                if not content:
                    continue
                kind = ElementType.HEADING if tag.name.startswith("h") else ElementType.PARAGRAPH

            order += 1
            element = ParsedElement(
                element_id=f"e{order}",
                element_type=kind,
                content=content,
                order=order,
                metadata=metadata
            )

            if kind == ElementType.HEADING:
                level = int(tag.name[1])
                element.heading_level = level
                element.hierarchy_source = "html_tag"
                element.hierarchy_confidence = 1.0
                element.parent_id = heading_parent(heading_stack, level)
                push_heading(heading_stack, level, element.element_id)
            else:
                element.parent_id = current_parent(heading_stack)

            elements.append(element)

        return elements
