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



추가 가이드 (Step by Step)
1) 의존성 설치

   ```bash
   pip install fastapi uvicorn pandas ccxt pyupbit requests python-dateutil
   ```

2) 환경 변수 설정(backend/.env)

   ```bash
   CMC_API_KEY=YOUR_CMC_KEY
   FIXER_API_KEY=YOUR_FIXER_KEY
   ```

3) 서버 실행

   ```bash
   python /Users/chan/Desktop/graduate/1-1/Project_1/backend/main.py
   ```

4) 최초 백필(심볼별 1회)

   ```bash
   curl -X POST http://localhost:8000/backfill/2020/BTC
   curl -X POST http://localhost:8000/backfill/2020/ETH
   curl -X POST http://localhost:8000/backfill/2020/SOL
   curl -X POST http://localhost:8000/backfill/2020/DOGE
   curl -X POST http://localhost:8000/backfill/2020/XRP
   curl -X POST http://localhost:8000/backfill/2020/ADA
   ```

5) 데이터 조회 예시(09:30 KST 컷오프 적용)

   ```bash
   curl "http://localhost:8000/dataset?start=2020-01-01&end=$(date +%F)&symbol=BTC"
   ```

6) 다운로드(캐시 보존, 임시 파일 응답)

   ```bash
   curl -OJ "http://localhost:8000/download?start=2020-01-01&end=$(date +%F)&symbol=BTC"
   ```

엔드포인트 요약
- GET /health: 서버 상태
- GET /btc_dominance: BTC dominance (1시간 내 캐시)
- GET /dataset?start&end&symbol: 심볼별 시작일로 start 클램프, 09:30 컷오프로 end 클램프, 증분 보충 반환
- GET /download?start&end&symbol: 캐시 보존, 요청 범위만 다운로드
- GET /realtime/{symbol}: 현재가 기반 실시간 김프(표시용)
- POST /backfill/2020/{symbol}: 심볼 시작일~컷오프까지 보장(증분)

캐시/증분 갱신 동작
- USD/KRW(backend/data/usdkrw_daily.csv)
  - 캐시 마지막일+1 ~ 오늘: Fixer API로 1차 채움(USD/KRW = KRW/EUR ÷ USD/EUR)
  - Fixer 실패/누락은 기존 스크래퍼 폴백
  - Fixer가 공휴일 기준일을 반환하면 해당 요청일을 usd_ffill=True로 기록
  - 오늘 행이 이미 있으면 재호출 안 함(증분 원칙)
- 심볼 CSV(backend/data/kimchi_premium_daily_{SYMBOL}.csv)
  - 뒤쪽 결손만 append, 앞쪽 결손은 prepend, 내부 소규모 갭(≤7일) 자동 보충
  - 저장은 원자적 저장(임시 파일→교체)

테스트 팁
- 최신부 N행 삭제 → 다음 /dataset 호출 시 자동 보충(권장)
- 내부 대규모 구간 삭제는 자동 복구 대상 아님 → 파일 삭제 후 /backfill 권장
- Fixer 동작 확인: usdkrw_daily.csv의 최신부 N행 삭제 후 /dataset 요청으로 재생성 확인
