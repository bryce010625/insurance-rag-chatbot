# hierarchy.py
#
# Quy tắc cây heading dùng chung cho mọi parser, normalizer và chunker.
# heading_stack: {cấp heading: element_id của heading đang mở ở cấp đó}

from dataclasses import dataclass

from rag_ingestion.models import ElementType, ParsedElement


def current_parent(heading_stack: dict[int, str]) -> str | None:
    """Phần tử thường thuộc heading sâu nhất đang mở."""
    return heading_stack[max(heading_stack)] if heading_stack else None


def heading_parent(heading_stack: dict[int, str], level: int) -> str | None:
    """Heading cha là heading gần nhất có cấp THẤP HƠN (chịu được nhảy cấp 1 -> 3)."""
    lower = [lv for lv in heading_stack if lv < level]
    return heading_stack[max(lower)] if lower else None


def push_heading(heading_stack: dict[int, str], level: int, element_id: str) -> None:
    for lv in list(heading_stack):
        if lv >= level:
            del heading_stack[lv]
    heading_stack[level] = element_id


def ancestor_ids(parent_id: str | None, by_id: dict[str, ParsedElement]) -> list[str]:
    """Chuỗi element_id từ gốc xuống tới parent_id (gồm cả parent_id)."""
    chain: list[str] = []
    seen: set[str] = set()
    while parent_id is not None and parent_id in by_id and parent_id not in seen:
        seen.add(parent_id)
        chain.append(parent_id)
        parent_id = by_id[parent_id].parent_id
    return chain[::-1]


# ------------------------------------------------------------------ validation

@dataclass
class HierarchyViolation:
    element_id: str
    code: str
    detail: str


# Vi phạm mà việc gỡ parent_id (về None) là cách sửa an toàn
REPAIRABLE = {
    "dangling_parent", "parent_not_before", "parent_not_heading", "level_not_lower", "cycle",
}


def validate_hierarchy(elements: list[ParsedElement]) -> list[HierarchyViolation]:
    """Sáu bất biến của cây. Trả về danh sách vi phạm (rỗng nghĩa là hợp lệ)."""
    violations: list[HierarchyViolation] = []
    index: dict[str, int] = {}

    # 6. element_id không trùng
    for i, el in enumerate(elements):
        if el.element_id in index:
            violations.append(HierarchyViolation(el.element_id, "duplicate_id",
                                                 f"id trùng ở vị trí {i}"))
        else:
            index[el.element_id] = i

    for i, el in enumerate(elements):
        if el.parent_id is None:
            continue
        j = index.get(el.parent_id)
        # 1. cha phải tồn tại trong cùng tài liệu
        if j is None:
            violations.append(HierarchyViolation(el.element_id, "dangling_parent",
                                                 f"parent_id={el.parent_id} không tồn tại"))
            continue
        # 2. cha đứng trước con
        if j >= i:
            violations.append(HierarchyViolation(el.element_id, "parent_not_before",
                                                 f"cha {el.parent_id} không đứng trước con"))
            continue
        parent = elements[j]
        # 3. cha phải là heading hoặc title
        if parent.element_type not in (ElementType.HEADING, ElementType.TITLE):
            violations.append(HierarchyViolation(el.element_id, "parent_not_heading",
                                                 f"cha {el.parent_id} là {parent.element_type.value}"))
        # 4. cấp của cha thấp hơn cấp của con
        elif (el.element_type == ElementType.HEADING
              and parent.element_type == ElementType.HEADING
              and (parent.heading_level or 1) >= (el.heading_level or 1)):
            violations.append(HierarchyViolation(
                el.element_id, "level_not_lower",
                f"cấp cha {parent.heading_level} không thấp hơn cấp con {el.heading_level}"))

    # 5. không có chu trình
    by_id = {el.element_id: el for el in elements}
    for el in elements:
        seen = {el.element_id}
        cur = el.parent_id
        while cur is not None and cur in by_id:
            if cur in seen:
                violations.append(HierarchyViolation(el.element_id, "cycle",
                                                     "chu trình trong parent_id"))
                break
            seen.add(cur)
            cur = by_id[cur].parent_id

    return violations
