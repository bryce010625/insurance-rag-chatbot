"""Chạy flow hoàn chỉnh trên PDF/DOCX/HTML mẫu và in NormalizedDocument.

Flow: Inspector -> Router -> Parser theo định dạng -> Normalizer -> NormalizedDocument

Chạy: uv run \
    python -m scripts.run_example
"""



from rag_ingestion.chunking.chunker import chunk_document
from rag_ingestion.pipelines.parse_pipeline import DocumentIngestionPipeline
from samples import DATA_DIR as DATA
from samples.documents import HTML, build_docx
from samples.pdf import build as build_pdf


def show(result) -> None:
    meta = result.metadata
    print(f"  document_id={result.document_id} title={result.title!r} status={meta['status']}")
    print(f"  hierarchy_stats={meta['hierarchy_stats']}")
    for issue in meta["issues"]:
        print(f"  ISSUE: {issue}")
    for el in result.elements:
        content = el.content if not isinstance(el.content, str) else el.content[:45]
        print({
            "id": el.element_id,
            "type": el.element_type.value,
            "content": content,
            "parent_id": el.parent_id,
            "order": el.order,
            "page": el.page_number,
            "src": f"{el.hierarchy_source}/{el.hierarchy_confidence}",
        })
    print("  -- CHUNKS")
    for c in chunk_document(result):
        print(f"  [{c.chunk_id}] {c.chunk_type:5} path={c.section_path} "
              f"heading_ids={c.heading_ids} elements={c.element_ids}")


def main() -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / "faq.html").write_text(HTML, encoding="utf-8")
    pdf = build_pdf(DATA / "insurance_policy.pdf")
    cases = {
        "DOCX": build_docx(DATA / "benefits.docx"),
        "HTML": DATA / "faq.html",
        "PDF 3 trang (trang 3 là scan)": pdf,
    }
    pipeline = DocumentIngestionPipeline()
    for name, path in cases.items():
        print(f"\n===== {name}: {path.name}")
        show(pipeline.ingest(str(path)))


if __name__ == "__main__":
    main()
