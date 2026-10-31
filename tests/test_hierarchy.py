"""Test bất biến của cây hierarchy và cách normalizer xử lý vi phạm (không cần PDF/DOCX).

Chạy: uv run \
    pytest tests
"""


from rag_ingestion.models import ElementType, ParsedElement
from rag_ingestion.structure.hierarchy import validate_hierarchy
from rag_ingestion.structure.normalizer import DocumentNormalizer


def el(eid, kind=ElementType.PARAGRAPH, parent=None, level=None, content="x"):
    return ParsedElement(element_id=eid, element_type=kind, content=content, order=0,
                         parent_id=parent, heading_level=level)


H = ElementType.HEADING


def codes(elements) -> set[str]:
    return {v.code for v in validate_hierarchy(elements)}


def test_valid_tree_has_no_violations() -> None:
    assert codes([el("h1", H, level=1), el("h2", H, "h1", 2), el("p", parent="h2")]) == set()


def test_dangling_parent() -> None:
    assert codes([el("p", parent="ghost")]) == {"dangling_parent"}


def test_parent_must_come_before_child() -> None:
    assert "parent_not_before" in codes([el("p", parent="h1"), el("h1", H, level=1)])


def test_parent_must_be_heading() -> None:
    assert codes([el("a"), el("b", parent="a")]) == {"parent_not_heading"}


def test_child_heading_level_must_be_higher_number() -> None:
    assert codes([el("h2", H, level=2), el("h1", H, "h2", 1)]) == {"level_not_lower"}


def test_cycle_detected() -> None:
    assert "cycle" in codes([el("a", H, "b", 1), el("b", H, "a", 2)])


def test_duplicate_id() -> None:
    assert codes([el("a"), el("a")]) == {"duplicate_id"}


def test_normalizer_repairs_and_reports_instead_of_saving_silently(tmp_path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    bad = [el("h1", H, level=1), el("p", parent="ghost")]
    doc = DocumentNormalizer().normalize(str(f), "docx", bad, metadata={"issues": []})
    p = doc.elements[1]
    assert p.parent_id is None and p.hierarchy_source == "none"  # không còn trỏ vào "ghost"
    assert validate_hierarchy(doc.elements) == []
    assert any(i["code"] == "hierarchy_dangling_parent" for i in doc.metadata["issues"])


def test_normalizer_fills_section_path_source_and_stats(tmp_path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    h1 = el("h1", H, level=1, content="A")
    h1.hierarchy_source, h1.hierarchy_confidence = "style", 1.0
    h2 = el("h2", H, level=2, content="B")
    h2.hierarchy_source, h2.hierarchy_confidence = "style", 1.0
    doc = DocumentNormalizer().normalize(str(f), "docx", [h1, h2, el("p")], metadata={})
    p = doc.elements[2]
    assert p.parent_id == "h2" and p.section_path == ["A", "B"]
    assert p.hierarchy_source == "style" and p.hierarchy_confidence == 1.0
    assert doc.metadata["hierarchy_stats"]["headings_by_source"] == {"style": 2}
    assert doc.title == "A"
