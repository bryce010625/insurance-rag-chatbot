# main.py

import logging

from rag_ingestion.detection.document_inspector import DocumentInspector
from rag_ingestion.detection.pdf_inspector import PDFInspector
from rag_ingestion.models import DocumentType
from rag_ingestion.parsing.router import ParserRouter
from rag_ingestion.structure.normalizer import DocumentNormalizer

log = logging.getLogger("ingest")


class DocumentIngestionPipeline:

    def __init__(self, ocr_engine=None):
        self.document_inspector = DocumentInspector()
        self.pdf_inspector = PDFInspector()
        self.parser_router = ParserRouter(ocr_engine)
        self.normalizer = DocumentNormalizer()

    def ingest(self, file_path: str):

        # 1. Inspect document
        profile = self.document_inspector.inspect(file_path)

        parsed_elements = []
        issues = []
        metadata = {}

        # 2. Parse based on document type
        if profile.document_type == DocumentType.PDF:

            # File PDF hỏng/mã hóa lộ ra ngay ở bước kiểm tra trang: cô lập theo TÀI LIỆU
            try:
                page_profiles = self.pdf_inspector.inspect(file_path)
            except Exception as e:
                log.warning("pdf_inspect_failed error=%s", type(e).__name__)
                issues.append({
                    "page": None,
                    "page_type": None,
                    "code": type(e).__name__,
                    "detail": "không đọc được cấu trúc PDF"
                })
                page_profiles = []
            metadata["page_count"] = len(page_profiles)

            for page_profile in page_profiles:

                # Cô lập lỗi THEO TRANG: một trang hỏng không làm mất cả tài liệu
                try:
                    parser = self.parser_router.route_pdf_page(
                        page_profile
                    )

                    page_elements = parser.parse_page(
                        file_path,
                        page_profile.page_number
                    )

                    parsed_elements.extend(page_elements)

                    # OCR tin cậy thấp: không im lặng coi là văn bản sạch, đưa vào issues để duyệt
                    low = [e for e in page_elements if e.metadata.get("low_confidence")]
                    if low:
                        issues.append({
                            "page": page_profile.page_number,
                            "page_type": page_profile.page_type.value,
                            "code": "low_ocr_confidence",
                            "detail": f"{len(low)} khối OCR dưới ngưỡng tin cậy: cần người duyệt",
                        })

                except Exception as e:
                    log.warning(
                        "page_failed page=%s type=%s error=%s",
                        page_profile.page_number,
                        page_profile.page_type.value,
                        type(e).__name__
                    )
                    issues.append({
                        "page": page_profile.page_number,
                        "page_type": page_profile.page_type.value,
                        "code": type(e).__name__,
                        "detail": str(e)
                    })

        else:

            # Cô lập lỗi theo TÀI LIỆU cho định dạng không có trang
            try:
                parser = self.parser_router.route_document(profile)

                parsed_elements = parser.parse(file_path)

            except Exception as e:
                log.warning("document_failed type=%s error=%s",
                            profile.document_type.value, type(e).__name__)
                issues.append({
                    "page": None,
                    "code": type(e).__name__,
                    "detail": str(e)
                })

        metadata["issues"] = issues

        # 3. Normalize parser outputs (dựng cây, kiểm tra bất biến, có thể thêm issue)
        normalized_document = self.normalizer.normalize(
            file_path=file_path,
            document_type=profile.document_type.value,
            parsed_elements=parsed_elements,
            metadata=metadata
        )

        # Trạng thái tính SAU normalize vì normalizer có thể thêm issue
        normalized_document.metadata["status"] = (
            "failed" if not normalized_document.elements
            else "partial" if normalized_document.metadata["issues"]
            else "ok"
        )

        return normalized_document
