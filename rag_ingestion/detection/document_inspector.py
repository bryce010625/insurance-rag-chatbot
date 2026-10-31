from pathlib import Path

from rag_ingestion.models import DocumentProfile, DocumentType


class DocumentInspector:

    def inspect(self, file_path: str) -> DocumentProfile:
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(file_path)

        extension = path.suffix.lower()

        if extension == ".pdf":
            document_type = DocumentType.PDF

        elif extension == ".docx":
            document_type = DocumentType.DOCX

        elif extension in [".html", ".htm"]:
            document_type = DocumentType.HTML

        else:
            raise ValueError(
                f"Unsupported document format: {extension}"
            )

        return DocumentProfile(
            file_path=file_path,
            document_type=document_type
        )
