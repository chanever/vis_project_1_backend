import os
import time
import json
import math
import ccxt
import pyupbit
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from dollar_scraper import get_usd_rates_df


def _to_date(dt_like) -> pd.Timestamp:
	"""Ensure pandas Timestamp normalized to date (no tz, midnight)."""
	if isinstance(dt_like, pd.Timestamp):
		return pd.Timestamp(dt_like.date())
	if isinstance(dt_like, datetime):
		return pd.Timestamp(dt_like.date())
	return pd.to_datetime(dt_like).normalize()


def _date_range_to_since_ms(start_date: str) -> int:
	"""Convert YYYY-MM-DD to since ms (UTC midnight)."""
	start = pd.to_datetime(start_date).tz_localize("UTC")
	return int(start.timestamp() * 1000)


def _validate_base_symbol(symbol: str) -> str:
	base = symbol.upper()
	allowed = {"BTC", "ETH", "SOL", "DOGE", "XRP", "ADA"}
	if base not in allowed:
		raise ValueError(f"Unsupported base symbol: {symbol}")
	return base


def fetch_binance_usdt_perp_daily(start_date: str, end_date: str, base_symbol: str = "BTC") -> pd.DataFrame:
	"""Fetch {BASE}USDT (Binance USD-M Futures) daily close prices. Return [date, <base>_usdt as close]."""
	base = _validate_base_symbol(base_symbol)
	exchange = ccxt.binanceusdm({"enableRateLimit": True})
	exchange.load_markets()
	# Prefer exact market id like 'BTCUSDT'
	market_id = f"{base}USDT"
	symbol = None
	for m in exchange.markets.values():
		if m.get("id") == market_id:
			symbol = m["symbol"]
			break
	if symbol is None:
		for cand in [f"{base}/USDT:USDT", f"{base}/USDT"]:
			if cand in exchange.markets:
				symbol = cand
				break
	if symbol is None:
		raise ValueError(f"Binance USDT-M futures market {market_id} not found")

	timeframe = "1d"
	since = _date_range_to_since_ms(start_date)
	all_rows = []
	limit = 1500
	while True:
		batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
		if not batch:
			break
		all_rows.extend(batch)
		last_ts = batch[-1][0]
		next_ts = last_ts + 1
		if pd.to_datetime(last_ts, unit="ms", utc=True).date() >= pd.to_datetime(end_date).date():
			break
		since = next_ts
		time.sleep(0.2)

	df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
	if df.empty:
		return pd.DataFrame(columns=["date", "usdt_close"]) 

	df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("UTC").dt.date
	df = df[["date", "close"]].rename(columns={"close": "usdt_close"})
	df["date"] = pd.to_datetime(df["date"])
	mask = (df["date"] >= pd.to_datetime(start_date)) & (df["date"] <= pd.to_datetime(end_date))
	df = df.loc[mask].drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
	return df


def fetch_upbit_krw_daily(start_date: str, end_date: str, base_symbol: str = "BTC") -> pd.DataFrame:
	"""Fetch Upbit KRW-{BASE} daily close. Return [date, krw_close]."""
	base = _validate_base_symbol(base_symbol)
	market = f"KRW-{base}"
	# Upbit 단일 호출로 긴 기간(count가 매우 클 때)이 실패하는 경우가 있어, 200일 단위로 백필 페이징
	end_dt = pd.to_datetime(end_date) + pd.Timedelta(days=1)
	start_dt = pd.to_datetime(start_date)
	to_ptr = end_dt
	chunks = []
	max_batch = 200
	max_iters = 200  # 안전장치(최대 ~ 40,000일)
	for _ in range(max_iters):
		# 최신에서 과거로 200개 단위 페이징
		part = pyupbit.get_ohlcv(
			market,
			interval="day",
			count=max_batch,
			to=to_ptr.strftime("%Y-%m-%d %H:%M:%S"),
		)
		if part is None or part.empty:
			break
		chunks.append(part)
		oldest_ts = pd.to_datetime(part.index.min())
		# 다음 루프용 포인터를 가장 오래된 캔들 직전 시각으로 이동
		to_ptr = oldest_ts - pd.Timedelta(minutes=1)
		# 이미 수집한 가장 오래된 날짜가 시작일 이전이면 중단
		if oldest_ts.date() <= start_dt.date():
			break
		# API 과호출 방지
		time.sleep(0.2)

	if not chunks:
		return pd.DataFrame(columns=["date", "krw_close"]) 

	# 수집한 조각 병합 후 정제
	merged = pd.concat(chunks, axis=0)
	merged = merged[~merged.index.duplicated(keep="last")]  # 중복 제거
	merged = merged.sort_index()
	res = merged.copy()
	res["date"] = pd.to_datetime(res.index.date)
	res = res[["date", "close"]].rename(columns={"close": "krw_close"})
	mask = (res["date"] >= start_dt) & (res["date"] <= pd.to_datetime(end_date))
	res = res.loc[mask].drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
	return res


def fetch_greed_index_daily(start_date: str, end_date: str) -> pd.DataFrame:
	"""Fetch Crypto Fear & Greed Index daily. Columns: [date, greed, greed_ffill]"""
	url = "https://api.alternative.me/fng/?limit=0&date_format=us"
	resp = requests.get(url, timeout=15)
	resp.raise_for_status()
	payload = resp.json()
	items = payload.get("data", [])
	rows = []
	for it in items:
		ts_str = it.get("timestamp"); val_str = it.get("value")
		if val_str is None or ts_str is None:
			continue
		try:
			dt = pd.to_datetime(ts_str)
		except Exception:
			try:
				dt = pd.to_datetime(int(ts_str), unit="s", utc=True).tz_convert("UTC").tz_localize(None)
			except Exception:
				continue
		rows.append((dt.normalize(), int(val_str)))
	
	df = pd.DataFrame(rows, columns=["date", "greed"]).drop_duplicates(subset=["date"]).sort_values("date")
	if df.empty:
		return pd.DataFrame(columns=["date", "greed", "greed_ffill"]) 
	
	sidx = pd.date_range(start=pd.to_datetime(start_date), end=pd.to_datetime(end_date), freq="D")
	df = df.set_index("date").reindex(sidx)
	orig = df["greed"].copy()
	df["greed"] = orig.ffill()
	df["greed_ffill"] = df["greed"].ne(orig)
	df = df.rename_axis("date").reset_index()
	return df


def build_dataset(start_date: str, end_date: str, base_symbol: str = "BTC") -> pd.DataFrame:
	"""Build joined DF with columns: date, usdt_close, krw_close, usdkrw, usd_ffill, greed, greed_ffill, kimchi_pct"""
	base = _validate_base_symbol(base_symbol)
	binance_df = fetch_binance_usdt_perp_daily(start_date, end_date, base)
	upbit_df = fetch_upbit_krw_daily(start_date, end_date, base)
	usd_df = get_usd_rates_df(start_date, end_date).rename(columns={"usd_rate": "usdkrw"})
	greed_df = fetch_greed_index_daily(start_date, end_date)
	
	for df in (binance_df, upbit_df, usd_df, greed_df):
		if not df.empty:
			df["date"] = pd.to_datetime(df["date"]).dt.normalize()
	
	df = binance_df.merge(upbit_df, on="date", how="inner").merge(usd_df, on="date", how="inner").merge(greed_df, on="date", how="inner")
	if df.empty:
		return pd.DataFrame(columns=["date", "usdt_close", "krw_close", "usdkrw", "usd_ffill", "greed", "greed_ffill", "kimchi_pct"]) 
	
	# 빈 값들을 이전 값으로 채우기 (forward fill)
	df["usdkrw"] = df["usdkrw"].ffill()
	df["greed"] = df["greed"].ffill()
	df["usdt_close"] = df["usdt_close"].ffill()
	df["krw_close"] = df["krw_close"].ffill()
	
	# kimchi_pct 계산 (0으로 나누기 방지)
	df["kimchi_pct"] = (df["krw_close"] / (df["usdt_close"] * df["usdkrw"]) - 1.0) * 100.0
	df["kimchi_pct"] = df["kimchi_pct"].ffill()  # 계산 결과도 forward fill
	return df[["date", "usdt_close", "krw_close", "usdkrw", "usd_ffill", "greed", "greed_ffill", "kimchi_pct"]].sort_values("date").reset_index(drop=True)


def save_csv(df: pd.DataFrame, path: str) -> None:
    """원자적 저장: 임시 파일에 쓰고 교체하여 부분 손상 방지."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    
    # 빈 값들을 이전 값으로 채우기 (forward fill)
    df_copy = df.copy()
    for col in ["usdkrw", "greed", "usdt_close", "krw_close", "kimchi_pct"]:
        if col in df_copy.columns:
            df_copy[col] = df_copy[col].ffill()
    
    df_copy.to_csv(tmp_path, index=False)
    try:
        os.replace(tmp_path, path)
    except Exception:
        # 교체 실패 시라도 최후 수단으로 직접 저장
        df_copy.to_csv(path, index=False)


def load_or_build_dataset(start_date: str, end_date: str, cache_path: Optional[str] = None, use_cache: bool = True, base_symbol: str = "BTC") -> pd.DataFrame:
    """증분 캐시를 사용해 데이터셋을 반환한다.
    - 캐시가 있으면 앞뒤 결손 구간만 빌드하여 append/prepend 후 저장
    - 캐시가 없으면 전체 구간 빌드 후 저장
    - 최근 3일 데이터는 항상 다시 확인하여 업데이트 (데이터 정확도 보장)
    - 항상 [start_date, end_date] 구간으로 슬라이싱하여 반환
    """
    req_start_dt = pd.to_datetime(start_date).normalize()
    req_end_dt = pd.to_datetime(end_date).normalize()

    cache_df: Optional[pd.DataFrame] = None
    if cache_path and use_cache and os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        try:
            cache_df = pd.read_csv(cache_path, parse_dates=["date"])  # columns: date, usdt_close, krw_close, usdkrw, usd_ffill, greed, greed_ffill, kimchi_pct
            cache_df["date"] = pd.to_datetime(cache_df["date"]).dt.normalize()
            cache_df = cache_df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
            
            # 빈 값들을 이전 값으로 채우기 (forward fill)
            for col in ["usdkrw", "greed", "usdt_close", "krw_close", "kimchi_pct"]:
                if col in cache_df.columns:
                    cache_df[col] = cache_df[col].ffill()
        except Exception:
            cache_df = None

    # 캐시가 없으면 전체 빌드 후 저장
    if cache_df is None or cache_df.empty:
        built = build_dataset(start_date, end_date, base_symbol=base_symbol)
        if cache_path:
            save_csv(built, cache_path)
        # 반환은 요청 구간 그대로
        mask = (built["date"] >= req_start_dt) & (built["date"] <= req_end_dt)
        return built.loc[mask].reset_index(drop=True)

    # 앞/뒤 결손 구간 보정 + 소규모 중간 결손 보정
    earliest_cached = pd.to_datetime(cache_df["date"].min()).normalize()
    latest_cached = pd.to_datetime(cache_df["date"].max()).normalize()
    updated_df = cache_df

    # 최근 3일 데이터 재확인 및 업데이트 (데이터 정확도 보장)
    recent_refresh_days = 3
    recent_start = max(earliest_cached, latest_cached - pd.Timedelta(days=recent_refresh_days - 1))
    recent_end = max(latest_cached, req_end_dt)
    
    if recent_start <= recent_end:
        recent_start_str = recent_start.strftime("%Y-%m-%d")
        recent_end_str = recent_end.strftime("%Y-%m-%d")
        recent_df = build_dataset(recent_start_str, recent_end_str, base_symbol=base_symbol)
        
        if not recent_df.empty:
            # 기존 캐시에서 최근 3일 데이터 제거
            updated_df = updated_df[updated_df["date"] < recent_start]
            # 새로운 최근 데이터 추가
            updated_df = pd.concat([updated_df, recent_df], ignore_index=True)
            updated_df = updated_df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    # 뒤쪽 결손: (latest_cached+1) ~ req_end_dt (최근 3일 재확인 후 업데이트된 latest_cached 기준)
    updated_latest = pd.to_datetime(updated_df["date"].max()).normalize()
    if req_end_dt > updated_latest:
        gap_start = (updated_latest + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        gap_end = req_end_dt.strftime("%Y-%m-%d")
        gap_df = build_dataset(gap_start, gap_end, base_symbol=base_symbol)
        if not gap_df.empty:
            updated_df = pd.concat([updated_df, gap_df], ignore_index=True)
            updated_df = updated_df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
            # 내부 소규모 갭도 함께 메움
            updated_df = _fill_small_internal_gaps(updated_df, base_symbol)

    # 앞쪽 결손: req_start_dt ~ (earliest_cached-1)
    updated_earliest = pd.to_datetime(updated_df["date"].min()).normalize()
    if req_start_dt < updated_earliest:
        pre_end = (updated_earliest - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        pre_start = req_start_dt.strftime("%Y-%m-%d")
        pre_df = build_dataset(pre_start, pre_end, base_symbol=base_symbol)
        if not pre_df.empty:
            updated_df = pd.concat([pre_df, updated_df], ignore_index=True)
            updated_df = updated_df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
            # 내부 소규모 갭도 함께 메움
            updated_df = _fill_small_internal_gaps(updated_df, base_symbol)

    # 캐시 파일 갱신
    if cache_path and use_cache:
        save_csv(updated_df, cache_path)

    # 요청 구간 슬라이스 반환
    mask = (updated_df["date"] >= req_start_dt) & (updated_df["date"] <= req_end_dt)
    return updated_df.loc[mask].reset_index(drop=True)


def _detect_small_gaps(dates: pd.Series, max_gap_days: int = 7) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """연속 일자에서 소규모 결손 구간들을 탐지한다."""
    if dates.empty:
        return []
    ds = pd.to_datetime(dates).sort_values().drop_duplicates().reset_index(drop=True)
    gaps = []
    for i in range(1, len(ds)):
        prev = ds.iloc[i - 1]
        curr = ds.iloc[i]
        if (curr - prev).days > 1:
            gap_len = (curr - prev).days - 1
            if gap_len <= max_gap_days:
                gaps.append((prev + pd.Timedelta(days=1), curr - pd.Timedelta(days=1)))
    return gaps


def _fill_small_internal_gaps(updated_df: pd.DataFrame, base_symbol: str) -> pd.DataFrame:
    """소규모 내부 결손(<=7일)을 감지해 해당 범위만 빌드/병합한다."""
    gaps = _detect_small_gaps(updated_df["date"]) if not updated_df.empty else []
    for (g0, g1) in gaps:
        g_start = g0.strftime("%Y-%m-%d")
        g_end = g1.strftime("%Y-%m-%d")
        gap_df = build_dataset(g_start, g_end, base_symbol=base_symbol)
        if not gap_df.empty:
            updated_df = pd.concat([updated_df, gap_df], ignore_index=True)
            updated_df = updated_df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
    return updated_df