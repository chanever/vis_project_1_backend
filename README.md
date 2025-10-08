Kimchi Premium Backend

개요
- FastAPI 기반 백엔드로, 일일 데이터셋을 빌드/서빙하고 일부 보조 엔드포인트를 제공합니다.
- 데이터 파이프라인은 바이낸스 선물 일봉, 업비트 KRW 일봉, USD/KRW(ffill 포함), Greed Index를 결합합니다.

핵심 정책 (KST 컷오프)
- 일일 데이터는 매일 오전 9시 30분(KST) 이후에 당일 데이터를 대상으로 빌드합니다.
- 오전 9시 30분 이전 요청은 종료일을 ‘어제’로 강제(effective end date)하여 처리합니다.
- 소스별 수급 지연 시(예: 일부 소스에 오늘 일봉이 아직 없음) inner join 특성상 당일 행은 제외되고 어제까지가 응답됩니다.
- USD/KRW는 주말·공휴일에 전일값으로 보간(ffill=True 표시)됩니다.

주요 파일
- main.py: FastAPI 엔트리포인트 및 엔드포인트 정의
- pipeline.py: 원천 데이터 수집 및 결합 로직
- dollar_scraper.py: USD/KRW 일일 데이터 수집 (주말/공휴일 ffill)

데이터 파이프라인
1) Binance USD-M Futures 일봉 (BASEUSDT)
2) Upbit KRW-BASE 일봉
3) USD/KRW 일일 환율 (ffill 포함)
4) Crypto Fear & Greed Index
→ inner join으로 결합 후 kimchi_pct 계산

중요 함수
- pipeline.build_dataset(start, end, base_symbol)
  - 반환 컬럼: date, usdt_close, krw_close, usdkrw, usd_ffill, greed, greed_ffill, kimchi_pct
- pipeline.load_or_build_dataset(start, end, cache_path, use_cache, base_symbol)
  - 캐시 CSV가 있고 use_cache=True면 범위 필터 후 반환, 아니면 새로 빌드 후 저장
- main._effective_end_date(end)
  - 09:30 이전엔 어제, 이후엔 오늘로 종료일을 결정

엔드포인트
- GET /health
  - 상태 체크
- GET /btc_dominance
  - BTC Dominance 간단 조회
- GET /dataset?start=YYYY-MM-DD&end=YYYY-MM-DD&symbol=BTC|ETH|SOL|DOGE|XRP|ADA
  - 컷오프 정책 적용된 종료일로 데이터셋 반환
  - 캐시 CSV 최신성이 부족하면 자동 재빌드
- GET /download?start=...&end=...&symbol=...
  - 즉시 재빌드 후 CSV 다운로드

실행 방법
1) 의존성 설치(예)
   pip install fastapi uvicorn pandas ccxt pyupbit requests python-dateutil
2) 서버 실행
   python /Users/chan/Desktop/graduate/1-1/Project_1/backend/main.py
3) 확인
   curl http://localhost:8000/health

동작 요약
- 09:30 이전: 오늘 데이터는 시도하지 않고 어제까지 반환
- 09:30 이후: 오늘 데이터 시도. 일부 소스 지연 시 당일 행이 누락될 수 있으며, 곧 재요청 시 채워짐


