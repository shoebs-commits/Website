"""PDFKit FastAPI backend: image-to-PDF, merge, split, history and downloads."""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter

from database import (
    create_operation,
    get_operation,
    initialize_database,
    list_operations,
    update_operation,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

# Limits are intentionally conservative for a student project running locally.
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB per input file
MAX_FILES_PER_REQUEST = 15
MAX_PDF_PAGES = 500
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pdfkit")

app = FastAPI(
    title="PDFKit API",
    description="Backend API for PDFKit document conversion and management.",
    version="1.0.0",
)

# Live Server commonly uses port 5500. Add other local development origins
# here if you choose a different frontend server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    """Prepare storage folders and initialize SQLite."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    initialize_database()
    logger.info("PDFKit API is ready")


@app.get("/")
def root() -> dict:
    return {
        "name": "PDFKit API",
        "status": "running",
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "message": "PDFKit backend is running."}


def safe_display_name(filename: str | None) -> str:
    """Return a harmless display-only filename."""
    name = Path(filename or "uploaded-file").name
    return name[:180] or "uploaded-file"


async def save_upload(upload: UploadFile, destination: Path) -> int:
    """Stream an upload to disk while enforcing the per-file size limit."""
    total = 0
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{safe_display_name(upload.filename)} exceeds the 20 MB file limit.",
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="An uploaded file is empty.")
    return total


def require_file_count(files: list[UploadFile], minimum: int, label: str) -> None:
    if len(files) < minimum:
        raise HTTPException(
            status_code=400,
            detail=f"{label} requires at least {minimum} file(s).",
        )
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"You can upload a maximum of {MAX_FILES_PER_REQUEST} files per request.",
        )


def validate_pdf(path: Path) -> PdfReader:
    """Open and validate a PDF, rejecting encrypted or excessively large files."""
    try:
        reader = PdfReader(str(path), strict=True)
        if reader.is_encrypted:
            raise HTTPException(
                status_code=400,
                detail="Password-protected PDFs are not supported.",
            )
        page_count = len(reader.pages)
        if page_count == 0:
            raise HTTPException(status_code=400, detail="The PDF contains no pages.")
        if page_count > MAX_PDF_PAGES:
            raise HTTPException(
                status_code=400,
                detail=f"PDFs are limited to {MAX_PDF_PAGES} pages.",
            )
        return reader
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="One of the uploaded files is not a valid, readable PDF.",
        ) from exc


def parse_page_selection(selection: str, page_count: int) -> list[int]:
    """Parse 1-based page numbers/ranges, e.g. '1,3-5', into zero-based indexes."""
    if not selection or not selection.strip():
        raise HTTPException(
            status_code=400,
            detail="Enter the pages to extract, for example 1,3-5.",
        )

    selected: set[int] = set()
    parts = selection.split(",")
    if len(parts) > MAX_PDF_PAGES:
        raise HTTPException(status_code=400, detail="Too many page selections.")

    for part in parts:
        token = part.strip()
        if not token:
            raise HTTPException(status_code=400, detail="Check the page selection format.")
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", token)
        if not match:
            raise HTTPException(
                status_code=400,
                detail="Invalid page selection. Use a format such as 1,3-5.",
            )

        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > page_count:
            raise HTTPException(
                status_code=400,
                detail=f"Page numbers must be between 1 and {page_count}.",
            )
        selected.update(range(start - 1, end))

    return sorted(selected)


def begin_record(operation_type: str, filenames: Iterable[str]) -> int:
    return create_operation(operation_type, list(filenames))


def finish_failed(operation_id: int | None, message: str) -> None:
    if operation_id is not None:
        update_operation(operation_id, "failed", error_message=message[:500])


def finish_success(operation_id: int, output_name: str) -> None:
    update_operation(
        operation_id,
        "success",
        output_filename=output_name,
    )


def make_output_path() -> tuple[str, Path]:
    filename = f"pdfkit_{uuid.uuid4().hex}.pdf"
    return filename, OUTPUT_DIR / filename


def cleanup(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove temporary file: %s", path)


@app.post("/api/image-to-pdf")
async def image_to_pdf(files: list[UploadFile] = File(...)) -> dict:
    require_file_count(files, 1, "Image to PDF")
    temp_paths: list[Path] = []
    operation_id: int | None = None
    output_path: Path | None = None

    try:
        for upload in files:
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise HTTPException(
                    status_code=400,
                    detail="Only JPG, JPEG and PNG images are supported.",
                )
            temp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
            await save_upload(upload, temp_path)
            temp_paths.append(temp_path)

        operation_id = begin_record(
            "image_to_pdf", [safe_display_name(file.filename) for file in files]
        )
        output_name, output_path = make_output_path()

        converted_images: list[Image.Image] = []
        try:
            for path in temp_paths:
                with Image.open(path) as source:
                    source.load()
                    if source.format not in {"JPEG", "PNG"}:
                        raise HTTPException(
                            status_code=400,
                            detail="An uploaded image is not a valid JPG or PNG file.",
                        )
                    converted_images.append(source.convert("RGB"))
            if not converted_images:
                raise HTTPException(status_code=400, detail="No valid images were provided.")

            first, *remaining = converted_images
            first.save(
                output_path,
                "PDF",
                save_all=True,
                append_images=remaining,
                resolution=150.0,
            )
        finally:
            for image in converted_images:
                image.close()

        finish_success(operation_id, output_name)
        return {
            "success": True,
            "message": "Images converted to PDF successfully.",
            "operation_id": operation_id,
            "filename": output_name,
            "download_url": f"/api/download/{operation_id}",
        }

    except HTTPException as exc:
        finish_failed(operation_id, str(exc.detail))
        raise
    except (UnidentifiedImageError, OSError) as exc:
        message = "Could not read one of the images. Please choose valid JPG or PNG files."
        finish_failed(operation_id, message)
        raise HTTPException(status_code=400, detail=message) from exc
    except Exception as exc:
        logger.exception("Image-to-PDF failed")
        finish_failed(operation_id, "Image conversion failed unexpectedly.")
        raise HTTPException(status_code=500, detail="Image conversion failed. Please try again.") from exc
    finally:
        cleanup(temp_paths)


@app.post("/api/merge-pdf")
async def merge_pdf(files: list[UploadFile] = File(...)) -> dict:
    require_file_count(files, 2, "Merge PDF")
    temp_paths: list[Path] = []
    operation_id: int | None = None
    output_path: Path | None = None

    try:
        for upload in files:
            if Path(upload.filename or "").suffix.lower() != ".pdf":
                raise HTTPException(status_code=400, detail="Only PDF files can be merged.")
            temp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}.pdf"
            await save_upload(upload, temp_path)
            temp_paths.append(temp_path)

        operation_id = begin_record(
            "merge_pdf", [safe_display_name(file.filename) for file in files]
        )
        output_name, output_path = make_output_path()
        writer = PdfWriter()
        total_pages = 0

        for path in temp_paths:
            reader = validate_pdf(path)
            total_pages += len(reader.pages)
            if total_pages > MAX_PDF_PAGES:
                raise HTTPException(
                    status_code=400,
                    detail=f"The merged document cannot exceed {MAX_PDF_PAGES} pages.",
                )
            for page in reader.pages:
                writer.add_page(page)

        with output_path.open("wb") as output:
            writer.write(output)
        writer.close()

        finish_success(operation_id, output_name)
        return {
            "success": True,
            "message": "PDF files merged successfully.",
            "operation_id": operation_id,
            "filename": output_name,
            "download_url": f"/api/download/{operation_id}",
            "page_count": total_pages,
        }

    except HTTPException as exc:
        finish_failed(operation_id, str(exc.detail))
        raise
    except Exception as exc:
        logger.exception("PDF merge failed")
        finish_failed(operation_id, "PDF merging failed unexpectedly.")
        raise HTTPException(status_code=500, detail="PDF merging failed. Please try again.") from exc
    finally:
        cleanup(temp_paths)


@app.post("/api/split-pdf")
async def split_pdf(
    file: UploadFile = File(...),
    pages: str = Form(..., description="Pages to extract, e.g. 1,3-5"),
) -> dict:
    if Path(file.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    temp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}.pdf"
    operation_id: int | None = None
    output_path: Path | None = None

    try:
        await save_upload(file, temp_path)
        operation_id = begin_record("split_pdf", [safe_display_name(file.filename)])
        reader = validate_pdf(temp_path)
        selected_pages = parse_page_selection(pages, len(reader.pages))

        output_name, output_path = make_output_path()
        writer = PdfWriter()
        for page_index in selected_pages:
            writer.add_page(reader.pages[page_index])

        with output_path.open("wb") as output:
            writer.write(output)
        writer.close()

        finish_success(operation_id, output_name)
        return {
            "success": True,
            "message": "Selected pages extracted successfully.",
            "operation_id": operation_id,
            "filename": output_name,
            "download_url": f"/api/download/{operation_id}",
            "page_count": len(selected_pages),
            "selected_pages": [index + 1 for index in selected_pages],
        }

    except HTTPException as exc:
        finish_failed(operation_id, str(exc.detail))
        raise
    except Exception as exc:
        logger.exception("PDF split failed")
        finish_failed(operation_id, "PDF splitting failed unexpectedly.")
        raise HTTPException(status_code=500, detail="PDF splitting failed. Please try again.") from exc
    finally:
        cleanup([temp_path])


@app.get("/api/history")
def history(limit: int = 50) -> dict:
    """Return recent operation metadata, newest first."""
    return {"operations": list_operations(limit)}


@app.get("/api/history/{operation_id}")
def operation_details(operation_id: int) -> dict:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    return operation


@app.get("/api/download/{operation_id}")
def download_result(operation_id: int) -> FileResponse:
    operation = get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    if operation["status"] != "success" or not operation["output_filename"]:
        raise HTTPException(status_code=409, detail="This operation has no downloadable result.")

    # Resolve only a basename inside OUTPUT_DIR; never accept a client-supplied path.
    filename = Path(operation["output_filename"]).name
    output_path = (OUTPUT_DIR / filename).resolve()
    if output_path.parent != OUTPUT_DIR.resolve() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="The generated PDF is no longer available.")

    return FileResponse(
        path=output_path,
        media_type="application/pdf",
        filename=filename,
    )
