from bs4 import BeautifulSoup
import pandas as pd

# 1. Open and read the KML file
print("Reading the KML file...")
with open('tmr-traffic-census-2020.kml', 'r', encoding='utf-8') as file:
    kml_content = file.read()

# 2. Parse the XML/KML content
print("Parsing data...")
soup = BeautifulSoup(kml_content, 'xml')

# 3. Find all placemarks (each traffic point)
placemarks = soup.find_all('Placemark')

data = []

# 4. Extract SITE_ID and REPORT_LINK for each point
for placemark in placemarks:
    site_id_tag = placemark.find('SimpleData', {'name': 'SITE_ID'})
    report_link_tag = placemark.find('SimpleData', {'name': 'REPORT_LINK'})
    
    if site_id_tag and report_link_tag:
        site_id = site_id_tag.text
        pdf_url = report_link_tag.text
        data.append({'SITE_ID': site_id, 'PDF_URL': pdf_url})

# 5. Convert to a pandas DataFrame and save as Excel
df = pd.DataFrame(data)
output_filename = 'traffic_pdf_links.xlsx'
df.to_excel(output_filename, index=False)

print(f"Success! {len(data)} data points have been saved to {output_filename}")