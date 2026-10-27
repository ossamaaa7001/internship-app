import os
from typing import Literal
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
ALLOWED_ORIGINS = [s.strip() for s in os.environ.get("ALLOWED_ORIGINS", "").split(",") if s.strip()]
app = FastAPI(title="Internship Radar API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False, allow_methods=["GET","POST","PATCH","DELETE"], allow_headers=["Authorization","Content-Type"])

class ApplicationInput(BaseModel):
    internship_id: str
    status: Literal["saved","applied","interview","offer","rejected","withdrawn"] = "saved"
    notes: str = Field(default="", max_length=4000)

class ApplicationUpdate(BaseModel):
    status: Literal["saved","applied","interview","offer","rejected","withdrawn"]
    notes: str | None = Field(default=None, max_length=4000)

async def request(method, path, token=None, payload=None, params=None):
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise HTTPException(503, "Supabase configuration missing")
    headers = {"apikey": SUPABASE_PUBLISHABLE_KEY, "Content-Type":"application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.request(method, SUPABASE_URL + path, headers=headers, json=payload, params=params)
        except httpx.RequestError:
            raise HTTPException(502, "Upstream database unavailable")
    if response.status_code >= 400:
        raise HTTPException(response.status_code, "Upstream request failed")
    return response.json() if response.content else None

async def user_token(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "Bearer token required")
    user = await request("GET", "/auth/v1/user", token)
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(401, "Invalid token")
    return user["id"], token

@app.get("/health")
def health():
    return {"status": "ok", "configured": bool(SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY)}

@app.get("/internships")
async def internships():
    return await request("GET", "/rest/v1/internships", params={"select":"id,title,company,location,description,source_url,deadline,created_at","is_active":"eq.true","order":"created_at.desc","limit":"100"})

@app.get("/me")
async def me(authorization: str | None = Header(default=None)):
    uid, token = await user_token(authorization)
    profiles = await request("GET","/rest/v1/profiles",token,params={"select":"*","id":"eq."+uid})
    return {"id":uid,"profile": profiles[0] if profiles else None}

@app.get("/applications")
async def applications(authorization: str | None = Header(default=None)):
    uid, token = await user_token(authorization)
    return await request("GET","/rest/v1/applications",token,params={"select":"*","user_id":"eq."+uid,"order":"created_at.desc"})

@app.post("/applications", status_code=201)
async def create_application(data: ApplicationInput, authorization: str | None = Header(default=None)):
    uid, token = await user_token(authorization)
    return await request("POST","/rest/v1/applications?select=*",token,{"user_id":uid,**data.model_dump()})

@app.patch("/applications/{application_id}")
async def update_application(application_id: str, data: ApplicationUpdate, authorization: str | None = Header(default=None)):
    uid, token = await user_token(authorization)
    return await request("PATCH","/rest/v1/applications?select=*&id=eq."+application_id+"&user_id=eq."+uid,token,data.model_dump(exclude_none=True))

@app.get("/matches")
async def matches(authorization: str | None = Header(default=None)):
    uid, token = await user_token(authorization)
    profiles = await request("GET","/rest/v1/profiles",token,params={"select":"skills,field_of_study,location","id":"eq."+uid})
    internships_list = await internships()
    profile = profiles[0] if profiles else {}
    keywords = [str(s).lower() for s in profile.get("skills", []) if s] + ([str(profile["field_of_study"]).lower()] if profile.get("field_of_study") else [])
    for item in internships_list:
        haystack = " ".join(str(item.get(k) or "") for k in ("title","description","location")).lower()
        item["match_score"] = round(100 * sum(k in haystack for k in keywords) / len(keywords)) if keywords else 0
    return sorted(internships_list,key=lambda i:i["match_score"],reverse=True)
