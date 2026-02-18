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
from typing import Optional, Dict, Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Depends, status
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
            tone_data TEXT
        )
    """)
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

app = FastAPI(title="Plinth Backend", version="1.0.0")

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
# Session & Hub Endpoints
# ============================================================================

@app.get("/api/session")
def get_session() -> SessionResponse:
    """Get session data with onboarding flags"""
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
def get_hub_today() -> DataEnvelopeResponse:
    """Get today's hub data"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "date": datetime.now(timezone.utc).date().isoformat(),
            "brief": {
                "topic": "AI Regulation in Tech",
                "angle": "Executive Perspective",
                "hook": "New EU AI Act enforcement signals market shift",
                "supporting_claims": [
                    "EU fines tech companies $1B+ for compliance violations",
                    "Fortune 500 increasing AI governance investment",
                    "Enterprise adoption accelerating despite regulatory concerns",
                ],
                "rationale": "Regulatory landscape is tightening, creating urgency for compliance",
            },
            "strategy_snapshot": {
                "positioning": "Thought Leader in Responsible AI",
                "recommended_focus": ["Governance", "Compliance", "Ethics"],
                "active_signals": ["EU Action", "Enterprise Demand", "Safety Focus"],
                "territory_coverage": 65,
            },
            "memory_state": {
                "total_territories": 12,
                "active_claims": 18,
                "reinforcement_count": 42,
            },
            "engagement_summary": {
                "messages_today": 5,
                "conversations_active": 2,
                "voice_consistency": 94,
            },
        },
    )

@app.get("/api/hub/brief")
def get_brief() -> DataEnvelopeResponse:
    """Get brief data"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "topic": "AI Regulation in Tech",
            "subtopic": "Enterprise Compliance Strategies",
            "angle": "Executive Perspective",
            "hook": "New EU AI Act enforcement signals market shift for enterprise tech leaders",
            "supporting_claims": [
                "EU fines tech companies $1B+ for compliance violations in 2024",
                "Fortune 500 companies increasing AI governance budgets by 150%",
                "Enterprise adoption rates up 40% despite regulatory concerns",
                "Compliance becomes competitive advantage in B2B sales",
                "In-house AI governance teams now standard at major firms",
            ],
            "rationale": "Enterprise leaders need pragmatic compliance strategies to capitalize on regulatory clarity",
            "key_messages": [
                "Regulation creates opportunities for compliant innovators",
                "Early movers gain competitive advantage in enterprise markets",
                "Governance maturity is now a boardroom priority",
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        },
    )

# ============================================================================
# Memory Endpoints
# ============================================================================

@app.get("/api/v2/memory/state")
def get_memory_state() -> DataEnvelopeResponse:
    """Get memory state"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "territories": [
                {
                    "id": "t1",
                    "name": "AI Governance",
                    "claims_count": 8,
                    "reinforcement_score": 92,
                },
                {
                    "id": "t2",
                    "name": "Enterprise Risk Management",
                    "claims_count": 5,
                    "reinforcement_score": 78,
                },
                {
                    "id": "t3",
                    "name": "Regulatory Compliance",
                    "claims_count": 5,
                    "reinforcement_score": 85,
                },
            ],
            "total_territories": 3,
            "total_claims": 18,
            "total_reinforcements": 127,
        },
    )

@app.get("/api/v2/memory/reinforcement/counts")
def get_reinforcement_counts() -> DataEnvelopeResponse:
    """Get reinforcement counts"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "weekly": 32,
            "monthly": 127,
            "all_time": 342,
            "by_territory": {
                "AI Governance": 48,
                "Enterprise Risk Management": 31,
                "Regulatory Compliance": 48,
            },
        },
    )

@app.get("/api/v2/memory/territory/coverage")
def get_territory_coverage() -> DataEnvelopeResponse:
    """Get territory coverage"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "coverage_percentage": 65,
            "territories": [
                {
                    "name": "AI Governance",
                    "coverage": 87,
                    "last_reinforced": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                },
                {
                    "name": "Enterprise Risk Management",
                    "coverage": 72,
                    "last_reinforced": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                },
                {
                    "name": "Regulatory Compliance",
                    "coverage": 66,
                    "last_reinforced": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
                },
            ],
        },
    )

# ============================================================================
# Strategy Endpoints
# ============================================================================

@app.get("/api/v2/strategy")
def get_strategy() -> DataEnvelopeResponse:
    """Get strategy data"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "positioning": "Thought Leader in Responsible AI Implementation",
            "target_audience": "Enterprise CTOs and AI Governance Leaders",
            "recommended_focus": [
                "Governance Frameworks",
                "Risk Mitigation",
                "Compliance Automation",
            ],
            "active_signals": [
                "EU AI Act Enforcement",
                "Enterprise Demand for Governance",
                "Board-Level Scrutiny of AI",
                "Insurance Market Growth for AI Risk",
            ],
            "territory_allocation": {
                "AI Governance": 40,
                "Enterprise Risk Management": 35,
                "Regulatory Compliance": 25,
            },
            "messaging_pillars": [
                "Regulation creates competitive advantage for prepared leaders",
                "Governance maturity is a boardroom priority",
                "Proactive compliance beats reactive firefighting",
            ],
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
            "current_brief": {
                "topic": "AI Regulation in Tech",
                "angle": "Executive Perspective",
            },
            "recent_territories": [
                "AI Governance",
                "Enterprise Risk Management",
            ],
            "message_history_count": 12,
            "conversation_context": "Discussing enterprise AI governance strategies",
        },
    )

@app.post("/api/coach/chat")
def coach_chat(req: ChatRequest) -> DataEnvelopeResponse:
    """Chat with coach"""
    message = req.message.lower()

    # Simple intent routing
    if "brief" in message:
        response_text = "Your current brief focuses on AI governance in enterprise. Would you like to explore specific angles or supporting claims?"
    elif "memory" in message or "reinforce" in message:
        response_text = "You've reinforced AI Governance 48 times this month. Your memory coverage is at 65%. Consider deepening your claim library in Enterprise Risk Management."
    elif "strategy" in message:
        response_text = "Your positioning as a thought leader in responsible AI is strong. Focus on the compliance-as-advantage angle—enterprise CTOs are increasingly receptive."
    elif "voice" in message or "tone" in message:
        response_text = "Your voice consistency is at 94%. Maintain your authoritative yet approachable tone when discussing governance frameworks."
    else:
        response_text = "I'm here to help you strengthen your positioning. What aspect of your brand intelligence would you like to explore—your brief, memory territories, strategy, or voice?"

    return DataEnvelopeResponse(
        ok=True,
        data={
            "message": response_text,
            "suggestions": [
                "Review your brief",
                "Check memory coverage",
                "Refine strategy focus",
            ],
            "context": req.context or {},
        },
    )

# ============================================================================
# Voice Endpoints
# ============================================================================

@app.get("/api/v2/voice/profile")
def get_voice_profile() -> DataEnvelopeResponse:
    """Get voice profile"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "tone_markers": [
                "Authoritative",
                "Pragmatic",
                "Insightful",
                "Professional",
                "Accessible",
            ],
            "boundaries": [
                "Avoid speculation",
                "Ground claims in evidence",
                "Balance optimism with realism",
                "Respect regulatory complexity",
            ],
            "examples": [
                "Enterprise AI governance is no longer optional—it's a competitive advantage.",
                "The regulation creates clarity. Smart organizations are moving faster, not slower.",
            ],
            "consistency_score": 94,
            "consistency_trend": "improving",
            "last_updated": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        },
    )

# ============================================================================
# Draft Endpoints
# ============================================================================

@app.post("/api/v2/drafts/generate")
def generate_draft(data: Optional[Dict[str, Any]] = None) -> DataEnvelopeResponse:
    """Generate a draft"""
    return DataEnvelopeResponse(
        ok=True,
        data={
            "draft_id": str(uuid4()),
            "type": "article",
            "title": "Why Enterprise Leaders Are Embracing AI Governance",
            "content": """The EU AI Act is often framed as a burden, but savvy enterprise leaders see it differently.

Regulation creates clarity. When rules are clear, those who move first gain advantages. Enterprise CTOs are increasingly recognizing that governance maturity isn't an obstacle to innovation—it's a prerequisite for scaling AI safely.

Consider the numbers: Fortune 500 companies are increasing AI governance budgets by 150%. Board-level scrutiny of AI is at an all-time high. And here's the key insight: the companies moving fastest to implement governance frameworks are also the ones capturing the most value.

The regulation isn't slowing innovation. It's accelerating the separation between leaders and laggards.

For enterprise organizations, the play is clear: build governance maturity now, and you'll have a two-year head start on competitors who delay.""",
            "brief_reference": "AI Regulation in Tech",
            "territories_covered": ["AI Governance", "Enterprise Risk Management"],
            "tone_alignment": 96,
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
            "tone_alignment_score": 94,
            "brief_alignment_score": 92,
            "territory_coverage_score": 88,
            "issues": [],
            "suggestions": [
                "Consider adding more specific enterprise examples",
                "Strong voice consistency throughout",
            ],
        },
    )

# ============================================================================
# Onboarding Endpoints
# ============================================================================

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
