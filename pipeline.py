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
	
	df["kimchi_pct"] = (df["krw_close"] / (df["usdt_close"] * df["usdkrw"]) - 1.0) * 100.0
	return df[["date", "usdt_close", "krw_close", "usdkrw", "usd_ffill", "greed", "greed_ffill", "kimchi_pct"]].sort_values("date").reset_index(drop=True)


def save_csv(df: pd.DataFrame, path: str) -> None:
	os.makedirs(os.path.dirname(path), exist_ok=True)
	df.to_csv(path, index=False)


def load_or_build_dataset(start_date: str, end_date: str, cache_path: Optional[str] = None, use_cache: bool = True, base_symbol: str = "BTC") -> pd.DataFrame:
	if cache_path and use_cache and os.path.exists(cache_path):
		df = pd.read_csv(cache_path, parse_dates=["date"])
		mask = (df["date"] >= pd.to_datetime(start_date)) & (df["date"] <= pd.to_datetime(end_date))
		return df.loc[mask].reset_index(drop=True)
	
	df = build_dataset(start_date, end_date, base_symbol=base_symbol)
	if cache_path:
		save_csv(df, cache_path)
	return df 