import os
import json
from fastapi import FastAPI, Query, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from datetime import date, timedelta, datetime, timezone
import pandas as pd
import ccxt
import pyupbit

from pipeline import load_or_build_dataset, save_csv

app = FastAPI(title="Kimchi Premium API")

# Allow local dev UIs
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

BACKEND_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BACKEND_DIR, "data")
DATA_CSV = os.path.join(DATA_DIR, "kimchi_premium_daily.csv")


@app.get("/health")
def health():
	return {"status": "ok"}


@app.get("/dataset")
def get_dataset(start: str = Query(...), end: str = Query(...), symbol: str = Query("BTC")):
	try:
		df = load_or_build_dataset(start, end, cache_path=os.path.abspath(DATA_CSV), use_cache=True, base_symbol=symbol)
		df = df.copy()
		df["date"] = df["date"].dt.strftime("%Y-%m-%d")
		return JSONResponse(content=df.to_dict(orient="records"))
	except Exception as e:
		return JSONResponse(status_code=500, content={"error": str(e)})


def _cache_json_path(symbol: str) -> str:
	return os.path.join(DATA_DIR, f"dataset_{symbol.upper()}_2025.json")


def _load_cache_json(path: str):
	if os.path.exists(path):
		try:
			with open(path, "r", encoding="utf-8") as f:
				return json.load(f)
		except Exception:
			return None
	return None


def _save_cache_json(path: str, content):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", encoding="utf-8") as f:
		json.dump(content, f, ensure_ascii=False)


@app.get("/dataset_{symbol}_2025")
def get_dataset_symbol_2025(symbol: str = Path(..., description="BTC|ETH|SOL|DOGE|XRP|ADA"), refresh: bool = Query(False)):
	try:
		start = "2025-01-01"; end = "2025-09-30"
		symbol = symbol.upper()
		cache_path = _cache_json_path(symbol)
		if not refresh:
			cache = _load_cache_json(cache_path)
			if isinstance(cache, list) and any(r.get("timestamp") == end for r in cache):
				return JSONResponse(content=cache)
		# build
		df = load_or_build_dataset(start, end, cache_path=None, use_cache=False, base_symbol=symbol)
		df = df.copy()
		df["upbit_usdt"] = df["krw_close"] / df["usdkrw"]
		records = []
		for _, row in df.iterrows():
			ts = row["date"].strftime("%Y-%m-%d") if isinstance(row["date"], pd.Timestamp) else str(row["date"]) 
			records.append({
				"timestamp": ts,
				"binance_usdt": float(row["usdt_close"]),
				"upbit_usdt": float(row["upbit_usdt"]),
				"kimchi_pct": float(row["kimchi_pct"]),
				"usdkrw": float(row["usdkrw"]),
				"greed": int(row["greed"]) if pd.notna(row["greed"]) else None,
				"usd_ffill": bool(row.get("usd_ffill", False)) if "usd_ffill" in row else False,
				"greed_ffill": bool(row.get("greed_ffill", False)) if "greed_ffill" in row else False,
			})
		_save_cache_json(cache_path, records)
		return JSONResponse(content=records)
	except Exception as e:
		return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/dataset/2025/{symbol}")
def get_dataset_symbol_2025_alt(symbol: str = Path(..., description="BTC|ETH|SOL|DOGE|XRP|ADA"), refresh: bool = Query(False)):
	try:
		start = "2025-01-01"; end = "2025-09-30"
		symbol = symbol.upper()
		cache_path = _cache_json_path(symbol)
		if not refresh:
			cache = _load_cache_json(cache_path)
			if isinstance(cache, list) and any(r.get("timestamp") == end for r in cache):
				return JSONResponse(content=cache)
		df = load_or_build_dataset(start, end, cache_path=None, use_cache=False, base_symbol=symbol)
		df = df.copy()
		df["upbit_usdt"] = df["krw_close"] / df["usdkrw"]
		records = []
		for _, row in df.iterrows():
			ts = row["date"].strftime("%Y-%m-%d") if isinstance(row["date"], pd.Timestamp) else str(row["date"]) 
			records.append({
				"timestamp": ts,
				"binance_usdt": float(row["usdt_close"]),
				"upbit_usdt": float(row["upbit_usdt"]),
				"kimchi_pct": float(row["kimchi_pct"]),
				"usdkrw": float(row["usdkrw"]),
				"greed": int(row["greed"]) if pd.notna(row["greed"]) else None,
				"usd_ffill": bool(row.get("usd_ffill", False)) if "usd_ffill" in row else False,
				"greed_ffill": bool(row.get("greed_ffill", False)) if "greed_ffill" in row else False,
			})
		_save_cache_json(cache_path, records)
		return JSONResponse(content=records)
	except Exception as e:
		return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/download")
def download_csv(start: str, end: str, symbol: str = Query("BTC")):
	df = load_or_build_dataset(start, end, cache_path=os.path.abspath(DATA_CSV), use_cache=False, base_symbol=symbol)
	save_csv(df, os.path.abspath(DATA_CSV))
	return FileResponse(os.path.abspath(DATA_CSV), media_type="text/csv", filename="kimchi_premium_daily.csv")


@app.get("/realtime/{symbol}")
def get_realtime(symbol: str = Path(..., description="BTC|ETH|SOL|DOGE|XRP|ADA")):
	try:
		symbol = symbol.upper()
		# Binance USD-M Futures last price
		ex = ccxt.binanceusdm({"enableRateLimit": True})
		market_id = f"{symbol}USDT"
		ex.load_markets()
		sym = None
		for m in ex.markets.values():
			if m.get("id") == market_id:
				sym = m["symbol"]; break
		if sym is None:
			for cand in [f"{symbol}/USDT:USDT", f"{symbol}/USDT"]:
				if cand in ex.markets:
					sym = cand; break
		if sym is None:
			raise ValueError("market not found")
		binance_ticker = ex.fetch_ticker(sym)
		binance_usdt = float(binance_ticker.get("last"))
		# Upbit KRW market last price
		upbit_market = f"KRW-{symbol}"
		upbit_ticker = pyupbit.get_current_price(upbit_market)
		if upbit_ticker is None:
			raise ValueError("upbit price unavailable")
		upbit_krw = float(upbit_ticker)
		# USDKRW: 최근 영업일 값 (캐시 파일이 있으면 마지막 값 사용, 없으면 스크래퍼로 오늘~오늘 호출)
		usdkrw = None
		# 1) 심볼 캐시 JSON의 최근 usdkrw 사용
		cache_any = _load_cache_json(_cache_json_path("BTC"))
		if isinstance(cache_any, list) and len(cache_any) > 0:
			try:
				usdkrw = float(cache_any[-1].get("usdkrw"))
			except Exception:
				usdkrw = None
		# 2) CSV 캐시가 있으면 최근 값 사용
		if usdkrw is None and os.path.exists(DATA_CSV):
			try:
				csv_df = pd.read_csv(DATA_CSV)
				if "usdkrw" in csv_df.columns and not csv_df.empty:
					usdkrw = float(csv_df.iloc[-1]["usdkrw"])  # 마지막 행
			except Exception:
				usdkrw = None
		# 3) 최근 14일 구간 스크래핑 후 가장 최근 값 사용(주말/휴일 포함, ffill 허용)
		if usdkrw is None:
			from dollar_scraper import get_usd_rates_df
			from datetime import date, timedelta
			today = date.today()
			start = (today - timedelta(days=14)).strftime("%Y-%m-%d")
			end = today.strftime("%Y-%m-%d")
			df = get_usd_rates_df(start, end)
			if not df.empty:
				try:
					usdkrw = float(df.iloc[-1]["usd_rate"])  # 마지막 가용값(주말이면 ffill된 값)
				except Exception:
					usdkrw = None
		# 4) 최종 폴백(비상용)
		if usdkrw is None:
			usdkrw = 1300.0
		# kimchi premium in real-time
		kimchi_pct = (upbit_krw / (binance_usdt * usdkrw) - 1.0) * 100.0
		return {
			"timestamp": datetime.utcnow().isoformat(),
			"binance_usdt": binance_usdt,
			"upbit_krw": upbit_krw,
			"usdkrw": usdkrw,
			"kimchi_pct": kimchi_pct,
		}
	except Exception as e:
		return JSONResponse(status_code=500, content={"error": str(e)})


if __name__ == "__main__":
	import uvicorn
	uvicorn.run(app, host="0.0.0.0", port=8000)