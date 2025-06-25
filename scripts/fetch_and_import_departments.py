import requests
import urllib3
import json
from spoi.db.models import Department, Institution
from spoi.db.session import SessionLocal

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def fetch_departments(year=2025):
    url = f"https://localhost:8443/service/toflugerd/?request=didinfo&year={year}"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json()

def import_departments(data, year=2025):
    session = SessionLocal()
    year_data = data['data'][str(year)]
    inserted, updated = 0, 0

    for faculty_id, departments in year_data.items():
        # Ensure institution exists
        institution = session.query(Institution).filter_by(institutionId=faculty_id).first()
        if not institution:
            institution = Institution(
                institutionId=faculty_id,
                name=json.dumps({'en': f'Institution {faculty_id}', 'is': f'Stofnum {faculty_id}'})
            )
            session.add(institution)

        for departmentId, info in departments.items():
            name_en = info.get('nameeng', '')
            name_is = info.get('nameice', '')
            new_name = json.dumps({'en': name_en, 'is': name_is})

            dept = session.query(Department).filter_by(departmentId=departmentId).first()
            if not dept:
                dept = Department(
                    departmentId=departmentId,
                    name=new_name,
                    institutionId=faculty_id
                )
                session.add(dept)
                inserted += 1
            else:
                changed = False
                if dept.name != new_name:
                    dept.name = new_name
                    changed = True
                if dept.institutionId != faculty_id:
                    dept.institutionId = faculty_id
                    changed = True
                if changed:
                    updated += 1

    session.commit()
    session.close()
    print(f"Departments imported: {inserted} inserted, {updated} updated.")

if __name__ == "__main__":
    year = 2025  # Change this to the desired year
    data = fetch_departments(year)
    import_departments(data, year)
