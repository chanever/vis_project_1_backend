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

def get_usd_rates_df(start_date: str, end_date: str) -> pd.DataFrame:
    """
    특정 기간(start_date ~ end_date) 동안의 KRW/USD 환율을 스크래핑해서 DataFrame으로 반환.
    - 주말/휴일: 직전 평일 값 유지 (usd_ffill=True로 표시)
    - 시작일이 주말/휴일이면: 다음 개장일 값으로 채움
    - 잘못된 날짜 입력 시: 명확한 에러 메시지 출력
    반환 컬럼: [date, usd_rate, usd_ffill]
    """
    base_url = "http://www.smbs.biz/Flash/TodayExRate_flash.jsp?tr_date={}"
    
    # ✅ 날짜 유효성 검사
    start = validate_date(start_date)
    end = validate_date(end_date)
    
    if start > end:
        raise ValueError(f"❌ 시작일({start})이 종료일({end})보다 이후일 수 없습니다.")
    
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
            
            # 1) 오류 페이지 처리
            if "오류가 발생하였습니다" in text:
                print(f"[ERROR] {current} : 잘못된 요청")
                current += delta
                continue
            
            # 2) 주말/휴일 처리
            if "USD=" not in text:
                pending_dates.append(current)
                current += delta
                continue
            
            # 3) USD 값 추출
            match = re.search(r"USD=([\d,]+\.\d+)", text)
            if match:
                rate = float(match.group(1).replace(",", ""))
                last_rate = rate
                # pending 채우기 (ffill=True)
                for pd_date in pending_dates:
                    data.append([pd_date, rate, True])
                pending_dates = []
                # 현재 날짜 (직접 값, ffill=False)
                data.append([current, rate, False])
            else:
                print(f"[WARN] {current} : USD 환율을 찾을 수 없음")
            
        except Exception as e:
            print(f"[EXCEPTION] {current} : {e}")
        
        current += delta
    
    # 마지막까지 pending 남아있으면 채움 (ffill=True)
    if pending_dates and last_rate is not None:
        for pd_date in pending_dates:
            data.append([pd_date, last_rate, True])
    
    df = pd.DataFrame(data, columns=["date", "usd_rate", "usd_ffill"])
    df["date"] = pd.to_datetime(df["date"])  # naive date
    df = df.sort_values("date").reset_index(drop=True)
    return df

# 테스트 실행 제거 (모듈 import 시 출력 방지)
