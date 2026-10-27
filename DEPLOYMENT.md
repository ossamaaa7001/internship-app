# Internship Radar deployment

Frontend: static `index.html` on Vercel. The existing preview is a UI prototype; it must be wired to Supabase Auth and the API for live account functionality.

Backend: Render Python web service. Root directory `backend`, build `pip install -r requirements.txt`, start `uvicorn main:app --host 0.0.0.0 --port $PORT`.

Backend environment: SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, ALLOWED_ORIGINS (exact Vercel HTTPS origin).

Supabase project: internship-radar. Existing tables: profiles, internships, saved_internships, applications, notification_preferences; RLS enabled. Verify ownership policies before allowing real users.

Security: never commit passwords, service-role keys, or access tokens. The backend checks Supabase Auth user on every protected request and forwards the user's own JWT so RLS applies. Email notification delivery, live internship ingestion, and production matching AI are not implemented by this starter API.
