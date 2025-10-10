import os
import json
from fastapi import FastAPI, Query, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import ccxt
import pyupbit

from pipeline import load_or_build_dataset, save_csv
from cmc_dominance import get_btc_dominance
from dollar_scraper import get_usd_rates_df

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

def _symbol_csv_path(symbol: str) -> str:
    sym = (symbol or "BTC").upper()
    return os.path.join(DATA_DIR, f"kimchi_premium_daily_{sym}.csv")

_SYMBOL_LISTING_START = {
    # 보수적으로 2020-01-01을 기본 시작일로 사용
    "BTC": "2020-01-01",
    "ETH": "2020-01-01",
    "XRP": "2020-01-01",
    "ADA": "2020-01-01",
    # 사용자 요구: DOGE, SOL은 2021년부터 데이터 제공
    "DOGE": "2021-01-01",
    "SOL": "2021-01-01",
}

def _clamp_start_by_symbol(symbol: str, requested_start: str) -> str:
    sym = (symbol or "BTC").upper()
    base = _SYMBOL_LISTING_START.get(sym, "2020-01-01")
    req = pd.to_datetime(requested_start).date()
    bas = pd.to_datetime(base).date()
    eff = max(req, bas)
    return pd.Timestamp(eff).strftime("%Y-%m-%d")

def _effective_end_date(requested_end: str) -> str:
    """Return end date respecting KST 09:30 daily data availability.
    - Before 09:30 KST: use yesterday
    - At/after 09:30 KST: today
    Also clamp to the client-requested end.
    """
    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    cutoff = kst_now.replace(hour=9, minute=30, second=0, microsecond=0)
    available_end = (kst_now.date() if kst_now >= cutoff else (kst_now.date() - timedelta(days=1)))
    req_end = pd.to_datetime(requested_end).date()
    eff_end = min(req_end, available_end)
    return pd.Timestamp(eff_end).strftime("%Y-%m-%d")


@app.get("/health")
def health():
	return {"status": "ok"}


@app.get("/btc_dominance")
def btc_dominance():
    try:
        payload = get_btc_dominance()
        return JSONResponse(content=payload)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/dataset")
def get_dataset(start: str = Query(...), end: str = Query(...), symbol: str = Query("BTC")):
	try:
		# KST 09:30 컷오프 반영 및 심볼별 CSV 경로
		symbol = (symbol or "BTC").upper()
		eff_end = _effective_end_date(end)
		eff_start = _clamp_start_by_symbol(symbol, start)
		csv_path = os.path.abspath(_symbol_csv_path(symbol))
		# 항상 증분 캐시 로직을 사용(앞/뒤/소규모 중간 결손 보충)
		df = load_or_build_dataset(eff_start, eff_end, cache_path=csv_path, use_cache=True, base_symbol=symbol)
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
		csv_path = os.path.abspath(_symbol_csv_path(symbol))
		df = load_or_build_dataset(start, end, cache_path=csv_path, use_cache=True, base_symbol=symbol)
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
		return JSONResponse(content=records)
	except Exception as e:
		return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/dataset/2025/{symbol}")
def get_dataset_symbol_2025_alt(symbol: str = Path(..., description="BTC|ETH|SOL|DOGE|XRP|ADA"), refresh: bool = Query(False)):
	try:
		start = "2025-01-01"; end = "2025-09-30"
		symbol = symbol.upper()
		csv_path = os.path.abspath(_symbol_csv_path(symbol))
		df = load_or_build_dataset(start, end, cache_path=csv_path, use_cache=True, base_symbol=symbol)
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
		return JSONResponse(content=records)
	except Exception as e:
		return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/download")
def download_csv(start: str, end: str, symbol: str = Query("BTC")):
	symbol = (symbol or "BTC").upper()
	eff_end = _effective_end_date(end)
	eff_start = _clamp_start_by_symbol(symbol, start)
	csv_path = os.path.abspath(_symbol_csv_path(symbol))
	# 캐시를 증분 갱신(보존)하고, 다운로드는 별도 임시 파일로 제공합니다.
	df = load_or_build_dataset(eff_start, eff_end, cache_path=csv_path, use_cache=True, base_symbol=symbol)
	# 임시 파일 경로
	from tempfile import NamedTemporaryFile
	import shutil
	with NamedTemporaryFile(delete=False, suffix=f"_{symbol}.csv") as tmp:
		# 요청 구간만 저장
		df.to_csv(tmp.name, index=False)
		tmp_path = tmp.name
	# 응답으로 임시 파일 제공(원본 캐시는 유지)
	return FileResponse(tmp_path, media_type="text/csv", filename=f"kimchi_premium_daily_{symbol}.csv")


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
		# CSV 캐시에서 최근 usdkrw 사용(우선 BTC, 없으면 아무 심볼)
		preferred = os.path.abspath(_symbol_csv_path("BTC"))
		csv_paths = []
		if os.path.exists(preferred):
			csv_paths.append(preferred)
		csv_paths.extend([os.path.join(DATA_DIR, p) for p in os.listdir(DATA_DIR) if p.startswith("kimchi_premium_daily_") and p.endswith(".csv")])
		for p in csv_paths:
			try:
				csv_df = pd.read_csv(p)
				if "usdkrw" in csv_df.columns and not csv_df.empty:
					usdkrw = float(csv_df.iloc[-1]["usdkrw"])  # 마지막 행
					break
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


@app.post("/backfill/2020/{symbol}")
def backfill_from_2020(symbol: str = Path(..., description="BTC|ETH|SOL|DOGE|XRP|ADA")):
	"""2020-01-01부터 오늘(KST 09:30 컷오프 반영)까지 해당 심볼의 데이터를 백필하고 CSV 캐시를 갱신한다.
	- 환율은 내부 캐시(data/usdkrw_daily.csv)가 증분 갱신되어 재사용됨
	- 완료 후 백필된 구간의 레코드 수를 반환
	"""
	try:
		symbol = (symbol or "BTC").upper()
		start = _SYMBOL_LISTING_START.get(symbol, "2020-01-01")
		# 오늘 기준 eff_end
		eff_end = _effective_end_date(datetime.now().strftime("%Y-%m-%d"))
		csv_path = os.path.abspath(_symbol_csv_path(symbol))
		# 증분 캐시를 활용하여 전체 구간을 보장
		df = load_or_build_dataset(start, eff_end, cache_path=csv_path, use_cache=True, base_symbol=symbol)
		# 캐시에 이미 저장되었지만, 확실히 저장
		save_csv(df, csv_path)
		return {"symbol": symbol, "start": start, "end": eff_end, "rows": int(len(df))}
	except Exception as e:
		return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
	import uvicorn
	uvicorn.run(app, host="0.0.0.0", port=8000)