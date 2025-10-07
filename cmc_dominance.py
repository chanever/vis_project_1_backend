import os
import csv
from datetime import datetime
from zoneinfo import ZoneInfo
import requests


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CSV_PATH = os.path.join(DATA_DIR, "btc_dominance.csv")
CMC_ENDPOINT = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"


def _today_kst_str() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")


def _load_dotenv() -> None:
    """백엔드 디렉토리의 .env를 읽어 환경변수에 주입 (간단 파서)."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" not in s:
                    continue
                key, value = s.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


def _read_existing_rows():
    if not os.path.exists(CSV_PATH):
        return []
    rows = []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append({"date": row.get("date"), "btc_dominance": float(row.get("btc_dominance", "nan"))})
    return rows


def _write_rows(rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "btc_dominance"])
        w.writeheader()
        for row in rows:
            w.writerow({"date": row["date"], "btc_dominance": row["btc_dominance"]})


def _append_row(date_str: str, value: float):
    os.makedirs(DATA_DIR, exist_ok=True)
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(["date", "btc_dominance"])  # header
        w.writerow([date_str, f"{value}"])


def _fetch_cmc_latest() -> tuple[float, str]:
    _load_dotenv()
    api_key = os.getenv("CMC_API_KEY")
    if not api_key:
        raise RuntimeError("CMC_API_KEY 환경 변수가 설정되지 않았습니다.")
    headers = {"Accept": "application/json", "X-CMC_PRO_API_KEY": api_key}
    resp = requests.get(CMC_ENDPOINT, headers=headers, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data", {})
    status = payload.get("status", {})
    dom = float(data.get("btc_dominance"))
    ts = status.get("timestamp") or datetime.utcnow().isoformat() + "Z"
    return dom, ts


def get_btc_dominance() -> dict:
    """
    - data/btc_dominance.csv에 일자별로 캐시
    - 오늘(KST)의 레코드가 없으면 CMC API를 호출해 추가
    - 반환: { btc_dominance: float, last_updated: ISO8601, date: YYYY-MM-DD }
    """
    today = _today_kst_str()
    rows = _read_existing_rows()
    has_today = any(r.get("date") == today for r in rows)

    # 최근 1시간 내 갱신 여부 확인(과도한 API 호출 방지)
    if rows:
        try:
            last_ts_path = os.path.join(DATA_DIR, "btc_dominance.last")
            if os.path.exists(last_ts_path):
                with open(last_ts_path, "r", encoding="utf-8") as f:
                    last_iso = f.read().strip()
                last_dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
                now_dt = datetime.now(ZoneInfo("UTC"))
                if (now_dt - last_dt).total_seconds() < 3600 and has_today:
                    last = rows[-1]
                    return {"btc_dominance": float(last["btc_dominance"]), "last_updated": last_iso, "date": last["date"]}
        except Exception:
            pass

    if not has_today:
        try:
            dom, ts = _fetch_cmc_latest()
            _append_row(today, dom)
            # 기록
            try:
                with open(os.path.join(DATA_DIR, "btc_dominance.last"), "w", encoding="utf-8") as f:
                    f.write(ts)
            except Exception:
                pass
            return {"btc_dominance": dom, "last_updated": ts, "date": today}
        except Exception:
            # API 실패 시: 직전 값으로 대체 (휴일/장마감 등)
            if rows:
                last = rows[-1]
                return {
                    "btc_dominance": float(last["btc_dominance"]),
                    "last_updated": datetime.now(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
                    "date": last["date"],
                }
            raise

    # 이미 오늘 데이터가 존재하면 그대로 반환
    last = rows[-1] if rows else {"date": today, "btc_dominance": float("nan")}
    return {
        "btc_dominance": float(last["btc_dominance"]),
        "last_updated": datetime.now(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "date": last["date"],
    }


