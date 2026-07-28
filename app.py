"""
FastAPI backend.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from config import APP_NAME, HOST, PORT, BASE_DIR
from core import database
from core.command_parser import parse_command
from core.document_service import (
    extract_placeholders, generate_document, MissingFieldError,
)
from core.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


class PasswordProtectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        required_password = os.environ.get("APP_PASSWORD")
        if not required_password:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            import base64
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                _username, _, supplied_password = decoded.partition(":")
            except Exception:
                supplied_password = ""
            if secrets.compare_digest(supplied_password, required_password):
                return await call_next(request)

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Office Assistant"'},
        )


app.add_middleware(PasswordProtectMiddleware)


@app.on_event("startup")
def on_startup() -> None:
    database.initialize_database()
    database.seed_default_templates()
    logger.info("%s web server ready.", APP_NAME)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = BASE_DIR / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


class TemplateIn(BaseModel):
    name: str
    category: str
    body: str


class GenerateIn(BaseModel):
    template_id: int
    title: str
    field_values: dict[str, str]


class ParseCommandIn(BaseModel):
    text: str
    placeholders: list[str]


@app.get("/api/templates")
def api_list_templates(category: Optional[str] = None):
    templates = database.list_templates(category=category)
    return [
        {
            "id": t.id, "name": t.name, "category": t.category,
            "body": t.body, "placeholders": extract_placeholders(t.body),
        }
        for t in templates
    ]


@app.get("/api/templates/{template_id}")
def api_get_template(template_id: int):
    template = database.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="টেমপ্লেট পাওয়া যায়নি")
    return {
        "id": template.id, "name": template.name, "category": template.category,
        "body": template.body, "placeholders": extract_placeholders(template.body),
    }


@app.post("/api/templates")
def api_create_template(payload: TemplateIn):
    if not payload.name.strip() or not payload.body.strip():
        raise HTTPException(status_code=400, detail="নাম ও টেমপ্লেট লেখা আবশ্যক")
    template_id = database.add_template(payload.name, payload.category, payload.body)
    return {"id": template_id}


@app.put("/api/templates/{template_id}")
def api_update_template(template_id: int, payload: TemplateIn):
    if not database.get_template(template_id):
        raise HTTPException(status_code=404, detail="টেমপ্লেট পাওয়া যায়নি")
    database.update_template(template_id, payload.name, payload.category, payload.body)
    return {"ok": True}


@app.delete("/api/templates/{template_id}")
def api_delete_template(template_id: int):
    database.delete_template(template_id)
    return {"ok": True}


@app.post("/api/parse-command")
def api_parse_command(payload: ParseCommandIn):
    result = parse_command(payload.text, payload.placeholders)
    return {"category": result.category, "field_values": result.field_values}


@app.post("/api/generate")
def api_generate(payload: GenerateIn):
    template = database.get_template(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="টেমপ্লেট পাওয়া যায়নি")
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="ফাইলের শিরোনাম আবশ্যক")

    try:
        result = generate_document(payload.title, template.body, payload.field_values)
    except MissingFieldError as exc:
        raise HTTPException(status_code=400, detail=f"এই তথ্যগুলো পূরণ করা হয়নি: {exc}")

    doc_id = database.add_document_record(template.id, payload.title, str(result.file_path))
    return {"document_id": doc_id, "file_path": str(result.file_path)}


@app.get("/api/documents")
def api_list_documents():
    return [
        {"id": d.id, "title": d.title, "created_at": d.created_at, "template_id": d.template_id}
        for d in database.list_documents()
    ]


@app.get("/api/documents/{document_id}/download")
def api_download_document(document_id: int):
    records = database.list_documents(limit=1000)
    record = next((r for r in records if r.id == document_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="ডকুমেন্ট পাওয়া যায়নি")
    path = Path(record.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="ফাইলটি ডিস্কে পাওয়া যায়নি")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT, reload=False)
