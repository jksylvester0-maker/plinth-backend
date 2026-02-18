"""
Minimal but functional FastAPI backend for Plinth app
Single-file implementation with SQLite, JWT, and mock data
"""

import os
import sqlite3
import json
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Depends, status, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from jose import JWTError, jwt
import uvicorn


# ============================================================================
# Configuration
# ============================================================================

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24
REFRESH_TOKEN_EXPIRY_DAYS = 7

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else []
DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:8080",
    "https://project-haven-pi.vercel.app",
]
CORS_ORIGINS = [o.strip() for o in CORS_ORIGINS if o.strip()] or DEFAULT_CORS_ORIGINS

# ============================================================================
# Database Setup
# ============================================================================

DB_PATH = "/tmp/plinth_users.db"

def init_db():
    """Initialize SQLite database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            onboarding_flags TEXT,
            questionnaire_data TEXT,
            tone_data TEXT,
            onboarding_data TEXT
        )
    """)
    # Add onboarding_data column if it doesn't exist (migration)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN onboarding_data TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    conn.close()

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============================================================================
# Password & JWT Utilities
# ============================================================================

def hash_password(password: str) -> str:
    """Hash password with SHA-256 + random salt"""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"

def verify_password(plain: str, hashed: str) -> bool:
    """Verify password against stored hash"""
    try:
        salt, h = hashed.split("$", 1)
        return hashlib.sha256((salt + plain).encode()).hexdigest() == h
    except (ValueError, AttributeError):
        return False

def create_tokens(user_id: str) -> Dict[str, str]:
    """Create access and refresh tokens"""
    now = datetime.now(timezone.utc)

    access_payload = {
        "sub": user_id,
        "exp": now + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": now,
        "type": "access",
    }
    access_token = jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    refresh_payload = {
        "sub": user_id,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS),
        "iat": now,
        "type": "refresh",
    }
    refresh_token = jwt.encode(refresh_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": user_id,
    }

def verify_token(token: str) -> str:
    """Verify token and return user_id"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    """Extract user_id from Authorization header"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    token = authorization.split(" ", 1)[1]
    return verify_token(token)

def get_user_onboarding(user_id: str) -> Optional[Dict[str, Any]]:
    """Get user's onboarding data if it exists"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT onboarding_data FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    db.close()
    if row and row["onboarding_data"]:
        try:
            return json.loads(row["onboarding_data"])
        except (json.JSONDecodeError, TypeError):
            return None
    return None

# ============================================================================
# Pydantic Models
# ============================================================================

class AuthRequest(BaseModel):
    email: str
    password: str

class AuthResponse(BaseModel):
    ok: bool
    data: Dict[str, Any]
    meta: Dict[str, str] = Field(default_factory=lambda: {"trace_id": str(uuid4())})

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class SessionResponse(BaseModel):
    ok: bool
    data: Dict[str, Any]
    meta: Dict[str, str] = Field(default_factory=lambda: {"trace_id": str(uuid4())})

class DataEnvelopeResponse(BaseModel):
    ok: bool
    data: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, str] = Field(default_factory=lambda: {"trace_id": str(uuid4())})

class ChatRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None

# ============================================================================
# App Setup
# ============================================================================

app = FastAPI(title="Plinth Backend", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database on startup
init_db()

# ============================================================================
# Authentication Endpoints
# ============================================================================

@app.post("/api/v2/auth/register")
def register(req: AuthRequest) -> AuthResponse:
    """Register a new user"""
    db = get_db()
    cursor = db.cursor()

    # Check if user exists
    cursor.execute("SELECT user_id FROM users WHERE email = ?", (req.email,))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="User already exists")

    # Create user
    user_id = str(uuid4())
    password_hash = hash_password(req.password)
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute("""
        INSERT INTO users (user_id, email, password_hash, created_at, onboarding_flags)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, req.email, password_hash, now, json.dumps({
        "completed_questionnaire": False,
        "selected_tone": False,
        "reviewed_brief": False,
    })))
    db.commit()
    db.close()

    tokens = create_tokens(user_id)
    return AuthResponse(
        ok=True,
        data=tokens,
    )

@app.post("/api/v2/auth/login")
def login(req: AuthRequest) -> AuthResponse:
    """Login a user"""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT user_id, password_hash FROM users WHERE email = ?", (req.email,))
    row = cursor.fetchone()
    db.close()

    if not row or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id = row["user_id"]
    tokens = create_tokens(user_id)
    return AuthResponse(
        ok=True,
        data=tokens,
    )

@app.get("/api/v2/auth/me")
def get_me(user_id: str = Depends(get_current_user)):
    """Get current user info including onboarding status"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT user_id, email, created_at, onboarding_data FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    db.close()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    onboarding_data = None
    onboarding_completed = False
    if row["onboarding_data"]:
        try:
            onboarding_data = json.loads(row["onboarding_data"])
            onboarding_completed = True
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "user_id": row["user_id"],
        "email": row["email"],
        "created_at": row["created_at"],
        "onboarding_completed": onboarding_completed,
        "onboarding_data": onboarding_data,
    }

@app.post("/api/v2/auth/refresh")
def refresh_token(req: RefreshTokenRequest) -> AuthResponse:
    """Refresh access token"""
    user_id = verify_token(req.refresh_token)
    tokens = create_tokens(user_id)
    return AuthResponse(
        ok=True,
        data=tokens,
    )

# ============================================================================
# Onboarding Endpoints
# ============================================================================

@app.post("/api/v2/onboarding/complete")
def complete_onboarding(data: Optional[Dict[str, Any]] = None, user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Save onboarding calibration data"""
    if not data:
        raise HTTPException(status_code=400, detail="No calibration data provided")

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE users SET onboarding_data = ?, onboarding_flags = ? WHERE user_id = ?",
        (json.dumps(data), json.dumps({"completed_questionnaire": True, "selected_tone": True, "reviewed_brief": False}), user_id)
    )
    db.commit()
    db.close()

    return DataEnvelopeResponse(
        ok=True,
        data={"saved": True, "onboarding_completed": True},
    )

@app.post("/api/v2/onboarding/questionnaire")
def save_questionnaire(data: Optional[Dict[str, Any]] = None) -> DataEnvelopeResponse:
    """Save onboarding questionnaire"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "saved": True,
            "questionnaire_id": str(uuid4()),
            "topics": data.get("topics", []) if data else [],
            "positioning": data.get("positioning", "") if data else "",
        },
    )

@app.post("/api/v2/onboarding/tone")
def save_tone(data: Optional[Dict[str, Any]] = None) -> DataEnvelopeResponse:
    """Save tone preferences"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "saved": True,
            "tone_markers": data.get("tone_markers", []) if data else [],
            "boundaries": data.get("boundaries", []) if data else [],
        },
    )

# ============================================================================
# Session & Hub Endpoints (Personalized)
# ============================================================================

@app.get("/api/session")
def get_session() -> SessionResponse:
    """Get session data"""
    return SessionResponse(
        ok=True,
        data={
            "user_id": str(uuid4()),
            "email": "user@example.com",
            "onboarding_flags": {
                "completed_questionnaire": False,
                "selected_tone": False,
                "reviewed_brief": False,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

@app.get("/api/hub/today")
def get_hub_today(user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Get today's hub data — personalized if onboarding is complete"""
    ob = get_user_onboarding(user_id)

    if ob:
        positioning = ob.get("positioning_target", "your expertise")
        topics = ob.get("content_territories", ob.get("core_ideas", []))
        audience = ob.get("audience_description", "your audience")
        first_topic = topics[0] if topics else "your core topic"
        second_topic = topics[1] if len(topics) > 1 else "your expertise"
        return DataEnvelopeResponse(
            ok=True,
            data={
                "date": datetime.now(timezone.utc).date().isoformat(),
                "personalized": True,
                "brief": {
                    "topic": first_topic,
                    "angle": f"Reinforce your positioning in {positioning}",
                    "hook": f"Share your perspective on {first_topic} — your audience needs this from you today",
                    "supporting_claims": ob.get("core_ideas", [])[:3],
                    "rationale": f"This reinforces your authority in {positioning} for {audience}",
                },
                "strategy_snapshot": {
                    "positioning": positioning,
                    "recommended_focus": topics[:3] if topics else ["Define your territories"],
                    "active_signals": [f"Reinforce {t}" for t in (topics[:2] if topics else ["your positioning"])],
                    "territory_coverage": min(len(topics) * 15, 100) if topics else 0,
                },
                "memory_state": {
                    "total_territories": len(topics) if topics else 0,
                    "active_claims": len(ob.get("core_ideas", [])),
                    "reinforcement_count": 0,
                },
                "engagement_summary": {
                    "messages_today": 0,
                    "conversations_active": 0,
                    "voice_consistency": 0,
                },
            },
        )

    # Default for users who haven't onboarded
    return DataEnvelopeResponse(
        ok=True,
        data={
            "date": datetime.now(timezone.utc).date().isoformat(),
            "personalized": False,
            "brief": {
                "topic": "Complete your brand setup",
                "angle": "Get started",
                "hook": "Set up your brand identity to unlock personalized strategic briefs",
                "supporting_claims": [],
                "rationale": "Complete onboarding to receive tailored daily briefs",
            },
            "strategy_snapshot": {
                "positioning": "Not yet configured",
                "recommended_focus": ["Complete brand setup"],
                "active_signals": [],
                "territory_coverage": 0,
            },
            "memory_state": {
                "total_territories": 0,
                "active_claims": 0,
                "reinforcement_count": 0,
            },
            "engagement_summary": {
                "messages_today": 0,
                "conversations_active": 0,
                "voice_consistency": 0,
            },
        },
    )

@app.get("/api/hub/brief")
def get_brief(user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Get brief data — personalized"""
    ob = get_user_onboarding(user_id)

    if ob:
        positioning = ob.get("positioning_target", "your expertise")
        topics = ob.get("content_territories", ob.get("core_ideas", []))
        first_topic = topics[0] if topics else positioning
        return DataEnvelopeResponse(
            ok=True,
            data={
                "topic": first_topic,
                "subtopic": positioning,
                "angle": f"Strengthen your authority in {first_topic}",
                "hook": f"Share your unique perspective on {first_topic} to reinforce your positioning",
                "supporting_claims": ob.get("core_ideas", []),
                "rationale": f"Publishing on {first_topic} reinforces your positioning in {positioning}",
                "key_messages": ob.get("core_ideas", [])[:3],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            },
        )

    return DataEnvelopeResponse(
        ok=True,
        data={
            "topic": "Set up your brand",
            "subtopic": "Getting started",
            "angle": "Complete onboarding",
            "hook": "Configure your brand identity to unlock personalized briefs",
            "supporting_claims": [],
            "rationale": "Complete the brand setup to receive strategic daily briefs",
            "key_messages": ["Complete your brand setup to get started"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        },
    )

# ============================================================================
# Memory Endpoints (Personalized)
# ============================================================================

@app.get("/api/v2/memory/state")
def get_memory_state(user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Get memory state — personalized"""
    ob = get_user_onboarding(user_id)

    if ob:
        topics = ob.get("content_territories", ob.get("core_ideas", []))
        territories = []
        for i, t in enumerate(topics[:6]):
            territories.append({
                "id": f"t{i+1}",
                "name": t,
                "claims_count": 0,
                "reinforcement_score": 0,
            })
        return DataEnvelopeResponse(
            ok=True,
            data={
                "territories": territories,
                "total_territories": len(territories),
                "total_claims": len(ob.get("core_ideas", [])),
                "total_reinforcements": 0,
            },
        )

    return DataEnvelopeResponse(
        ok=True,
        data={
            "territories": [],
            "total_territories": 0,
            "total_claims": 0,
            "total_reinforcements": 0,
        },
    )

@app.get("/api/v2/memory/reinforcement/counts")
def get_reinforcement_counts(user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Get reinforcement counts"""
    ob = get_user_onboarding(user_id)
    by_territory = {}
    if ob:
        for t in ob.get("content_territories", [])[:6]:
            by_territory[t] = 0
    return DataEnvelopeResponse(
        ok=True,
        data={
            "weekly": 0,
            "monthly": 0,
            "all_time": 0,
            "by_territory": by_territory,
        },
    )

@app.get("/api/v2/memory/territory/coverage")
def get_territory_coverage(user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Get territory coverage"""
    ob = get_user_onboarding(user_id)
    territories = []
    if ob:
        for t in ob.get("content_territories", [])[:6]:
            territories.append({
                "name": t,
                "coverage": 0,
                "last_reinforced": None,
            })
    return DataEnvelopeResponse(
        ok=True,
        data={
            "coverage_percentage": 0,
            "territories": territories,
        },
    )

# ============================================================================
# Strategy Endpoints (Personalized)
# ============================================================================

@app.get("/api/v2/strategy")
def get_strategy(user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Get strategy data — personalized"""
    ob = get_user_onboarding(user_id)

    if ob:
        positioning = ob.get("positioning_target", "your expertise")
        audience = ob.get("audience_description", "your audience")
        topics = ob.get("content_territories", [])
        core_ideas = ob.get("core_ideas", [])
        return DataEnvelopeResponse(
            ok=True,
            data={
                "positioning": positioning,
                "target_audience": audience,
                "recommended_focus": topics[:3] if topics else [],
                "active_signals": [f"Reinforce: {c}" for c in core_ideas[:4]],
                "territory_allocation": {t: round(100 / max(len(topics), 1)) for t in topics[:5]},
                "messaging_pillars": core_ideas[:3],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    return DataEnvelopeResponse(
        ok=True,
        data={
            "positioning": "Not yet configured",
            "target_audience": "Complete brand setup",
            "recommended_focus": [],
            "active_signals": [],
            "territory_allocation": {},
            "messaging_pillars": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

# ============================================================================
# Chat Endpoints
# ============================================================================

@app.post("/api/v2/chat/context")
def get_chat_context(data: Optional[Dict[str, Any]] = None) -> DataEnvelopeResponse:
    """Get chat context"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "current_brief": {"topic": "Your brand strategy", "angle": "Personalized"},
            "recent_territories": [],
            "message_history_count": 0,
            "conversation_context": "Strategic coaching session",
        },
    )

@app.post("/api/coach/chat")
def coach_chat(req: ChatRequest, user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Chat with coach — personalized"""
    ob = get_user_onboarding(user_id)
    message = req.message.lower()

    if ob:
        positioning = ob.get("positioning_target", "your expertise")
        topics = ob.get("content_territories", [])
        core_ideas = ob.get("core_ideas", [])

        if "brief" in message:
            response_text = f"Your current focus is on reinforcing your positioning in {positioning}. Would you like to explore specific angles?"
        elif "memory" in message or "reinforce" in message:
            response_text = f"You have {len(topics)} territories and {len(core_ideas)} core ideas configured. Let's plan your reinforcement strategy."
        elif "strategy" in message:
            response_text = f"Your positioning as an authority in {positioning} is your strategic foundation. Which territory would you like to strengthen first?"
        else:
            topic_list = ", ".join(topics[:3]) if topics else "your key topics"
            response_text = f"I'm here to help you build authority in {positioning}. Your territories include {topic_list}. What would you like to work on?"
    else:
        response_text = "Welcome to Plinth! Complete your brand setup first to unlock personalized coaching. Head to Setup to configure your brand identity."

    return DataEnvelopeResponse(
        ok=True,
        data={
            "message": response_text,
            "suggestions": ["Review your brief", "Check memory coverage", "Refine strategy focus"],
            "context": req.context or {},
        },
    )

# ============================================================================
# Voice Endpoints
# ============================================================================

@app.get("/api/v2/voice/profile")
def get_voice_profile(user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Get voice profile — personalized"""
    ob = get_user_onboarding(user_id)

    if ob:
        voice_style = ob.get("voice_style", "Professional")
        return DataEnvelopeResponse(
            ok=True,
            data={
                "tone_markers": [voice_style, "Authentic", "Strategic"],
                "boundaries": ob.get("integrity_boundaries", {}).get("values_protect", []),
                "examples": [],
                "consistency_score": 0,
                "consistency_trend": "new",
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
        )

    return DataEnvelopeResponse(
        ok=True,
        data={
            "tone_markers": [],
            "boundaries": [],
            "examples": [],
            "consistency_score": 0,
            "consistency_trend": "new",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        },
    )

# ============================================================================
# Draft Endpoints
# ============================================================================

@app.post("/api/v2/drafts/generate")
def generate_draft(data: Optional[Dict[str, Any]] = None, user_id: str = Depends(get_current_user)) -> DataEnvelopeResponse:
    """Generate a draft — personalized"""
    ob = get_user_onboarding(user_id)

    if ob:
        positioning = ob.get("positioning_target", "your expertise")
        topics = ob.get("content_territories", ob.get("core_ideas", []))
        first_topic = topics[0] if topics else positioning
        core_ideas = ob.get("core_ideas", [])
        claims_text = "\n\n".join([f"- {c}" for c in core_ideas[:3]]) if core_ideas else ""

        content = f"""Here's a draft focused on {first_topic} to reinforce your positioning in {positioning}.

Your key messages to reinforce:
{claims_text}

[This is a structural draft. Connect your personal experience and insights to these core ideas to make it authentic and compelling.]"""
    else:
        content = "Complete your brand setup to generate personalized drafts."

    return DataEnvelopeResponse(
        ok=True,
        data={
            "draft_id": str(uuid4()),
            "type": "article",
            "title": f"Draft: {ob.get('positioning_target', 'Your Topic') if ob else 'Set up your brand'}",
            "content": content,
            "territories_covered": ob.get("content_territories", [])[:3] if ob else [],
            "tone_alignment": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

@app.post("/api/v2/drafts/validate")
def validate_draft(data: Optional[Dict[str, Any]] = None) -> DataEnvelopeResponse:
    """Validate a draft"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "is_valid": True,
            "tone_alignment_score": 0,
            "brief_alignment_score": 0,
            "territory_coverage_score": 0,
            "issues": [],
            "suggestions": ["Continue building your content library"],
        },
    )

# ============================================================================
# Health Check
# ============================================================================

@app.get("/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}

# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
