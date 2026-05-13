"""
Knowledge Base dashboard pages.

Routes:
  GET /kb            → kb_dashboard_page
  GET /kb/accounts   → kb_accounts_page
  GET /kb/discovery  → kb_discovery_page
  GET /kb/graph      → kb_graph_page
  GET /kb/alerts     → kb_alerts_page
  GET /kb/briefs     → kb_briefs_page
  GET /kb/schedule   → kb_schedule_page
  GET /kb/documents  → kb_documents_page
  GET /kb/inbox      → kb_inbox_page
  GET /kb/teams      → kb_teams_page
  GET /kb/wiki       → kb_wiki_page
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.web._templates import templates, _tctx, get_user_from_request
from config.kb_config import get_domains_for_ui

router = APIRouter(tags=["web-kb"])


@router.get("/kb", response_class=HTMLResponse)
async def kb_dashboard_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_dashboard.html", _tctx(user, request, kb_domains=get_domains_for_ui()))


@router.get("/kb/accounts", response_class=HTMLResponse)
async def kb_accounts_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_accounts.html", _tctx(user, request, kb_domains=get_domains_for_ui()))


@router.get("/kb/discovery", response_class=HTMLResponse)
async def kb_discovery_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_discovery.html", _tctx(user, request, kb_domains=get_domains_for_ui()))


@router.get("/kb/graph", response_class=HTMLResponse)
async def kb_graph_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_graph.html", _tctx(user, request, kb_domains=get_domains_for_ui()))


@router.get("/kb/alerts", response_class=HTMLResponse)
async def kb_alerts_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_alerts.html", _tctx(user, request))


@router.get("/kb/briefs", response_class=HTMLResponse)
async def kb_briefs_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_briefs.html", _tctx(user, request, kb_domains=get_domains_for_ui()))


@router.get("/kb/schedule", response_class=HTMLResponse)
async def kb_schedule_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_schedule.html", _tctx(user, request, kb_domains=get_domains_for_ui()))


@router.get("/kb/documents", response_class=HTMLResponse)
async def kb_documents_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_documents.html", _tctx(user, request))


@router.get("/kb/inbox", response_class=HTMLResponse)
async def kb_inbox_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_inbox.html", _tctx(user, request))


@router.get("/kb/teams", response_class=HTMLResponse)
async def kb_teams_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_teams.html", _tctx(user, request))


@router.get("/kb/wiki", response_class=HTMLResponse)
async def kb_wiki_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("kb_wiki.html", _tctx(user, request))
