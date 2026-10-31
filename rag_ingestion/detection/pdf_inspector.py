import pymupdf

from rag_ingestion.models import PageProfile, PageType


class PDFInspector:

    def inspect(self, file_path: str) -> list[PageProfile]:
        pdf = pymupdf.open(file_path)

        profiles = []

        for index, page in enumerate(pdf):
            text = page.get_text("text").strip()
            images = page.get_images(full=True)
            tables = page.find_tables().tables

            text_chars = len(text)
            image_count = len(images)
            table_count = len(tables)

            if text_chars < 30 and image_count > 0:
                page_type = PageType.SCANNED

            elif text_chars >= 30 and image_count == 0 and table_count == 0:
                page_type = PageType.TEXT_SIMPLE

            else:
                # có bảng hoặc ảnh lẫn văn bản: cần đọc theo vùng
                page_type = PageType.MIXED_OR_COMPLEX

            profiles.append(
                PageProfile(
                    page_number=index + 1,
                    page_type=page_type,
                    text_chars=text_chars,
                    image_count=image_count,
                    table_count=table_count
                )
            )

        pdf.close()
        return profiles
