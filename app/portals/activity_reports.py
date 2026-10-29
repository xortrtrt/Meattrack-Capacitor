"""Scoped preview, document and download endpoints for Sales Activity Reports."""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.concurrency import run_in_threadpool

from app.business_time import business_today
from app.config import SESSION_SECRET_KEY
from app.report_documents import csv_document, document_context, excel_document
from app.repositories import activity_reports as reports
from app.web import templates
from . import shared

router = APIRouter()
signer = URLSafeTimedSerializer(SESSION_SECRET_KEY, salt="sales-activity-report-preview-v1")


def authorize(request: Request, role_key: str, *, owner_allowed: bool = False):
    if role_key not in ({"team-leader", "sales-supervisor", "owner"} if owner_allowed else {"team-leader", "sales-supervisor"}):
        raise HTTPException(404)
    guard = shared.require_portal_session(request, role_key)
    if guard:
        return guard
    if role_key != "owner":
        guard = shared.require_team_leader_role(request, "sales")
        if guard:
            return guard
    return None


def workspace_context(request: Request) -> dict:
    today = business_today()
    start = today.replace(day=1)
    last_month_end = start - timedelta(days=1)
    context = {"activity_preview": None, "activity_error": "", "preview_token": "",
               "report_start": start.isoformat(), "report_end": today.isoformat(),
               "previous_start": last_month_end.replace(day=1).isoformat(), "previous_end": last_month_end.isoformat(),
               "report_today": today.isoformat(), "report_view": "history" if request.query_params.get("view") == "history" else "prepare",
               "draft_notes": "", "draft_section_notes": {}}
    if request.query_params.get("preview") != "1":
        return context
    try:
        start = date.fromisoformat(request.query_params.get("period_start", start.isoformat()))
        end = date.fromisoformat(request.query_params.get("period_end", today.isoformat()))
        context.update(report_start=start.isoformat(), report_end=end.isoformat())
        actor_id = shared.session_account_id(request)
        payload = reports.build_report(actor_id, start, end)
        draft = request.session.get("activity_report_draft", {})
        if draft.get("period_start") == start.isoformat() and draft.get("period_end") == end.isoformat():
            context.update(draft_notes=draft.get("notes", ""), draft_section_notes=draft.get("section_notes", {}))
        payload = reports.with_notes(payload, context["draft_notes"], context["draft_section_notes"])
        token = signer.dumps({"actor": actor_id, "start": start.isoformat(), "end": end.isoformat(),
                              "fingerprint": reports.fingerprint(payload), "key": str(uuid4())})
        context.update(activity_preview=payload, preview_token=token, **document_context(payload))
    except ValueError as exc:
        context["activity_error"] = str(exc)
    return context


def _preview_input(request: Request, form):
    try:
        signed = signer.loads(str(form.get("preview_token", "")), max_age=3600)
        if signed["actor"] != shared.session_account_id(request):
            raise ValueError("This preview belongs to another account.")
        notes = str(form.get("notes", ""))
        section_notes = {key: str(form.get(f"notes_{key}", "")) for key in reports.SECTIONS[1:]}
        return signed, notes, section_notes
    except (BadSignature, KeyError, TypeError) as exc:
        raise ValueError("Your preview expired. Generate a new preview before continuing.") from exc


def _refresh(request, role_key, signed, notes, section_notes, error):
    request.session["activity_report_draft"] = {"period_start": signed["start"], "period_end": signed["end"],
                                                "notes": notes, "section_notes": section_notes}
    query = urlencode({"preview": "1", "period_start": signed["start"], "period_end": signed["end"], "error": error})
    return RedirectResponse(f"/portal/{role_key}/reports?{query}", status_code=303)


@router.get("/portal/{role_key}/reports/preview")
def preview(request: Request, role_key: str):
    guard = authorize(request, role_key)
    if guard:
        return guard
    query = dict(request.query_params)
    query["preview"] = "1"
    return RedirectResponse(f"/portal/{role_key}/reports?{urlencode(query)}", status_code=303)


async def submit(request: Request, role_key: str):
    guard = authorize(request, role_key)
    if guard:
        return guard
    form = await request.form()
    try:
        signed, notes, section_notes = _preview_input(request, form)
    except ValueError as exc:
        return shared.redirect_to(shared.safe_portal_path(role_key, "reports", error=str(exc)))
    try:
        report_id = await run_in_threadpool(reports.submit_report, shared.session_account_id(request), date.fromisoformat(signed["start"]),
            date.fromisoformat(signed["end"]), signed["fingerprint"], signed["key"], notes, section_notes)
    except ValueError as exc:
        return _refresh(request, role_key, signed, notes, section_notes, str(exc))
    request.session.pop("activity_report_draft", None)
    return RedirectResponse(f"/portal/{role_key}/reports/{report_id}", status_code=303)


def _output(request: Request, role_key: str, payload: dict, output: str, section: str):
    identifier = str(payload.get("report_id", "preview"))
    filename = f"meattrack-report-{identifier}-{payload['period_start']}-{payload['period_end']}"
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if output == "csv":
        if section not in reports.SECTIONS:
            raise HTTPException(404)
        headers["Content-Disposition"] = f'attachment; filename="{filename}-{section}.csv"'
        return Response(csv_document(payload, section), media_type="text/csv; charset=utf-8", headers=headers)
    if output == "xlsx":
        headers["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return Response(excel_document(payload), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)
    if output not in {"view", "print"}:
        raise HTTPException(404)
    return templates.TemplateResponse(request=request, name="portals/reports/document.html", context={
        **document_context(payload), "role_key": role_key, "print_view": output == "print",
    }, headers=headers)


@router.post("/portal/{role_key}/reports/preview/{output}")
async def preview_output(request: Request, role_key: str, output: str, section: str = "summary"):
    guard = authorize(request, role_key)
    if guard:
        return guard
    try:
        signed, notes, section_notes = _preview_input(request, await request.form())
        payload = await run_in_threadpool(reports.build_report, shared.session_account_id(request), date.fromisoformat(signed["start"]), date.fromisoformat(signed["end"]))
        if reports.fingerprint(payload) != signed["fingerprint"]:
            raise reports.StalePreview("Records changed since the preview. Generate a new preview before downloading.")
        payload = reports.with_notes(payload, notes, section_notes)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return await run_in_threadpool(_output, request, role_key, payload, output, section)


@router.get("/portal/{role_key}/reports/{report_id:int}")
@router.get("/portal/{role_key}/reports/{report_id:int}/{output}")
def saved_output(request: Request, role_key: str, report_id: int, output: str = "view", section: str = "summary"):
    guard = authorize(request, role_key, owner_allowed=True)
    if guard:
        return guard
    payload = reports.saved_report(report_id, shared.session_account_id(request), owner=role_key == "owner")
    if payload is None:
        raise HTTPException(404, "Activity report not found. Legacy sales reports remain available in report history.")
    return _output(request, role_key, payload, output, section)
