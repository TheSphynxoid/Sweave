from __future__ import annotations

from fastapi import FastAPI
from sweave.api.projects import (
    create_project,
    list_projects,
    get_project,
    set_active_project,
    delete_project,
    create_session,
    list_sessions,
    set_active_session,
    get_active_session,
    get_active_project,
)

app = FastAPI(title="Sweave Project API")


@app.post("/api/projects")
async def api_create_project(name: str, path: str, description: str = ""):
    project = await create_project(ProjectCreate(name=name, path=path, description=description))
    return {"success": True, "project": project.to_dict()}


@app.get("/api/projects")
async def api_list_projects():
    return {"projects": await list_projects()}


@app.get("/api/projects/{name}")
async def api_get_project(name: str):
    project = await get_project(name)
    if not project:
        return {"error": "Project not found"}, 404
    return project.to_dict()


@app.post("/api/projects/{name}/active")
async def api_set_active_project(name: str):
    return await set_active_project(name)


@app.delete("/api/projects/{name}")
async def api_delete_project(name: str):
    return await delete_project(name)


@app.post("/api/sessions")
async def api_create_session(name: str, project_name: str | None = None):
    session = await create_session(SessionCreate(name=name, project_name=project_name))
    return {"success": True, "session": session.to_dict()}


@app.get("/api/sessions")
async def api_list_sessions(project_name: str | None = None):
    return {"sessions": await list_sessions(project_name)}


@app.post("/api/sessions/{session_id}/active")
async def api_set_active_session(session_id: str):
    return await set_active_session(session_id)


@app.get("/api/sessions/active")
async def api_get_active_session():
    session = await get_active_session()
    if session:
        return session.to_dict()
    return None


@app.get("/api/projects/active")
async def api_get_active_project():
    return await get_active_project()