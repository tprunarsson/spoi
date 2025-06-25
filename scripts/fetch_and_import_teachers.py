import urllib3
import requests
import json
from spoi.db.models import Person, CourseOffering, CourseOfferingTeacher
from spoi.db.session import SessionLocal
import sys
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_teachers_from_api(base_url, year, course_code):
    try:
        resp = requests.get(
            f"{base_url}?request=teacherbycid&year={year}&cid={course_code}",
            verify=False)
        data = resp.json().get('data', {})
        # Fix: If data is a list, return empty dict
        if isinstance(data, list):
            return {}
        return data.get(str(year), {}).get(course_code, {})
    except Exception as e:
        print(f"Error fetching teachers for {course_code} in {year}: {e}")
        return {}

def fetch_and_import_teachers(year, batch_size=50):
    session = SessionLocal()
    base_url = "https://localhost:8443/service/toflugerd/"
    all_offerings = session.query(CourseOffering).filter(CourseOffering.academicYear == str(year)).all()
    print(f"Found {len(all_offerings)} course offerings for year {year}")

    counter = 0
    total_links = 0
    for idx, offering in enumerate(all_offerings, 1):
        course_code = offering.courseCode
        teachers = get_teachers_from_api(base_url, year, course_code)

        # Fallback: If no teachers for this year, try previous year
        if not teachers:
            prev_year = str(int(year)-1)
            teachers = get_teachers_from_api(base_url, prev_year, course_code)
            if teachers:
                print(f"  No teachers for {course_code} in {year}, using {prev_year} instead.")

        if not teachers:
            continue

        for person_id, info in teachers.items():
            try:
                r2 = requests.get(f"{base_url}?request=namebykt&kt={person_id}", verify=False)
                r2_json = r2.json().get('data', {})
                person_name = list(r2_json.values())[0] if r2_json else "Óþekkt"

                # Upsert Person
                person = session.query(Person).filter_by(personId=person_id).first()
                if not person:
                    person = Person(
                        personId=person_id,
                        name=json.dumps({'is': person_name, 'en': person_name}),
                        role='teacher'
                    )
                    session.add(person)
                # Upsert link
                link = session.query(CourseOfferingTeacher).filter_by(
                    courseOfferingId=offering.courseOfferingId,
                    personId=person_id,
                    role=info.get('hlutverk_text')
                ).first()
                if not link:
                    link = CourseOfferingTeacher(
                        courseOfferingId=offering.courseOfferingId,
                        personId=person_id,
                        role=info.get('hlutverk_text')
                    )
                    session.add(link)
                total_links += 1
            except Exception as e:
                print(f"  Error processing teacher {person_id} for {course_code}: {e}")

        counter += 1
        # Commit every `batch_size` course offerings
        if counter % batch_size == 0:
            try:
                session.commit()
                print(f"Committed {counter} course offerings, {total_links} teacher links so far.")
            except Exception as e:
                print(f"[ERROR] Commit failed at batch {counter}: {e}")
                session.rollback()

    # Final commit for any remainder
    try:
        session.commit()
        print(f"Final commit: processed {counter} course offerings, {total_links} teacher links total.")
    except Exception as e:
        print(f"[ERROR] Final commit failed: {e}")
        session.rollback()
    session.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        year = sys.argv[1]
    else:
        year = str(datetime.now().year)
    print(f"Fetching and importing teachers for year {year} ...")
    fetch_and_import_teachers(year, batch_size=50)
    print("Done.")
