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


# Public remote job search. Data belongs to Himalayas; direct applications to its source.
from fastapi import Query
import re

@app.get("/discover")
async def discover(q: str = Query(default="", max_length=100), country: str = Query(default="", max_length=60), page: int = Query(default=1, ge=1, le=20), internship_only: bool = False):
    params = {"q": q.strip(), "sort": "recent", "page": page}
    if country.strip():
        params["country"] = country.strip()
    try:
        async with httpx.AsyncClient(timeout=18, follow_redirects=True) as client:
            response = await client.get("https://himalayas.app/jobs/api/search", params=params, headers={"Accept": "application/json", "User-Agent": "InternshipRadar/1.1"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
        raise HTTPException(502, "External job provider is temporarily unavailable")
    results = []
    for item in payload.get("jobs", [])[:30]:
        title = str(item.get("title") or "")
        employment = str(item.get("employmentType") or "")
        seniority = item.get("seniority") or []
        if internship_only and not re.search(r"intern|trainee|graduate|apprentice", title+" "+employment, re.I):
            continue
        link = str(item.get("applicationLink") or "")
        if not link.startswith("https://"):
            continue
        location = item.get("locationRestrictions") or []
        company_logo = item.get("companyLogo") or ""
        results.append({
            "id": str(item.get("guid") or link),
            "title": title,
            "company": str(item.get("companyName") or "Company"),
            "location": ", ".join(x.get("name","") for x in location if isinstance(x,dict)) or "Worldwide / check eligibility",
            "description": str(item.get("excerpt") or "")[:650],
            "source_url": link,
            "company_logo": company_logo if isinstance(company_logo,str) and company_logo.startswith("https://") else "",
            "employment_type": employment,
            "seniority": seniority,
            "published_at": item.get("pubDate"),
            "source": "Himalayas",
            "external": True
        })
    return {"jobs": results, "source": "Himalayas", "total": payload.get("totalCount", 0), "page": page, "notice": "Remote roles may have location restrictions; check the original listing and eligibility before applying."}


class AssistantMessage(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    skills: list[str] = Field(default_factory=list, max_length=15)
    history: list[dict] = Field(default_factory=list, max_length=8)

@app.post("/assistant/chat")
async def assistant_chat(data: AssistantMessage):
    """Career assistant: live search + optional LLM. No customer-care contacts are fabricated."""
    message = data.message.strip()
    low = message.lower()
    intent_jobs = any(word in low for word in ("job", "intern", "opportunit", "vacanc", "career", "remote", "find", "search", "hire", "work"))
    support = any(word in low for word in ("support", "customer care", "contact", "whatsapp", "email", "call", "phone", "helpdesk"))
    results = []
    search_term = message
    if intent_jobs:
        # Strip natural-language prefixes while retaining useful role/skill terms.
        search_term = re.sub(r"(?i)\\b(find|show|search|look for|me|please|jobs?|internships?|opportunities|remote|available|for|in|the|a|an)\\b", " ", message)
        search_term = re.sub(r"\\s+", " ", search_term).strip()[:80]
        if not search_term:
            search_term = " ".join(data.skills[:3])[:80]
        try:
            search = await discover(q=search_term, page=1, internship_only=("intern" in low))
            results = search.get("jobs", [])[:5]
        except HTTPException:
            pass
    help_text = (
        "I can help you navigate Internship Radar, search live remote opportunities, "
        "choose roles matching your skills, and explain application tracking. "
        "Use Find Internships to search; My Applications to track saved applications; "
        "My Profile to add skills; and the top-right account button to sign in."
    )
    if support:
        help_text = ("Customer care phone, WhatsApp and email are not configured yet. "
                     "You can use the Contact Support options once the administrator adds verified details.")
    elif intent_jobs:
        help_text = (f"I searched external remote job listings for '{search_term or 'general roles'}'. "
                     f"I found {len(results)} results to explore below. "
                     "Check the original listing for eligibility, deadlines and availability."
                     if results else
                     "I couldn't retrieve matching live opportunities right now. Try a specific skill or job title. "
                     "You can also search using the Find Internships section.")
    elif "apply" in low or "track" in low or "save" in low:
        help_text = "To track an application, sign in, open Find Internships, and select Save / Track. Then open My Applications to update the status. External job links can be saved to this browser."
    elif "sign" in low or "register" in low or "account" in low:
        help_text = "Select Sign in / Register at the top of the page. Register with your email and password, confirm your email if asked, and then sign in."
    elif "skill" in low or "profile" in low:
        help_text = "Open My Profile, add your skills and field of study, and select Save my skills. Then use Find matching jobs to search for relevant opportunities."
    ai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if ai_key:
        try:
            system = (
                "You are Internship Radar's concise, friendly career and app assistant. "
                "Explain the app accurately. Only recommend specific real jobs from the supplied results; "
                "never invent vacancies, deadlines, contacts, eligibility, or application links. "
                "For support, contact details are not configured yet. "
                "Give practical matching guidance for the user's skills. "
                "Mention that external remote roles may be location-restricted. "
                "Do not promise jobs or ask for sensitive personal data."
            )
            context = {"message":message,"skills":data.skills,"live_jobs":results,
                       "app_guidance":help_text}
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization":"Bearer "+ai_key,"Content-Type":"application/json"},
                    json={"model":os.environ.get("OPENAI_MODEL","gpt-4o-mini"),
                          "messages":[{"role":"system","content":system},
                                      {"role":"user","content":str(context)}],
                          "temperature":0.35,"max_tokens":320})
                response.raise_for_status()
                ai_reply=response.json()["choices"][0]["message"]["content"]
                return {"reply":ai_reply,"jobs":results,"mode":"ai","support_configured":False}
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            pass
    return {"reply":help_text,"jobs":results,"mode":"guided","support_configured":False}
