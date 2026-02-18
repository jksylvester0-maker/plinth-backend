"""
FastAPI harness for Plinth pipeline.
"""

from dotenv import load_dotenv
load_dotenv()  # Load .env before any module reads os.getenv

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import uuid
import os

# Legacy imports (v1 pipeline)
from schemas import PipelineInput, PipelineOutput
from pipeline import run_pipeline

# New v2 imports
from plinth_v2.api.schemas.envelope import APIEnvelope, ErrorCode
from plinth_v2.services.engine_orchestrator import get_orchestrator
from plinth_v2.services.database import get_db_service, init_db, ensure_default_user

# v2 API routers
from plinth_v2.api.routes.auth import router as auth_router
from plinth_v2.api.routes.onboarding import router as onboarding_router
from plinth_v2.api.routes.ideas import router as ideas_router
from plinth_v2.api.routes.chat import router as chat_router
from plinth_v2.api.routes.drafts import router as drafts_router
from plinth_v2.api.routes.strategy import router as strategy_router
from plinth_v2.api.routes.memory import router as memory_router
from plinth_v2.api.routes.analytics import content_router, analytics_router
from plinth_v2.api.routes.reports import router as reports_router
from plinth_v2.api.routes.linkedin import router as linkedin_router
from plinth_v2.api.routes.capture import router as capture_router
from plinth_v2.api.routes.notifications import router as notifications_router
from plinth_v2.api.routes.trends import router as trends_router
from plinth_v2.api.routes.workouts import router as workouts_router
from plinth_v2.api.routes.voice import router as voice_router
from plinth_v2.api.routes.api_keys import router as api_keys_router
from plinth_v2.api.routes.agency import router as agency_router
from plinth_v2.api.routes.workflow import router as workflow_router
from plinth_v2.api.routes.dashboard import router as dashboard_router
from plinth_v2.api.routes.developer import router as developer_router

app = FastAPI(title="Plinth Creative Brain + Logic Engine")

# CORS middleware
# Build allowed origins from environment variable and hardcoded defaults
cors_origins = [
    "http://localhost:5173",  # Vite default dev server
    "http://localhost:3000",  # Alternative dev port
    "http://localhost:8080",  # Lovable frontend dev port
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8080",
    "https://id-preview--277bde6d-8d97-4d6a-bbf1-66d23b8f62de.lovable.app",  # Lovable preview
    "https://project-haven-pi.vercel.app",  # Production frontend
]

# Add origins from environment variable (comma-separated)
env_origins = os.getenv("CORS_ORIGINS", "").strip()
if env_origins:
    cors_origins.extend([origin.strip() for origin in env_origins.split(",") if origin.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include v2 API routers
app.include_router(auth_router)
app.include_router(onboarding_router)
app.include_router(ideas_router)
app.include_router(chat_router)
app.include_router(drafts_router)
app.include_router(strategy_router)
app.include_router(memory_router)
app.include_router(content_router)
app.include_router(analytics_router)
app.include_router(reports_router)
app.include_router(linkedin_router)
app.include_router(capture_router)
app.include_router(notifications_router)
app.include_router(trends_router)
app.include_router(workouts_router)
app.include_router(voice_router)
app.include_router(api_keys_router)
app.include_router(agency_router)
app.include_router(workflow_router)
app.include_router(dashboard_router)
app.include_router(developer_router)


@app.on_event("startup")
def startup_db():
    """Create tables and seed default user on startup."""
    init_db()
    ensure_default_user()


@app.on_event("startup")
async def startup_linkedin_polling():
    """Start background LinkedIn engagement polling."""
    import asyncio
    try:
        from plinth_v2.services.linkedin_service import linkedin_engagement_poll_loop
        asyncio.create_task(linkedin_engagement_poll_loop())
    except Exception:
        pass


@app.on_event("startup")
async def startup_email_notifications():
    """Start background daily email notification loop."""
    import asyncio
    try:
        from plinth_v2.services.notification_scheduler import daily_email_notification_loop
        asyncio.create_task(daily_email_notification_loop())
    except Exception:
        pass


@app.on_event("startup")
async def startup_trend_fetch():
    """Start background trend feed fetching loop."""
    import asyncio
    try:
        from plinth_v2.services.trend_radar import trend_fetch_loop
        asyncio.create_task(trend_fetch_loop())
    except Exception:
        pass


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "services": {
            "auth": True,
            "onboarding": True,
            "ideas": True,
            "chat": True,
            "drafts": True,
            "strategy": True,
            "memory": True,
        }
    }


def get_trace_id() -> str:
    """Generate trace ID for request."""
    return str(uuid.uuid4())


def get_user_id(request: Request, x_user_id: Optional[str] = Header(None)) -> str:
    """
    Extract user ID from request.
    Priority: 1) Bearer token (JWT), 2) X-User-Id header, 3) default-user.
    """
    # Try Bearer token first (same auth as /api/v2/ routes)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from plinth_v2.api.routes.auth import decode_token
            payload = decode_token(auth_header[7:])
            if payload and payload.get("sub"):
                return payload["sub"]
        except Exception:
            pass

    if x_user_id:
        return x_user_id
    # Fallback: use default user (for development)
    return "default-user"


# Legacy v1 endpoint (kept for backward compatibility)
@app.post("/run", response_model=PipelineOutput)
async def run_pipeline_endpoint(input_data: PipelineInput):
    """
    Execute the Plinth pipeline (v1).
    
    Returns final output and full trace.
    """
    try:
        # Convert Pydantic model to dict for pipeline (serialize dates as ISO strings)
        input_dict = input_data.model_dump(mode='json')
        
        # Execute pipeline
        result = run_pipeline(
            input_data=input_dict,
            brain_version="1.0.0",
            ruleset_version="1.0.0"
        )
        
        return PipelineOutput(**result)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# New v2 endpoints

@app.get("/api/session")
async def get_session(
    request: Request,
    x_user_id: Optional[str] = Header(None)
) -> APIEnvelope:
    """
    Get session state with onboarding flags.
    
    Returns server-truth flags mapped to frontend-friendly names:
    - questionnaire_complete (is_onboarded)
    - tone_complete (is_tone_set)
    - interview_complete (is_interviewed)
    - calendar_connected (calendar_connected or is_profile_linked)
    - social_linked (derived from social_accounts exists)
    """
    trace_id = get_trace_id()
    user_id = get_user_id(request, x_user_id)
    
    try:
        orchestrator = get_orchestrator()
        session = orchestrator.get_session(user_id)
        db_service = get_db_service()
        
        # Get real DB-backed flags
        flags = db_service.get_user_onboarding_flags(user_id)
        
        data = {
            "user_id": user_id,
            "questionnaire_complete": flags["questionnaire_complete"],
            "tone_complete": flags["tone_complete"],
            "interview_complete": flags["interview_complete"],
            "calendar_connected": flags["calendar_connected"],
            "social_linked": flags["social_linked"],
            "session": session.model_dump() if hasattr(session, 'model_dump') else {}
        }
        
        return APIEnvelope.success(data=data, trace_id=trace_id)
        
    except Exception as e:
        return APIEnvelope.error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"Failed to get session: {str(e)}",
            trace_id=trace_id
        )


@app.get("/api/hub/today")
async def get_today_hub(
    request: Request,
    date: Optional[str] = None,
    x_user_id: Optional[str] = Header(None)
):
    """
    Get Daily Creative Hub data for Today's Focus.

    Returns hub artefact compiled from Strategy, Behaviour, Memory, and Validation engines.
    The ``brief`` field flows through the behaviour engine pipeline and contains:
    topic, angle, hook, supporting_claims[], rationale, reinforcement_targets[],
    and claim_references[].
    """
    trace_id = get_trace_id()
    user_id = get_user_id(request, x_user_id)

    try:
        orchestrator = get_orchestrator()
        hub_data = orchestrator.get_today_hub(user_id=user_id, date=date)

        # Extract compiler meta (internal field, not part of hub data)
        compiler_meta = hub_data.pop("_compiler_meta", {})

        response_dict = APIEnvelope.success(data=hub_data, trace_id=trace_id).model_dump()
        response_dict["meta"]["compiler"] = compiler_meta
        return response_dict

    except Exception as e:
        return APIEnvelope.error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"Failed to get today hub: {str(e)}",
            trace_id=trace_id
        ).model_dump()


@app.get("/api/hub/brief")
async def get_today_brief(
    request: Request,
    energy: Optional[str] = "medium",
    count: Optional[int] = 3,
    exclude_topics: Optional[str] = None,
    x_user_id: Optional[str] = Header(None)
):
    """
    Get structured content brief(s) for the authenticated user.

    Standalone endpoint for the brief generator — use this when the frontend
    only needs the brief without the full hub artefact.

    Query params:
        exclude_topics: Comma-separated topics to exclude (previously rejected).
    """
    trace_id = get_trace_id()
    user_id = get_user_id(request, x_user_id)

    # Parse exclusion list from comma-separated string
    exclusions = [t.strip() for t in exclude_topics.split(",") if t.strip()] if exclude_topics else []

    try:
        orchestrator = get_orchestrator()
        result = orchestrator.generate_brief(
            user_id=user_id,
            energy=energy or "medium",
            count=min(count or 3, 5),  # Cap at 5
            exclude_topics=exclusions,
        )
        return APIEnvelope.success(data=result, trace_id=trace_id).model_dump()

    except Exception as e:
        return APIEnvelope.error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"Failed to generate brief: {str(e)}",
            trace_id=trace_id
        ).model_dump()


# ── Brief rejection tracking (in-memory per-session) ──
_brief_rejections: dict = {}  # { user_id: { "date": "YYYY-MM-DD", "rejected_topics": [...], "count": int } }

@app.post("/api/hub/brief/reject")
async def reject_brief(
    request: Request,
    body: dict,
    x_user_id: Optional[str] = Header(None)
):
    """
    Track a brief rejection. Frontend sends the rejected topic.
    Returns the updated rejection state for the session (today).

    Body: { "topic": "The rejected topic string" }
    Returns: { "rejected_topics": [...], "rejections_today": int, "alternatives_remaining": int }
    """
    from datetime import date as _date
    trace_id = get_trace_id()
    user_id = get_user_id(request, x_user_id)
    topic = body.get("topic", "").strip()

    if not topic:
        return APIEnvelope.error(
            code=ErrorCode.VALIDATION_ERROR,
            message="Missing 'topic' in request body",
            trace_id=trace_id
        ).model_dump()

    today_str = _date.today().isoformat()
    MAX_REJECTIONS_PER_DAY = 3

    # Initialize or reset for new day
    if user_id not in _brief_rejections or _brief_rejections[user_id]["date"] != today_str:
        _brief_rejections[user_id] = {"date": today_str, "rejected_topics": [], "count": 0}

    state = _brief_rejections[user_id]

    if state["count"] >= MAX_REJECTIONS_PER_DAY:
        return APIEnvelope.error(
            code=ErrorCode.VALIDATION_ERROR,
            message="Maximum 3 alternative briefs per day reached",
            trace_id=trace_id
        ).model_dump()

    # Track the rejection
    if topic not in state["rejected_topics"]:
        state["rejected_topics"].append(topic)
    state["count"] += 1

    return APIEnvelope.success(
        data={
            "rejected_topics": state["rejected_topics"],
            "rejections_today": state["count"],
            "alternatives_remaining": MAX_REJECTIONS_PER_DAY - state["count"],
        },
        trace_id=trace_id,
    ).model_dump()


@app.get("/api/hub/brief/rejections")
async def get_brief_rejections(
    request: Request,
    x_user_id: Optional[str] = Header(None)
):
    """Get today's rejection state for the user."""
    from datetime import date as _date
    trace_id = get_trace_id()
    user_id = get_user_id(request, x_user_id)
    today_str = _date.today().isoformat()

    state = _brief_rejections.get(user_id)
    if not state or state["date"] != today_str:
        return APIEnvelope.success(
            data={"rejected_topics": [], "rejections_today": 0, "alternatives_remaining": 3},
            trace_id=trace_id,
        ).model_dump()

    return APIEnvelope.success(
        data={
            "rejected_topics": state["rejected_topics"],
            "rejections_today": state["count"],
            "alternatives_remaining": max(0, 3 - state["count"]),
        },
        trace_id=trace_id,
    ).model_dump()


@app.post("/api/coach/chat")
async def coach_chat(
    request: Request,
    body: dict,
    x_user_id: Optional[str] = Header(None)
) -> APIEnvelope:
    """
    Chat with coach.
    
    Body:
    {
        "conversation_id": string (optional, backend generates if missing),
        "message": string,
        "images": [...] (optional)
    }
    
    Returns:
    {
        "conversation_id": string,
        "message_id": string,
        "responses": [{"model": string, "provider": string, "text": string, "meta": {...}}],
        "violations": [...] (optional),
        "decisions": [...] (optional)
    }
    """
    trace_id = get_trace_id()
    user_id = get_user_id(request, x_user_id)
    
    try:
        # Extract request body
        conversation_id = body.get("conversation_id")
        message = body.get("message")
        images = body.get("images", [])
        
        if not message:
            return APIEnvelope.error(
                code=ErrorCode.VALIDATION_ERROR,
                message="Message is required",
                trace_id=trace_id
            )
        
        orchestrator = get_orchestrator()
        chat_result = orchestrator.chat_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=message,
            attachments=images
        )
        
        return APIEnvelope.success(data=chat_result, trace_id=trace_id)
        
    except Exception as e:
        return APIEnvelope.error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"Failed to process chat: {str(e)}",
            trace_id=trace_id
        )


import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

