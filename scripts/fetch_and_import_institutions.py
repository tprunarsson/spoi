import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import requests
from spoi.db.models import Institution
from spoi.db.session import SessionLocal

def fetch_school_data(year=2025):
    url = f"https://localhost:8443/service/toflugerd/?request=scidinfo&year={year}"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json()

def import_institutions(data, year=2025):
    session = SessionLocal()
    year_data = data['data'][str(year)]
    for institutionId, school_data in year_data.items():
        name_en = school_data.get('nameeng', '')
        name_is = school_data.get('nameice', '')
        institution = Institution(
            institutionId=institutionId
        )
        institution.set_names(name_en, name_is)
        session.merge(institution)
    session.commit()
    session.close()
    print("Institutions imported.")

if __name__ == "__main__":
    data = fetch_school_data()
    import_institutions(data)
