from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
import os
import requests
import json

DB_USER = os.getenv("DB_USER", "user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "cogniflow")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-3.5-turbo")

app = FastAPI(title="CogniFlow Backend", description="API do zbierania metryk i rekomendacji przerw.")

Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

class Metric(Base):
    __tablename__ = "metrics"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    keystrokes_per_min = Column(Integer)
    keystroke_density = Column(Float)
    mouse_moves_per_min = Column(Integer)
    perclos = Column(Float)
    head_roll_deg = Column(Float)
    idle_seconds = Column(Integer)
    window_switches = Column(Integer)

class Metrics(BaseModel):
    session_id: str
    keystrokes_per_min: int
    keystroke_density: float
    mouse_moves_per_min: int
    perclos: float
    head_roll_deg: float
    idle_seconds: int
    window_switches: int

@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)

# Dependency: sesja DB dla endpointów
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/metrics")
def post_metrics(metrics: Metrics, db: Session = Depends(get_db)):
    """
    Endpoint przyjmujący metryki co 10s z aplikacji desktopowej (JavaFX).
    Przykład danych JSON w ciele POST:
    {
      "session_id": "abc123",
      "keystrokes_per_min": 220,
      "keystroke_density": 0.64,
      "mouse_moves_per_min": 370,
      "perclos": 0.18,
      "head_roll_deg": 4.2,
      "idle_seconds": 12,
      "window_switches": 3
    }
    """
    db_entry = Metric(
        session_id=metrics.session_id,
        keystrokes_per_min=metrics.keystrokes_per_min,
        keystroke_density=metrics.keystroke_density,
        mouse_moves_per_min=metrics.mouse_moves_per_min,
        perclos=metrics.perclos,
        head_roll_deg=metrics.head_roll_deg,
        idle_seconds=metrics.idle_seconds,
        window_switches=metrics.window_switches
    )
    db.add(db_entry)
    db.commit()
    return {"status": "OK", "message": "Dane zapisane."}

@app.get("/recommendation")
def get_recommendation(session_id: str = Query(...), db: Session = Depends(get_db)):
    """
    Endpoint GET zwracający rekomendację na podstawie zebranych metryk.
    Zwraca JSON:
    {"status": "OK/WARN/ALERT", "message": "<krótka rekomendacja do 14 słów>"}
    """
    now = datetime.utcnow()
    five_min_ago = now - timedelta(minutes=5)
    thirty_min_ago = now - timedelta(minutes=30)

    avg_5 = db.query(
        func.avg(Metric.keystrokes_per_min).label("kpm"),
        func.avg(Metric.keystroke_density).label("kd"),
        func.avg(Metric.mouse_moves_per_min).label("mmp"),
        func.avg(Metric.perclos).label("perclos"),
        func.avg(Metric.head_roll_deg).label("hrd"),
        func.avg(Metric.idle_seconds).label("idle"),
        func.avg(Metric.window_switches).label("win")
    ).filter(
        Metric.session_id == session_id,
        Metric.timestamp >= five_min_ago
    ).first()

    avg_30 = db.query(
        func.avg(Metric.keystrokes_per_min).label("kpm"),
        func.avg(Metric.keystroke_density).label("kd"),
        func.avg(Metric.mouse_moves_per_min).label("mmp"),
        func.avg(Metric.perclos).label("perclos"),
        func.avg(Metric.head_roll_deg).label("hrd"),
        func.avg(Metric.idle_seconds).label("idle"),
        func.avg(Metric.window_switches).label("win")
    ).filter(
        Metric.session_id == session_id,
        Metric.timestamp >= thirty_min_ago
    ).first()

    if not avg_5 or not avg_30:
        raise HTTPException(status_code=404, detail="Brak wystarczających danych dla podanego session_id")

    avg_5_vals = {
        "kpm": avg_5.kpm or 0, "kd": avg_5.kd or 0,
        "mmp": avg_5.mmp or 0, "perclos": avg_5.perclos or 0,
        "hrd": avg_5.hrd or 0, "idle": avg_5.idle or 0,
        "win": avg_5.win or 0
    }
    avg_30_vals = {
        "kpm": avg_30.kpm or 0, "kd": avg_30.kd or 0,
        "mmp": avg_30.mmp or 0, "perclos": avg_30.perclos or 0,
        "hrd": avg_30.hrd or 0, "idle": avg_30.idle or 0,
        "win": avg_30.win or 0
    }

    score_5 = (
        (avg_5_vals["kpm"] / 200.0) * 0.2 +
        (avg_5_vals["mmp"] / 300.0) * 0.2 +
        (avg_5_vals["idle"] / 60.0) * 0.2 +
        (avg_5_vals["perclos"]) * 0.2 +
        (avg_5_vals["hrd"] / 30.0) * 0.2
    ) * 100
    score_30 = (
        (avg_30_vals["kpm"] / 200.0) * 0.2 +
        (avg_30_vals["mmp"] / 300.0) * 0.2 +
        (avg_30_vals["idle"] / 60.0) * 0.2 +
        (avg_30_vals["perclos"]) * 0.2 +
        (avg_30_vals["hrd"] / 30.0) * 0.2
    ) * 100

    score = max(0, min(100, score_5))
    fatigue_delta = score_5 - score_30

    if score < 40:
        label = "OK"
    elif score < 70:
        label = "WARN"
    else:
        label = "ALERT"

    prompt = (
        f"Na podstawie poniższych danych o aktywności użytkownika (sesja {session_id})\n"
        f"średnie w ostatnich 5 min:\n"
        f"- KPM (naciśnięć/min): {avg_5_vals['kpm']:.1f}\n"
        f"- Gęstość naciśnięć: {avg_5_vals['kd']:.2f}\n"
        f"- Ruchy myszy: {avg_5_vals['mmp']:.1f}\n"
        f"- PERCL: {avg_5_vals['perclos']:.2f}\n"
        f"- Obrót głowy: {avg_5_vals['hrd']:.1f}°\n"
        f"- Bezczynność (s): {avg_5_vals['idle']:.1f}\n"
        f"- Przełączenia okien: {avg_5_vals['win']:.1f}\n\n"
        f"Podaj krótką (<=14 słów) rekomendację przerwy w języku polskim."
    )

    message = None
    if OPENROUTER_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}]
            }
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                data=json.dumps(data)
            )
            if response.ok:
                result = response.json()
                message = result["choices"][0]["message"]["content"].strip().strip('"')
        except Exception:
            message = None

    if not message:
        if label == "OK":
            message = "Kontynuuj pracę, pamiętaj o krótkich przerwach."
        elif label == "WARN":
            message = "Zrób krótką przerwę, rozciągnij się i weź głęboki oddech."
        else:  # ALERT
            message = "Zrób dłuższą przerwę, odpocznij i przewietrz się."
        message = " ".join(message.split()[:14])

    return {"status": label, "message": message}