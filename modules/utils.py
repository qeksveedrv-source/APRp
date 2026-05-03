import math
from datetime import datetime

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # 地球半徑 (km)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def format_date(roc_date_str):
    try:
        s = str(int(float(roc_date_str)))
        year = int(s[:-4]) + 1911
        return f"{year}-{s[-4:-2]}-{s[-2:]}"
    except:
        return "無資料"

def calculate_age_from_roc(roc_date_str):
    try:
        s = str(int(float(roc_date_str)))
        build_year = int(s[:-4])
        current_roc_year = datetime.now().year - 1911
        return max(current_roc_year - build_year, 0)
    except:
        return None
