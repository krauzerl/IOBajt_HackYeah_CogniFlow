# CogniFlow – Backend & Database (README – draft)

## Overview
CogniFlow to środowisko backendowe do zbierania metryk aktywności użytkownika i generowania krótkich rekomendacji przerw. Składa się z:
- Backend API (FastAPI, Python/uvicorn) – przyjmuje metryki i zwraca rekomendacje.
- Bazy danych (PostgreSQL) – przechowuje zdarzenia oraz agregaty metryk.
- (Klient) Aplikacja desktop (JavaFX) zbiera dane lokalnie i:
  - cyklicznie wysyła metryki baz danych PostgreSQL,
  - co ~30 min wyświetla powiadomienie o aktualnym stanie zmęczenia i koncentracji na podstawie `GET /recommendation`.
- Python LC / OpenRouter do generowania treści rekomendacji; w razie braku klucza działa heurystyka.

Środowisko uruchamiane jest w Dockerze i udostępniane w sieci VPN (bez publicznej ekspozycji).

---

## Architektura i adresy
- VM (Ubuntu/Debian) z IP: `192.168.1.226` - Został użyty własny prywatny serwer oraz VPN.
- Backend: HTTP :8000
- PostgreSQL: :5432 (udostępniany w LAN/VPN wg konfiguracji Compose)

Przykładowe URL:
- API docs: `http://192.168.1.226:8000/docs`
- Rekomendacja: `http://192.168.1.226:8000/recommendation?session_id=16`

---

## Endpointy API (skrót)
- `POST /metrics` – zapis metryk sesji:
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
- `GET /recommendation` – zwraca status (`OK|WARN|ALERT`) i krótką wiadomość (LLM lub heurystyka).

---

## Zmienne środowiskowe (.env)
DB_USER=cogniflowuser
DB_PASSWORD=strongpassword
DB_HOST=db
DB_PORT=5432
DB_NAME=cogniflowdb
OPENROUTER_API_KEY=(LLM Api key)
OPENROUTER_MODEL=openai/gpt-3.5-turbo
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

> Dla innych usług w tym samym `docker-compose` używamy `DB_HOST=db`.
> Dla klientów spoza dockera (np. JavaFX lokalnie) użyj `DB_HOST=192.168.1.226` i odpowiedniej mapy portów w Compose (sekcja niżej).

---

## Wdrożenie (Docker)
1) Instalacja Dockera i Compose (Ubuntu/Debian)

sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
| sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

2) Struktura projektu

/opt/cogniflow/
  ├─ main.py
  ├─ requirements.txt
  ├─ Dockerfile
  ├─ docker-compose.yml
  └─ .env

3) Konfiguracja `docker-compose.yml` (ważne fragmenty)
- Backend wystaw na IP VM:

services:
  backend:
    build: .
    ports:
      - "192.168.1.226:8000:8000"
    env_file: .env
    depends_on: [db]
    restart: unless-stopped

  db:
    image: postgres:15
    environment:
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: ${DB_NAME}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    # Dostęp do bazy z zewnątrz (np. z aplikacji desktop/IntelliJ po VPN):
    ports:
      - "192.168.1.226:5432:5432"
    restart: unless-stopped

volumes:
  postgres_data:

> Jeśli nie potrzebujesz zewnętrznego dostępu do bazy, usuń mapowanie `5432`.

4) Uruchomienie

cd /opt/cogniflow
sudo docker compose up -d --build

---

## Testy
API

curl http://192.168.1.226:8000/docs
curl -X POST http://192.168.1.226:8000/metrics \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test123","keystrokes_per_min":200,"keystroke_density":0.6,"mouse_moves_per_min":300,"perclos":0.1,"head_roll_deg":5,"idle_seconds":10,"window_switches":3}'
curl http://192.168.1.226:8000/recommendation?session_id=16

DB (z innego urządzenia po VPN)
- Windows PowerShell:
  Test-NetConnection 192.168.1.226 -Port 5432
- psql:
  psql -h 192.168.1.226 -U cogniflowuser -d cogniflowdb
  \dt
  SELECT COUNT(*) FROM metrics;

---

## Logi i diagnostyka
- Backend (HTTP żądania, błędy):
  sudo docker compose logs -f backend
- Baza (połączenia, błędy):
  sudo docker compose logs -f db
- (Opcjonalnie) pełne logowanie zapytań w Postgres: w `postgresql.conf` ustaw `logging_collector=on`, `log_statement='all'`, restart kontenera.

---

## Integracja z aplikacją desktop (JavaFX)
- Aplikacja:
  - zbiera zdarzenia klawiatury/myszy i (opcjonalnie) sygnały wideo,
  - cyklicznie wysyła metryki do bazy danych cogniflowdb,
  - co ok. 30 minut pokazuje użytkownikowi powiadomienie (stan zmęczenia/koncentracji) pobrane z `GET /recommendation`.
- Połączenie DB (opcjonalnie z narzędzi/IDE): JDBC
  jdbc:postgresql://192.168.1.226:5432/cogniflowdb (user: cogniflowuser, pass: strongpassword).

---

## Bezpieczeństwo
- Dostęp przez VPN – brak ekspozycji publicznej.
- Jeśli musisz otworzyć `5432`, ogranicz regułami firewall (np. tylko z podsieci VPN).
- Nie przechowuj wrażliwych treści w surowych danych klawiatury (rozważ anonimizację).

---

## Typowe problemy i rozwiązania
- `GET /recommendation` → 404: w bazie nie ma jeszcze danych dla ostatniej sesji. Najpierw wyślij metryki przez `POST /metrics`.
- `connection refused` do DB przy starcie: backend ruszył szybciej niż Postgres. Dodaj krótki delay (command: "sh -c 'sleep 5 && uvicorn main:app --host 0.0.0.0 --port 8000'" ) lub użyj skryptu wait-for-it.
- `Invalid HTTP request received` w logach: błędne żądania (np. próba HTTPS na porcie 8000). Używaj `http://`, nie `https://`.

---

## Utrzymanie
- Autostart: `restart: unless-stopped` w `docker-compose.yml`.
- Aktualizacja:
  git pull / skopiuj nowe pliki
  sudo docker compose build
  sudo docker compose up -d
- Kopie zapasowe: wolumen `postgres_data` + dump bazy (pg_dump).