import requests
import json

url = "https://portal.data.nsw.gov.au/arcgis/rest/services/Hosted/TfNSW_Traffic_Volume_Counts_v2_Public/FeatureServer/0/query"
params = {
    'where': '1=1',
    'outFields': '*',
    'f': 'geojson',
    'resultRecordCount': 1000,
    'resultOffset': 0
}

all_features = []
while True:
    response = requests.get(url, params=params).json()
    features = response.get('features', [])
    if not features:
        break
    all_features.extend(features)
    print(f"Downloaded {len(all_features)} records...")
    params['resultOffset'] += 1000
    
    # Check if we reached the end
    if not response.get('exceededTransferLimit'):
        break

with open('NSW_Full_Database.geojson', 'w') as f:
    json.dump({"type": "FeatureCollection", "features": all_features}, f)