import urllib3
import requests
import json
from spoi.db.models import Room
from spoi.db.session import SessionLocal

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def fetch_room_data():
    url = "https://localhost:8443/service/toflugerd/?request=ridinfo"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json()

def import_rooms(data):
    session = SessionLocal()
    building_data = data['data']
    count = 0
    for building_id, rooms in building_data.items():
        for room_id, room in rooms.items():
            if not room_id or not str(room_id).isdigit():
                print(f"[SKIP] Room with bad id: {room_id}")
                continue
            count += 1
            # Optionally handle None values for other integer columns:
            def safe_int(val):
                try:
                    return int(val)
                except (TypeError, ValueError):
                    return None

            room_obj = Room(
                roomId=safe_int(room_id),  # Ensures correct type
                name=room.get('name'),
                type=str(room.get('type')) if room.get('type') is not None else None,
                typeName=room.get('type_name'),
                capacity=safe_int(room.get('capacity')),
                examCapacity=safe_int(room.get('examseats')),
                examSpecialCapacity=safe_int(room.get('examseatsspecial')),
                buildingId=safe_int(building_id),
            )
            properties = {
                'blackboardcount': room.get('blackboardcount'),
                'incline': room.get('incline'),
                'homeprogram': room.get('homeprogram'),
                'computer': room.get('computer'),
                'group': room.get('group'),
                'distance': room.get('distance'),
                'priority': room.get('priority'),
            }
            room_obj.set_properties(properties)
            session.merge(room_obj)
    session.commit()
    session.close()
    print(f"Rooms imported in total: {count}.")

if __name__ == "__main__":
    data = fetch_room_data()
    import_rooms(data)
