import urllib3
import requests
from spoi.db.models import Building
from spoi.db.session import SessionLocal

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def fetch_building_data():
    url = "https://localhost:8443/service/toflugerd/?request=bidinfo"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json()

def import_buildings(data):
    session = SessionLocal()
    building_data = data['data']
    for building_id, binfo in building_data.items():
        # Clean the ID to int (fail gracefully)
        try:
            bid = int(building_id)
        except (ValueError, TypeError):
            print(f"[SKIP] Bad building id: {building_id}")
            continue
        building = Building(
            buildingId=bid,
            name=binfo.get("name", None)
        )
        session.merge(building)
    session.commit()
    session.close()
    print("Buildings imported.")

if __name__ == "__main__":
    data = fetch_building_data()
    import_buildings(data)
