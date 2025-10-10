import os
import requests
import datetime
import re
import pandas as pd

def validate_date(date_str: str) -> datetime.date:
    """날짜 문자열(YYYY-MM-DD)이 올바른지 검사하고 date 객체로 반환"""
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"❌ 잘못된 날짜 형식 또는 존재하지 않는 날짜: {date_str}")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
USDKRW_CSV_PATH = os.path.join(DATA_DIR, "usdkrw_daily.csv")


def _read_usd_cache() -> pd.DataFrame:
    if not os.path.exists(USDKRW_CSV_PATH) or os.path.getsize(USDKRW_CSV_PATH) == 0:
        return pd.DataFrame(columns=["date", "usd_rate", "usd_ffill"])
    try:
        df = pd.read_csv(USDKRW_CSV_PATH, parse_dates=["date"])  # ensure datetime
        # 정렬 및 컬럼 보정
        base_cols = ["date", "usd_rate", "usd_ffill"]
        for c in base_cols:
            if c not in df.columns:
                df[c] = pd.Series(dtype="float64" if c != "date" else "datetime64[ns]")
        df = df[base_cols].sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "usd_rate", "usd_ffill"])


def _write_usd_cache(df: pd.DataFrame) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    out = df.copy()
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    out.to_csv(USDKRW_CSV_PATH, index=False)


def _load_dotenv() -> None:
    """간단한 .env 로더(FIXER_API_KEY 등에 사용)."""
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


def _fetch_fixer_usdkrw_for_date(d: datetime.date) -> tuple[float, str] | None:
    """Fixer API에서 특정 날짜의 USD/KRW를 조회.
    - Free plan: base EUR 고정 → USD/KRW = KRW_per_EUR / USD_per_EUR
    - 성공 시 (usd_krw, api_date_str) 반환
    - 실패 시 None
    """
    _load_dotenv()
    api_key = os.getenv("FIXER_API_KEY")
    if not api_key:
        return None
    url = f"https://data.fixer.io/api/{d.strftime('%Y-%m-%d')}"
    try:
        res = requests.get(url, params={"access_key": api_key, "symbols": "USD,KRW"}, timeout=10)
        data = res.json()
        if not data or not data.get("success"):
            return None
        rates = data.get("rates", {})
        usd = float(rates.get("USD"))
        krw = float(rates.get("KRW"))
        if usd <= 0 or krw <= 0:
            return None
        usd_krw = krw / usd
        api_date = str(data.get("date") or d.strftime("%Y-%m-%d"))
        return usd_krw, api_date
    except Exception:
        return None


def _scrape_usd_rates_range_fixer(start: datetime.date, end: datetime.date) -> pd.DataFrame:
    """Fixer를 이용해 [start, end] 일자별 USD/KRW를 조회.
    - 공휴일 등으로 API의 date가 과거로 나올 수 있음 → 요청일에 기록하고 usd_ffill=True로 표기
    """
    delta = datetime.timedelta(days=1)
    cur = start
    rows = []  # (date, usd_rate, usd_ffill)
    last_rate = None
    while cur <= end:
        got = _fetch_fixer_usdkrw_for_date(cur)
        if got is not None:
            rate, api_date = got
            # 요청일과 API가 보고한 기준일이 다르면 ffill로 간주
            ffill = (api_date != cur.strftime("%Y-%m-%d"))
            last_rate = rate
            rows.append([cur, rate, ffill])
        else:
            # Fixer 실패: 일단 빈 칸으로 두고 폴백 로직에서 처리
            rows.append([cur, None, None])
        cur += delta

    # 누락된 날짜(값 None)는 폴백 스크래퍼로 채움
    # 연속 구간으로 묶어서 최소 호출
    missing_spans = []
    span_start = None
    cur = start
    for i, (_, rate, _) in enumerate(rows):
        if rate is None and span_start is None:
            span_start = start + datetime.timedelta(days=i)
        if rate is not None and span_start is not None:
            span_end = start + datetime.timedelta(days=i - 1)
            missing_spans.append((span_start, span_end))
            span_start = None
    if span_start is not None:
        missing_spans.append((span_start, end))

    if missing_spans:
        fb_parts = []
        for s, e in missing_spans:
            fb = _scrape_usd_rates_range(s, e)
            if not fb.empty:
                fb_parts.append(fb)
        if fb_parts:
            fb_all = pd.concat(fb_parts, ignore_index=True)
            fb_all = fb_all.sort_values("date").reset_index(drop=True)
            # Fixer 결과와 병합: Fixer 우선, 폴백은 None만 채움
            fix_df = pd.DataFrame(rows, columns=["date", "usd_rate", "usd_ffill"])
            fix_df["date"] = pd.to_datetime(fix_df["date"])
            fix_df = fix_df.sort_values("date").reset_index(drop=True)
            # where로 None 채우기
            merged = fix_df.merge(fb_all, on="date", how="left", suffixes=("_fix", "_fb"))
            def _pick_rate(row):
                return row["usd_rate_fix"] if pd.notna(row["usd_rate_fix"]) else row["usd_rate_fb"]
            def _pick_ffill(row):
                # Fixer가 제공한 ffill 정보가 우선. 없으면 폴백의 ffill 사용
                if pd.notna(row.get("usd_ffill_fix")):
                    return bool(row["usd_ffill_fix"]) if not pd.isna(row["usd_ffill_fix"]) else False
                if pd.notna(row.get("usd_ffill_fb")):
                    return bool(row["usd_ffill_fb"]) if not pd.isna(row["usd_ffill_fb"]) else False
                return False
            merged["usd_rate"] = merged.apply(_pick_rate, axis=1)
            merged["usd_ffill"] = merged.apply(_pick_ffill, axis=1)
            out = merged[["date", "usd_rate", "usd_ffill"]].copy()
            out["date"] = pd.to_datetime(out["date"])  # normalize
            return out

    # 모두 Fixer로 채워졌다면 그대로 반환
    df = pd.DataFrame(rows, columns=["date", "usd_rate", "usd_ffill"]).dropna(subset=["usd_rate"]) 
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]) 
    return df.sort_values("date").reset_index(drop=True)


def _scrape_usd_rates_range(start: datetime.date, end: datetime.date) -> pd.DataFrame:
    base_url = "http://www.smbs.biz/Flash/TodayExRate_flash.jsp?tr_date={}"
    delta = datetime.timedelta(days=1)
    current = start
    data = []  # (date, rate, usd_ffill)
    last_rate = None
    pending_dates = []
    
    while current <= end:
        url = base_url.format(current.strftime("%Y-%m-%d"))
        try:
            resp = requests.get(url, timeout=5)
            text = resp.text.strip()
            
            if "오류가 발생하였습니다" in text:
                print(f"[ERROR] {current} : 잘못된 요청")
                current += delta
                continue
            
            if "USD=" not in text:
                pending_dates.append(current)
                current += delta
                continue
            
            match = re.search(r"USD=([\d,]+\.\d+)", text)
            if match:
                rate = float(match.group(1).replace(",", ""))
                last_rate = rate
                for pd_date in pending_dates:
                    data.append([pd_date, rate, True])
                pending_dates = []
                data.append([current, rate, False])
            else:
                print(f"[WARN] {current} : USD 환율을 찾을 수 없음")
        except Exception as e:
            print(f"[EXCEPTION] {current} : {e}")
        current += delta
    
    if pending_dates and last_rate is not None:
        for pd_date in pending_dates:
            data.append([pd_date, last_rate, True])
    
    df = pd.DataFrame(data, columns=["date", "usd_rate", "usd_ffill"])
    df["date"] = pd.to_datetime(df["date"])  # naive date
    df = df.sort_values("date").reset_index(drop=True)
    return df


def get_usd_rates_df(start_date: str, end_date: str) -> pd.DataFrame:
    """
    특정 기간(start_date ~ end_date) 동안의 KRW/USD 환율을 반환.
    - 내부적으로 CSV 캐시(data/usdkrw_daily.csv)를 사용하여 "가장 최신 저장일+1"부터만 스크래핑하여 증분 갱신.
    - 캐시가 비어 있으면 전체 구간을 스크래핑하여 저장.
    - 반환 컬럼: [date, usd_rate, usd_ffill]
    """
    # 날짜 유효성 검사
    start = validate_date(start_date)
    end = validate_date(end_date)
    if start > end:
        raise ValueError(f"❌ 시작일({start})이 종료일({end})보다 이후일 수 없습니다.")

    # 1) 캐시 로드
    cache_df = _read_usd_cache()

    # 2) 증분 스크래핑 범위 결정 (캐시가 있으면 마지막 날짜 + 1일부터 end까지)
    need_scrape = False
    scrape_start = None
    scrape_end = None
    if cache_df.empty:
        need_scrape = True
        scrape_start = start
        scrape_end = end
    else:
        last_cached: pd.Timestamp = pd.to_datetime(cache_df["date"].max()).to_pydatetime().date()
        if end > last_cached:
            need_scrape = True
            scrape_start = (last_cached + datetime.timedelta(days=1))
            scrape_end = end

    # 3) 필요한 경우에만 스크래핑 후 캐시 갱신
    if need_scrape and scrape_start is not None and scrape_start <= scrape_end:
        # 1차: Fixer로 시도
        try:
            fx_df = _scrape_usd_rates_range_fixer(scrape_start, scrape_end)
        except Exception:
            fx_df = pd.DataFrame(columns=["date", "usd_rate", "usd_ffill"])
        scraped_df = fx_df
        # Fixer가 비거나 실패하면 기존 스크래퍼로 전체 구간 폴백
        if scraped_df.empty:
            scraped_df = _scrape_usd_rates_range(scrape_start, scrape_end)
        if not scraped_df.empty:
            merged = pd.concat([cache_df, scraped_df], ignore_index=True)
            _write_usd_cache(merged)
            cache_df = _read_usd_cache()

    # 4) 요청 구간 슬라이싱 후 반환 (캐시 기반)
    if cache_df.empty:
        return cache_df
    mask = (cache_df["date"] >= pd.to_datetime(start)) & (cache_df["date"] <= pd.to_datetime(end))
    return cache_df.loc[mask].reset_index(drop=True)
 
# 테스트 실행 제거 (모듈 import 시 출력 방지)
