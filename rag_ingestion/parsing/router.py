from rag_ingestion.models import DocumentType, PageType
from rag_ingestion.parsing.docx_parser import DOCXParser
from rag_ingestion.parsing.html_parser import HTMLParser
from rag_ingestion.parsing.ocr import OCREngine, PDFOCRParser
from rag_ingestion.parsing.pdf_parsers import PDFRegionParser, PDFTextParser


class ParserRouter:

    def __init__(self, ocr_engine: OCREngine | None = None):
        self.pdf_text_parser = PDFTextParser()
        self.pdf_ocr_parser = PDFOCRParser(ocr_engine)
        self.pdf_region_parser = PDFRegionParser()

        self.docx_parser = DOCXParser()
        self.html_parser = HTMLParser()

    def route_document(self, profile):
        if profile.document_type == DocumentType.DOCX:
            return self.docx_parser

        if profile.document_type == DocumentType.HTML:
            return self.html_parser

        raise ValueError("PDF requires page-level routing")

    def route_pdf_page(self, page_profile):
        if page_profile.page_type == PageType.TEXT_SIMPLE:
            return self.pdf_text_parser

        if page_profile.page_type == PageType.SCANNED:
            return self.pdf_ocr_parser

        if page_profile.page_type == PageType.MIXED_OR_COMPLEX:
            return self.pdf_region_parser

        raise ValueError("Unsupported page type")
